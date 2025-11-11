"""Unit tests for the billings service."""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import app

# Test data
TEST_BILLING_ID = "b123"
TEST_INCIDENT_ID = "inc123"
TEST_PATIENT_ID = "p456"
TEST_PAYMENT_REF = "pi_test123"
TEST_AMOUNT = 1000


class TestProcessPayment:
    """Tests for the process_payment function."""

    @patch("app.stripe_service")
    def test_process_payment_success(self, mock_stripe, billings_app_module):
        """Test successful payment processing."""
        mock_stripe.process_stripe_payment.return_value = {
            "success": True,
            "payment_intent_id": TEST_PAYMENT_REF,
            "error": None,
        }

        result = app.process_payment(
            patient_id=TEST_PATIENT_ID, amount=TEST_AMOUNT, description="Test payment"
        )

        assert result["success"] is True
        assert result["payment_intent_id"] == TEST_PAYMENT_REF
        assert result["error"] is None
        mock_stripe.process_stripe_payment.assert_called_once_with(
            amount=TEST_AMOUNT / 100,
            description="Test payment",
        )

    @patch("app.stripe_service")
    def test_process_payment_failure(self, mock_stripe, billings_app_module):
        """Test payment processing with Stripe error."""
        mock_stripe.process_stripe_payment.return_value = {
            "success": False,
            "payment_intent_id": None,
            "error": "Card was declined",
        }

        result = app.process_payment(
            patient_id=TEST_PATIENT_ID, amount=TEST_AMOUNT, description="Test payment"
        )

        assert result["success"] is False
        assert result.get("payment_intent_id") is None
        assert result.get("error") is not None


class TestVerifyInsurance:
    """Tests for the verify_insurance function."""

    @patch("requests.post")
    def test_verify_insurance_success(self, mock_post, billings_app_module):
        """Test successful insurance verification."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "verified": True,
            "reason": "OK",
            "message": "Insurance verified",
            "coverage_amount": 10000,
        }

        # Patch the environment to use a test URL
        with patch.dict(
            "os.environ",
            {"insurance_service_url_internal": "http://test-insurance:5200"},
        ):
            result = app.verify_insurance(
                incident_id=TEST_INCIDENT_ID,
                patient_id=TEST_PATIENT_ID,
                amount=TEST_AMOUNT,
            )

        # Verify results
        assert result["verified"] is True
        assert result["reason"] == "OK"
        assert "verified" in result["message"].lower()

        # Verify the request was made correctly
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"] == {
            "patient_id": TEST_PATIENT_ID,
            "incident_id": TEST_INCIDENT_ID,
            "amount": TEST_AMOUNT,
        }

    @patch("requests.post")  # Patch requests directly
    def test_verify_insurance_insufficient_coverage(
        self, mock_post, billings_app_module
    ):
        """Test insurance verification with insufficient coverage."""
        # Setup mock response for insufficient coverage
        mock_post.return_value.status_code = 402
        mock_post.return_value.json.return_value = {
            "verified": False,
            "reason": "INSUFFICIENT_COVERAGE",
            "message": "Insufficient coverage",
            "coverage_amount": 100,
        }

        # Patch the environment to use a test URL
        with patch.dict(
            "os.environ",
            {"insurance_service_url_internal": "http://test-insurance:5200"},
        ):
            result = app.verify_insurance(
                incident_id=TEST_INCIDENT_ID,
                patient_id=TEST_PATIENT_ID,
                amount=TEST_AMOUNT,
            )

        # Verify results
        assert result["verified"] is False
        assert result["reason"] == "INSUFFICIENT_COVERAGE"


class TestUpdateBillingStatus:
    """Tests for the update_billing_status function."""

    @patch("app.mysql.connector")
    def test_update_billing_status_success(self, mock_mysql, billings_app_module):
        """Test successful billing status update."""
        # Setup mock database connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_mysql.connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.rowcount = 1

        # Call the function
        result = app.update_billing_status(
            id=TEST_BILLING_ID,
            insurance_verified=True,
            payment_reference=TEST_PAYMENT_REF,
            status="COMPLETED",
        )

        # Verify results
        assert result is True
        mock_cursor.execute.assert_called_once_with(
            """
            UPDATE billings
            SET status = %s,
                insurance_verified = %s,
                payment_reference = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            ("COMPLETED", True, TEST_PAYMENT_REF, TEST_BILLING_ID),
        )
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()


class TestCompensatePayment:
    """Tests for the compensate_payment function."""

    @patch("app.stripe_service.refund_payment")
    @patch("app.mysql.connector")
    def test_compensate_payment_success(
        self, mock_mysql, mock_refund, billings_app_module
    ):
        """Test successful payment compensation."""
        # Setup Stripe mock
        mock_refund.return_value = {"success": True, "refund_id": "re_123"}

        # Setup database mocks
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_mysql.connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.rowcount = 1
        mock_conn.is_connected.return_value = True

        # Call the function
        result = app.compensate_payment(TEST_BILLING_ID, TEST_PAYMENT_REF)

        # Verify the result
        assert result is True

        # Verify stripe refund was called
        mock_refund.assert_called_once_with(TEST_PAYMENT_REF)

        # Verify database operations happened
        mock_cursor.execute.assert_called()  # At least one execute
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()
