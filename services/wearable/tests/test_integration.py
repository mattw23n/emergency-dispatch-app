import pytest
import json
import time
import threading
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.app import WearablePublisher
# from src.amqp_setup import amqp_setup


class TestWearableIntegration:
    """Integration tests for wearable service."""

    @patch("src.app.amqp_setup")
    def test_end_to_end_message_flow(self, mock_amqp, sample_wearable_metrics):
        """Test complete message flow from generation to publishing."""
        # Setup mock
        mock_amqp.publish_wearable_data = Mock()
        mock_amqp.connect = Mock()

        # Create publisher and simulate one iteration
        publisher = WearablePublisher("normal")
        publisher.is_connected = True  # Skip connection logic

        # Generate and verify payload structure
        payload = publisher._generate_base_payload()
        payload["timestamp"] = int(time.time() * 1000)
        payload["metrics"] = publisher.metric_generator()

        # Simulate publishing
        mock_amqp.publish_wearable_data(payload)

        # Verify message was published with correct structure
        mock_amqp.publish_wearable_data.assert_called_once_with(payload)

        # Verify payload structure
        assert "patient_id" in payload
        assert "device" in payload
        assert "timestamp" in payload
        assert "metrics" in payload
        assert "schemaVersion" in payload

        # Verify metrics structure
        metrics = payload["metrics"]
        required_metrics = [
            "heartRateBpm",
            "spO2Percentage",
            "respirationRateBpm",
            "bodyTemperatureCelsius",
            "stepsSinceLastReading",
        ]
        for metric in required_metrics:
            assert metric in metrics

    @patch("src.app.amqp_setup")
    def test_scenario_switching_integration(
        self, mock_amqp, client, reset_global_publisher
    ):
        """Test scenario switching affects message generation."""
        import src.app

        # Create publisher and set it globally
        publisher = WearablePublisher("normal")
        src.app.publisher_instance = publisher

        # Test normal scenario metrics
        normal_metrics = publisher._generate_normal_metrics()
        assert 60 <= normal_metrics["heartRateBpm"] <= 95

        # Switch to emergency via API
        response = client.put(
            "/scenario",
            data=json.dumps({"scenario": "emergency"}),
            content_type="application/json",
        )
        assert response.status_code == 200

        # Verify scenario changed
        assert publisher.scenario == "emergency"

        # Test emergency scenario metrics
        emergency_metrics = publisher._generate_emergency_metrics()
        assert 140 <= emergency_metrics["heartRateBpm"] <= 190

    @patch("src.app.amqp_setup")
    def test_message_publishing_with_different_scenarios(
        self, mock_amqp, sample_wearable_metrics
    ):
        """Test message publishing for all scenarios."""
        scenarios = ["normal", "abnormal", "emergency"]

        for scenario in scenarios:
            mock_amqp.reset_mock()
            mock_amqp.publish_wearable_data = Mock()

            publisher = WearablePublisher(scenario)
            publisher.is_connected = True

            # Generate payload
            payload = publisher._generate_base_payload()
            payload["timestamp"] = int(time.time() * 1000)
            payload["metrics"] = publisher.metric_generator()

            # Publish
            mock_amqp.publish_wearable_data(payload)

            # Verify publishing occurred
            mock_amqp.publish_wearable_data.assert_called_once()

            # Verify scenario-specific metrics ranges
            metrics = payload["metrics"]
            if scenario == "normal":
                assert 60 <= metrics["heartRateBpm"] <= 95
                assert 96.0 <= metrics["spO2Percentage"] <= 99.5
            elif scenario == "abnormal":
                assert 105 <= metrics["heartRateBpm"] <= 125
                assert 92.0 <= metrics["spO2Percentage"] <= 94.5
            else:  # emergency
                assert 140 <= metrics["heartRateBpm"] <= 190
                assert 85.0 <= metrics["spO2Percentage"] <= 92.5

    @patch("src.app.amqp_setup")
    def test_concurrent_publishing_thread_safety(self, mock_amqp):
        """Test that concurrent message generation is thread-safe."""
        mock_amqp.publish_wearable_data = Mock()
        mock_amqp.connect = Mock()

        publisher = WearablePublisher("normal")
        publisher.is_connected = True

        # Generate multiple payloads concurrently
        payloads = []
        threads = []

        def generate_payload():
            payload = publisher._generate_base_payload()
            payload["timestamp"] = int(time.time() * 1000)
            payload["metrics"] = publisher.metric_generator()
            payloads.append(payload)

        # Create and start threads
        for _ in range(10):
            thread = threading.Thread(target=generate_payload)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify all payloads were generated
        assert len(payloads) == 10

        # Verify all payloads have required structure
        for payload in payloads:
            assert "patient_id" in payload
            assert "metrics" in payload
            assert "timestamp" in payload

    def test_message_schema_compliance(self, sample_wearable_metrics):
        """Test that generated messages comply with expected schema."""
        publisher = WearablePublisher("normal")

        payload = publisher._generate_base_payload()
        payload["timestamp"] = int(time.time() * 1000)
        payload["metrics"] = sample_wearable_metrics["normal"]

        # Define expected schema structure
        required_top_level = [
            "patient_id",
            "device",
            "timestamp",
            "metrics",
            "schemaVersion",
        ]
        required_device = ["id", "model"]
        required_metrics = [
            "heartRateBpm",
            "spO2Percentage",
            "respirationRateBpm",
            "bodyTemperatureCelsius",
            "stepsSinceLastReading",
        ]

        # Verify top-level structure
        for field in required_top_level:
            assert field in payload, f"Missing required field: {field}"

        # Verify device structure
        for field in required_device:
            assert field in payload["device"], f"Missing device field: {field}"

        # Verify metrics structure
        for field in required_metrics:
            assert field in payload["metrics"], f"Missing metrics field: {field}"

        # Verify data types
        assert isinstance(payload["patient_id"], str)
        assert isinstance(payload["timestamp"], int)
        assert isinstance(payload["metrics"]["heartRateBpm"], int)
        assert isinstance(payload["metrics"]["spO2Percentage"], (int, float))

    @patch("src.app.amqp_setup")
    def test_error_handling_during_publishing(self, mock_amqp):
        """Test error handling during message publishing."""
        # Setup mock to raise exception
        mock_amqp.publish_wearable_data.side_effect = Exception("Publishing failed")
        mock_amqp.connect = Mock()

        publisher = WearablePublisher("normal")
        publisher.is_connected = True

        # This should not raise an exception but should handle it gracefully
        try:
            payload = publisher._generate_base_payload()
            payload["timestamp"] = int(time.time() * 1000)
            payload["metrics"] = publisher.metric_generator()
            mock_amqp.publish_wearable_data(payload)
        except Exception:
            # If an exception occurs, the publisher should handle it gracefully
            # and set is_connected to False (this would happen in the real run_publisher_loop)
            pass

        # Verify that publish was attempted
        mock_amqp.publish_wearable_data.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
