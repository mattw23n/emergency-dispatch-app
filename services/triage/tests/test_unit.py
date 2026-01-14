import pytest
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.app import determine_triage_status, service_state


class TestTriageEndpoints:
    """Unit tests for triage service HTTP endpoints."""

    def test_health_endpoint_connected(self, client, reset_service_state):
        """Test health endpoint when service is connected."""
        service_state["is_connected"] = True
        service_state["messages_processed"] = 10
        service_state["emergencies_detected"] = 2
        service_state["abnormalities_detected"] = 3

        response = client.get("/health")

        assert response.status_code == 200
        data = response.get_json()
        assert data["is_connected"] is True
        assert data["messages_processed"] == 10
        assert data["emergencies_detected"] == 2
        assert data["abnormalities_detected"] == 3

    def test_health_endpoint_disconnected(self, client, reset_service_state):
        """Test health endpoint when service is disconnected."""
        service_state["is_connected"] = False

        response = client.get("/health")

        assert response.status_code == 503
        data = response.get_json()
        assert data["is_connected"] is False

    @patch("src.app.amqp_setup")
    def test_status_endpoint_connected(self, mock_amqp, client):
        """Test status endpoint when AMQP is connected."""
        mock_amqp.connection = Mock()
        mock_amqp.connection.is_open = True

        response = client.get("/status")

        assert response.status_code == 200
        data = response.get_json()
        assert data["amqp_connected"] is True

    @patch("src.app.amqp_setup")
    def test_status_endpoint_disconnected(self, mock_amqp, client):
        """Test status endpoint when AMQP is disconnected."""
        mock_amqp.connection = None

        response = client.get("/status")

        assert response.status_code == 503
        data = response.get_json()
        assert data["amqp_connected"] is False


class TestTriageLogic:
    """Unit tests for triage decision logic."""

    def test_determine_status_normal_vitals(self):
        """Test normal vital signs."""
        metrics = {
            "heartRateBpm": 75,
            "spO2Percentage": 98.5,
            "bodyTemperatureCelsius": 37.0,
            "respirationRateBpm": 16,
        }

        status, reason = determine_triage_status(metrics)
        assert status == "Normal"
        assert "normal range" in reason.lower()

    def test_determine_status_emergency_low_spo2(self):
        """Test emergency condition - critically low SpO2."""
        metrics = {
            "heartRateBpm": 75,
            "spO2Percentage": 88.0,  # Emergency threshold
            "bodyTemperatureCelsius": 37.0,
            "respirationRateBpm": 16,
        }

        status, reason = determine_triage_status(metrics)
        assert status == "Emergency"
        assert "critically low blood oxygen" in reason.lower()

    def test_determine_status_emergency_high_heart_rate(self):
        """Test emergency condition - critically high heart rate."""
        metrics = {
            "heartRateBpm": 160,  # Emergency threshold
            "spO2Percentage": 98.0,
            "bodyTemperatureCelsius": 37.0,
            "respirationRateBpm": 16,
        }

        status, reason = determine_triage_status(metrics)
        assert status == "Emergency"
        assert "critically abnormal heart rate" in reason.lower()

    def test_determine_status_abnormal_heart_rate(self):
        """Test abnormal condition - moderately high heart rate."""
        metrics = {
            "heartRateBpm": 110,  # Abnormal but not emergency
            "spO2Percentage": 98.0,
            "bodyTemperatureCelsius": 37.0,
            "respirationRateBpm": 16,
        }

        status, reason = determine_triage_status(metrics)
        assert status == "Abnormal"
        assert "abnormal heart rate" in reason.lower()

    def test_determine_status_missing_metrics(self):
        """Test with missing metrics (should use defaults)."""
        metrics = {}  # Empty metrics

        status, reason = determine_triage_status(metrics)
        assert status == "Emergency"  # Should default to normal with default values

    def test_determine_status_priority_emergency_over_abnormal(self):
        """Test that emergency conditions take priority over abnormal."""
        metrics = {
            "heartRateBpm": 110,  # Would be abnormal
            "spO2Percentage": 88.0,  # Emergency - should take priority
            "bodyTemperatureCelsius": 37.0,
            "respirationRateBpm": 16,
        }

        status, reason = determine_triage_status(metrics)
        assert status == "Emergency"
        assert "critically low blood oxygen" in reason.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
