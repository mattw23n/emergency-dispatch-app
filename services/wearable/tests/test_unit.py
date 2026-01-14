import pytest
import json
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.app import WearablePublisher
from src.amqp_setup import amqp_setup


class TestWearableEndpoints:
    """Unit tests for wearable service HTTP endpoints."""

    def test_health_endpoint_with_connected_publisher(
        self, client, reset_global_publisher
    ):
        """Test health endpoint when publisher is connected."""
        import src.app

        # Create a mock publisher instance
        mock_publisher = Mock()
        mock_publisher.is_connected = True
        mock_publisher.scenario = "normal"
        mock_publisher.messages_sent = 10
        src.app.publisher_instance = mock_publisher

        response = client.get("/health")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert data["rabbitmq_connected"] is True
        assert data["scenario"] == "normal"
        assert data["messages_sent"] == 10

    def test_health_endpoint_with_disconnected_publisher(
        self, client, reset_global_publisher
    ):
        """Test health endpoint when publisher is disconnected."""
        import src.app

        mock_publisher = Mock()
        mock_publisher.is_connected = False
        mock_publisher.scenario = "normal"
        mock_publisher.messages_sent = 5
        src.app.publisher_instance = mock_publisher

        response = client.get("/health")

        assert response.status_code == 503
        data = response.get_json()
        assert data["status"] == "degraded"
        assert data["rabbitmq_connected"] is False

    def test_health_endpoint_no_publisher(self, client, reset_global_publisher):
        """Test health endpoint when no publisher is initialized."""
        response = client.get("/health")

        assert response.status_code == 500
        data = response.get_json()
        assert data["status"] == "error"
        assert "not initialized" in data["message"]

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

    def test_get_scenario_endpoint(self, client, reset_global_publisher):
        """Test GET scenario endpoint."""
        import src.app

        mock_publisher = Mock()
        mock_publisher.scenario = "abnormal"
        src.app.publisher_instance = mock_publisher

        response = client.get("/scenario")

        assert response.status_code == 200
        data = response.get_json()
        assert data["scenario"] == "abnormal"

    def test_get_scenario_no_publisher(self, client, reset_global_publisher):
        """Test GET scenario endpoint when no publisher exists."""
        response = client.get("/scenario")

        assert response.status_code == 500
        data = response.get_json()
        assert "not initialized" in data["error"]

    def test_change_scenario_valid(self, client, reset_global_publisher):
        """Test PUT scenario endpoint with valid scenario."""
        import src.app

        mock_publisher = Mock()
        mock_publisher.scenario = "normal"
        src.app.publisher_instance = mock_publisher

        response = client.put(
            "/scenario",
            data=json.dumps({"scenario": "emergency"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["scenario"] == "emergency"
        assert "changed to emergency" in data["message"]

    def test_change_scenario_invalid(self, client, reset_global_publisher):
        """Test PUT scenario endpoint with invalid scenario."""
        import src.app

        mock_publisher = Mock()
        src.app.publisher_instance = mock_publisher

        response = client.put(
            "/scenario",
            data=json.dumps({"scenario": "invalid"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "Invalid scenario" in data["error"]

    def test_change_scenario_no_publisher(self, client, reset_global_publisher):
        """Test PUT scenario endpoint when no publisher exists."""
        response = client.put(
            "/scenario",
            data=json.dumps({"scenario": "normal"}),
            content_type="application/json",
        )

        assert response.status_code == 500
        data = response.get_json()
        assert "not initialized" in data["error"]


class TestWearablePublisher:
    """Unit tests for WearablePublisher class."""

    def test_publisher_initialization_valid_scenario(self):
        """Test publisher initialization with valid scenarios."""
        for scenario in ["normal", "abnormal", "emergency"]:
            publisher = WearablePublisher(scenario)
            assert publisher.scenario == scenario
            assert publisher.messages_sent == 0
            assert publisher.is_running is True
            assert publisher.is_connected is False

    def test_publisher_initialization_invalid_scenario(self):
        """Test publisher initialization with invalid scenario."""
        with pytest.raises(ValueError, match="Scenario must be"):
            WearablePublisher("invalid")

    def test_generate_normal_metrics(self):
        """Test normal metrics generation."""
        publisher = WearablePublisher("normal")
        metrics = publisher._generate_normal_metrics()

        assert 50 <= metrics["heartRateBpm"] <= 100
        assert 95.0 <= metrics["spO2Percentage"] <= 99.5
        assert 10 <= metrics["respirationRateBpm"] <= 24
        assert 36.0 <= metrics["bodyTemperatureCelsius"] <= 37.5
        assert 0 <= metrics["stepsSinceLastReading"] <= 30

    def test_generate_abnormal_metrics(self):
        """Test abnormal metrics generation."""
        publisher = WearablePublisher("abnormal")
        metrics = publisher._generate_abnormal_metrics()

        assert (101 <= metrics["heartRateBpm"] <= 150) or (
            40 <= metrics["heartRateBpm"] <= 49
        )
        assert 91.0 <= metrics["spO2Percentage"] <= 94.9
        assert (25 <= metrics["respirationRateBpm"] <= 30) or (
            8 <= metrics["respirationRateBpm"] <= 9
        )
        assert (37.6 <= metrics["bodyTemperatureCelsius"] <= 39.0) or (
            35.1 <= metrics["bodyTemperatureCelsius"] <= 35.9
        )
        assert 0 <= metrics["stepsSinceLastReading"] <= 10

    def test_generate_emergency_metrics(self):
        """Test emergency metrics generation."""
        publisher = WearablePublisher("emergency")
        metrics = publisher._generate_emergency_metrics()

        assert (151 <= metrics["heartRateBpm"] <= 190) or (
            20 <= metrics["heartRateBpm"] <= 39
        )
        assert 80.0 <= metrics["spO2Percentage"] <= 90.9
        assert (31 <= metrics["respirationRateBpm"] <= 40) or (
            4 <= metrics["respirationRateBpm"] <= 7
        )
        assert (39.1 <= metrics["bodyTemperatureCelsius"] <= 42.0) or (
            32.0 <= metrics["bodyTemperatureCelsius"] <= 34.9
        )
        assert metrics["stepsSinceLastReading"] == 0

    def test_generate_base_payload(self):
        """Test base payload generation."""
        publisher = WearablePublisher("normal")
        payload = publisher._generate_base_payload()

        assert payload["patient_id"] == "P123"
        assert payload["device"]["id"] == "wearable-1"
        assert payload["device"]["model"] == "HealthTracker v1"
        assert "location" in payload
        assert payload["schemaVersion"] == "1.0"

    @patch("src.app.amqp_setup")
    def test_publisher_stop(self, mock_amqp):
        """Test publisher stop functionality."""
        publisher = WearablePublisher("normal")
        publisher.stop()

        assert publisher.is_running is False
        mock_amqp.close.assert_called_once()


class TestAMQPSetup:
    """Unit tests for AMQP setup."""

    def test_amqp_initialization(self):
        """Test AMQP setup initialization."""
        amqp = amqp_setup
        assert amqp.RK_WEARABLE_DATA == "wearable.data"
        assert amqp.hostname == "rabbitmq"  # default value
        assert amqp.port == 5672  # default value

    @patch("pika.BlockingConnection")
    def test_amqp_connect_success(self, mock_connection):
        """Test successful AMQP connection."""
        mock_conn_instance = Mock()
        mock_channel = Mock()
        mock_conn_instance.channel.return_value = mock_channel
        mock_connection.return_value = mock_conn_instance

        amqp = amqp_setup
        amqp.connect(max_retry_time=1)

        assert amqp.connection == mock_conn_instance
        assert amqp.channel == mock_channel

    def test_publish_wearable_data(self):
        """Test wearable data publishing."""
        with patch.object(amqp_setup, "publish") as mock_publish:
            test_payload = {"test": "data"}
            amqp_setup.publish_wearable_data(test_payload)

            mock_publish.assert_called_once_with("wearable.data", test_payload)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
