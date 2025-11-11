"""Unit tests for the billings service.

This module contains unit tests for the billings service, including database operations,
message processing, and integration with external services like Stripe and insurance verification.
"""

import json

import pytest

import app as billings_app


# ---- Dummy MySQL objects for unit tests (no real DB) ----
class DummyCursor:
    """Mock database cursor for unit testing without a real database connection.

    This class simulates a database cursor with basic functionality needed for testing.
    It tracks executed queries and can be configured to return predefined results.
    """

    def __init__(self, dictionary=False):
        """Initialize the dummy cursor.

        Args:
            dictionary (bool): If True, return rows as dictionaries instead of tuples.
        """
        self.dictionary = dictionary
        self._lastrowid = 101
        self._executed = []

    @property
    def lastrowid(self):
        """Get the last inserted row id.

        Returns:
            int: The last inserted row id.
        """
        return self._lastrowid

    def execute(self, sql, params=None):
        """Store the executed SQL query and parameters for later inspection.

        Args:
            sql (str): The SQL query to execute.
            params (tuple, optional): Parameters for the SQL query.
        """
        self._executed.append((sql, params))

    def fetchone(self):
        """Return a mock database row.

        Returns:
            dict or tuple: A single row of mock data, format depends on dictionary flag.
        """
        # Return a row for "SELECT amount ..." paths if needed
        if self.dictionary:
            return {"amount": 100.00}
        return (1,)

    def close(self):
        """Close the cursor.

        This is a no-op in the mock implementation but is included for interface compatibility.
        """
        pass


class DummyConn:
    """Mock database connection for unit testing.

    This class simulates a database connection with basic cursor creation
    and connection management for testing purposes.
    """

    def __init__(self):
        """Initialize the dummy database connection."""
        self._open = True
        self._cursor = DummyCursor()

    def cursor(self, dictionary=False, buffered=False):
        """Create and return a new mock cursor.

        Args:
            dictionary (bool): If True, cursor returns rows as dictionaries.
            buffered (bool): Ignored in this mock implementation.

        Returns:
            DummyCursor: A new mock cursor instance.
        """
        return DummyCursor(dictionary=dictionary)

    def commit(self):
        """Commit the current transaction. No-op in this mock implementation."""

    def is_connected(self):
        """Check if the connection is active.

        Returns:
            bool: True if the connection is open, False otherwise.
        """
        return self._open

    def close(self):
        """Close the database connection.

        Sets the connection state to closed, which will cause is_connected() to return False.
        """
        self._open = False


@pytest.fixture
def fake_mysql_connect(monkeypatch):
    """Fixture to replace mysql.connector.connect with a mock implementation.

    Args:
        monkeypatch: Pytest fixture for safely patching modules and functions.

    Returns:
        function: The mock connection factory function.
    """

    def _fake_connect(**kwargs):
        return DummyConn()

    import mysql
    import mysql.connector

    monkeypatch.setattr(mysql.connector, "connect", _fake_connect)
    return _fake_connect


@pytest.fixture
def capture_publish(monkeypatch):
    """Fixture to capture calls to the AMQP publish_status_update function.

    Args:
        monkeypatch: Pytest fixture for safely patching modules and functions.

    Returns:
        dict: Dictionary containing captured publish calls.
    """
    published = {"calls": []}

    def _fake_publish_status_update(message, is_success=True):
        # message may be dict or str in your code; normalize to dict
        if isinstance(message, str):
            try:
                message = json.loads(message)
            except Exception:
                message = {"_raw": message}
        published["calls"].append((message, is_success))
        return True

    monkeypatch.setattr(
        billings_app.amqp, "publish_status_update", _fake_publish_status_update
    )
    return published


def _initiate_msg(incident_id="inc-u-1", patient_id="P123", amount=100):
    """Create a test message in the format expected by the billing service.

    Args:
        incident_id (str): Unique identifier for the incident.
        patient_id (str): Identifier for the patient.
        amount (float): Billing amount.

    Returns:
        bytes: JSON-encoded message as bytes.
    """
    return json.dumps(
        {"incident_id": incident_id, "patient_id": patient_id, "amount": amount}
    ).encode("utf-8")


def test_callback_success_paid(
    fake_mysql_connect, capture_publish, fake_stripe_module, monkeypatch
):
    """Test successful billing callback with insurance verification and payment.

    Verifies that a valid billing request with proper insurance coverage
    and successful payment processing results in a COMPLETED status.

    Args:
        fake_mysql_connect: Fixture providing a mock database connection.
        capture_publish: Fixture to capture AMQP publish events.
        fake_stripe_module: Fixture providing a mock Stripe module.
        monkeypatch: Pytest fixture for patching functions.
    """
    # Insurance OK
    monkeypatch.setattr(
        billings_app,
        "verify_insurance",
        lambda incident_id, patient_id, amount=None: {
            "verified": True,
            "reason": "OK",
            "message": "ok",
            "http_status": 200,
        },
    )

    # Stripe returns success via fake module (already provided in conftest)
    fake_stripe_module.process_stripe_payment = lambda **kw: {
        "success": True,
        "payment_intent_id": "pi_test_OK",
        "client_secret": "cs",
    }

    # Call the callback directly
    billings_app.callback(ch=None, method=None, properties=None, body=_initiate_msg())

    # One publish, to event.billing.completed
    assert len(capture_publish["calls"]) == 1
    msg, is_success = capture_publish["calls"][0]
    assert is_success is True
    assert msg["status"] == "COMPLETED"
    assert msg["amount"] == 100


def test_callback_insurance_no_policy(
    fake_mysql_connect, capture_publish, fake_stripe_module, monkeypatch
):
    """Test billing callback when no insurance policy is found.

    Verifies that the service correctly handles the case where a patient
    does not have an active insurance policy.

    Args:
        fake_mysql_connect: Fixture providing a mock database connection.
        capture_publish: Fixture to capture AMQP publish events.
        fake_stripe_module: Fixture providing a mock Stripe module.
        monkeypatch: Pytest fixture for patching functions.
    """
    # Insurance fails with NO_POLICY
    monkeypatch.setattr(
        billings_app,
        "verify_insurance",
        lambda incident_id, patient_id, amount=None: {
            "verified": False,
            "reason": "NO_POLICY",
            "message": "not found",
            "http_status": 404,
        },
    )

    billings_app.callback(ch=None, method=None, properties=None, body=_initiate_msg())

    assert len(capture_publish["calls"]) == 1
    msg, is_success = capture_publish["calls"][0]
    assert is_success is False
    assert msg["status"] == "CANCELLED"


def test_callback_payment_declined(
    fake_mysql_connect, capture_publish, fake_stripe_module, monkeypatch
):
    """Test billing callback when payment is declined by the payment processor.

    Verifies that the service correctly handles payment failures and reports
    the appropriate status.

    Args:
        fake_mysql_connect: Fixture providing a mock database connection.
        capture_publish: Fixture to capture AMQP publish events.
        fake_stripe_module: Fixture providing a mock Stripe module.
        monkeypatch: Pytest fixture for patching functions.
    """
    # Insurance OK
    monkeypatch.setattr(
        billings_app,
        "verify_insurance",
        lambda incident_id, patient_id, amount=None: {
            "verified": True,
            "reason": "OK",
            "message": "ok",
            "http_status": 200,
        },
    )

    # Stripe fails like "card was declined"
    def _decline(**kw):
        return {
            "success": False,
            "error": "Your card was declined",
            "payment_intent_id": None,
            "client_secret": None,
        }

    fake_stripe_module.process_stripe_payment = _decline

    billings_app.callback(ch=None, method=None, properties=None, body=_initiate_msg())

    assert len(capture_publish["calls"]) == 1
    msg, is_success = capture_publish["calls"][0]
    assert is_success is False
    # The actual implementation uses CANCELLED status for all failures
    assert msg["status"] == "CANCELLED"
