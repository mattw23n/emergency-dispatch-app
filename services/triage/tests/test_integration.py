import pytest
import json
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.app import determine_triage_status, process_message, service_state


class TestTriageIntegration:
    """Integration tests for triage service with wearable and events manager."""

    def test_end_to_end_normal_to_emergency_flow(
        self, reset_service_state, mock_amqp, sample_wearable_data, mock_channel
    ):
        """Test complete flow from normal to emergency status change."""
        channel, method, properties = mock_channel

        # Step 1: Send normal vitals (should not trigger alert)
        normal_data = json.dumps(sample_wearable_data["normal"])
        process_message(channel, method, properties, normal_data.encode())

        # Verify normal processing
        assert service_state["messages_processed"] == 1
        assert service_state["emergencies_detected"] == 0
        assert service_state["patient_states"]["P123"] == "Normal"
        mock_amqp.publish_triage_status.assert_not_called()

        # Step 2: Send emergency vitals (should trigger alert)
        emergency_data = json.dumps(sample_wearable_data["emergency"])
        process_message(channel, method, properties, emergency_data.encode())

        # Verify emergency processing
        assert service_state["messages_processed"] == 2
        assert service_state["emergencies_detected"] == 1
        assert service_state["patient_states"]["P123"] == "Emergency"

        # Verify alert was published
        mock_amqp.publish_triage_status.assert_called_once()
        call_args = mock_amqp.publish_triage_status.call_args

        assert call_args[0][1] == "Emergency"  # status
        published_event = call_args[0][2]  # payload
        assert published_event["status"] == "emergency"
        assert published_event["previous_status"] == "normal"
        assert published_event["patient_id"] == "P123"
        assert published_event["type"] == "TriageStatus"

    def test_wearable_to_triage_message_processing(
        self, reset_service_state, mock_amqp, sample_wearable_data, mock_channel
    ):
        """Test message processing from wearable service format."""
        channel, method, properties = mock_channel

        # Test with wearable data format
        wearable_data = {
            "patient_id": "P456",
            "device": {"id": "wearable-2", "model": "HealthTracker v2"},
            "location": {"lat": 1.290270, "lng": 103.851959},
            "timestampMs": int(time.time() * 1000),
            "metrics": {
                "heartRateBpm": 35,  # Emergency - too low
                "spO2Percentage": 96.0,
                "respirationRateBpm": 16,
                "bodyTemperatureCelsius": 37.0,
                "stepsSinceLastReading": 0,
            },
        }

        message_body = json.dumps(wearable_data).encode()
        process_message(channel, method, properties, message_body)

        # Verify processing
        assert service_state["messages_processed"] == 1
        assert service_state["emergencies_detected"] == 1
        assert service_state["patient_states"]["P456"] == "Emergency"

        # Verify emergency alert was sent
        mock_amqp.publish_triage_status.assert_called_once()
        call_args = mock_amqp.publish_triage_status.call_args
        assert call_args[0][1] == "Emergency"

    def test_triage_to_events_manager_routing_keys(
        self, reset_service_state, mock_amqp, sample_wearable_data, mock_channel
    ):
        """Test that correct routing keys are used for events manager."""
        channel, method, properties = mock_channel

        # Test abnormal status
        abnormal_data = json.dumps(sample_wearable_data["abnormal"])
        process_message(channel, method, properties, abnormal_data.encode())

        # Verify abnormal routing key
        mock_amqp.publish_triage_status.assert_called_once()
        call_args = mock_amqp.publish_triage_status.call_args
        assert call_args[0][1] == "Abnormal"  # status parameter

        # Reset mock
        mock_amqp.reset_mock()

        # Test emergency status
        emergency_data = json.dumps(sample_wearable_data["emergency"])
        process_message(channel, method, properties, emergency_data.encode())

        # Verify emergency routing key
        mock_amqp.publish_triage_status.assert_called_once()
        call_args = mock_amqp.publish_triage_status.call_args
        assert call_args[0][1] == "Emergency"  # status parameter

    def test_multiple_patients_status_tracking(
        self, reset_service_state, mock_amqp, mock_channel
    ):
        """Test tracking multiple patients independently."""
        channel, method, properties = mock_channel

        # Patient 1 - Emergency
        patient1_data = {
            "patient_id": "P001",
            "metrics": {
                "heartRateBpm": 160,  # Emergency
                "spO2Percentage": 96.0,
                "respirationRateBpm": 16,
                "bodyTemperatureCelsius": 37.0,
            },
        }

        # Patient 2 - Abnormal
        patient2_data = {
            "patient_id": "P002",
            "metrics": {
                "heartRateBpm": 110,  # Abnormal
                "spO2Percentage": 96.0,
                "respirationRateBpm": 16,
                "bodyTemperatureCelsius": 37.0,
            },
        }

        # Process both patients
        process_message(channel, method, properties, json.dumps(patient1_data).encode())
        process_message(channel, method, properties, json.dumps(patient2_data).encode())

        # Verify independent tracking
        assert service_state["patient_states"]["P001"] == "Emergency"
        assert service_state["patient_states"]["P002"] == "Abnormal"
        assert service_state["emergencies_detected"] == 1
        assert service_state["abnormalities_detected"] == 1
        assert mock_amqp.publish_triage_status.call_count == 2

    def test_no_duplicate_alerts_for_same_status(
        self, reset_service_state, mock_amqp, sample_wearable_data, mock_channel
    ):
        """Test that duplicate alerts are not sent for unchanged status."""
        channel, method, properties = mock_channel

        # Send emergency data twice
        emergency_data = json.dumps(sample_wearable_data["emergency"])

        # First emergency message - should trigger alert
        process_message(channel, method, properties, emergency_data.encode())
        assert mock_amqp.publish_triage_status.call_count == 1

        # Second emergency message - should NOT trigger alert (same status)
        process_message(channel, method, properties, emergency_data.encode())
        assert mock_amqp.publish_triage_status.call_count == 1  # Still 1, not 2

        # Verify state
        assert service_state["messages_processed"] == 2
        assert service_state["emergencies_detected"] == 1  # Only counted once
        assert service_state["patient_states"]["P123"] == "Emergency"

    def test_status_escalation_abnormal_to_emergency(
        self, reset_service_state, mock_amqp, sample_wearable_data, mock_channel
    ):
        """Test status escalation from abnormal to emergency."""
        channel, method, properties = mock_channel

        # Step 1: Abnormal status
        abnormal_data = json.dumps(sample_wearable_data["abnormal"])
        process_message(channel, method, properties, abnormal_data.encode())

        assert service_state["abnormalities_detected"] == 1
        assert service_state["emergencies_detected"] == 0
        assert mock_amqp.publish_triage_status.call_count == 1

        # Verify abnormal alert
        call_args = mock_amqp.publish_triage_status.call_args
        assert call_args[0][1] == "Abnormal"

        # Step 2: Emergency status (escalation)
        emergency_data = json.dumps(sample_wearable_data["emergency"])
        process_message(channel, method, properties, emergency_data.encode())

        assert service_state["abnormalities_detected"] == 1
        assert service_state["emergencies_detected"] == 1
        assert mock_amqp.publish_triage_status.call_count == 2

        # Verify emergency alert with previous status
        call_args = mock_amqp.publish_triage_status.call_args
        assert call_args[0][1] == "Emergency"
        published_event = call_args[0][2]
        assert published_event["previous_status"] == "abnormal"

    def test_malformed_message_handling(
        self, reset_service_state, mock_amqp, mock_channel
    ):
        """Test handling of malformed messages from wearable service."""
        channel, method, properties = mock_channel

        # Test invalid JSON
        invalid_json = b"not valid json"
        process_message(channel, method, properties, invalid_json)

        # Verify error handling
        channel.basic_nack.assert_called_with(
            delivery_tag=method.delivery_tag, requeue=False
        )
        assert service_state["messages_processed"] == 0
        mock_amqp.publish_triage_status.assert_not_called()

    def test_message_acknowledgment(
        self, reset_service_state, mock_amqp, sample_wearable_data, mock_channel
    ):
        """Test proper message acknowledgment."""
        channel, method, properties = mock_channel

        # Process valid message
        normal_data = json.dumps(sample_wearable_data["normal"])
        process_message(channel, method, properties, normal_data.encode())

        # Verify message was acknowledged
        channel.basic_ack.assert_called_once_with(delivery_tag=method.delivery_tag)
        channel.basic_nack.assert_not_called()

    def test_events_manager_message_format(
        self, reset_service_state, mock_amqp, sample_wearable_data, mock_channel
    ):
        """Test that messages sent to events manager have correct format."""
        channel, method, properties = mock_channel

        emergency_data = json.dumps(sample_wearable_data["emergency"])
        process_message(channel, method, properties, emergency_data.encode())

        # Verify message format for events manager
        mock_amqp.publish_triage_status.assert_called_once()
        call_args = mock_amqp.publish_triage_status.call_args

        incident_id = call_args[0][0]
        status = call_args[0][1]
        payload = call_args[0][2]

        # Verify required fields for events manager
        assert isinstance(incident_id, str)
        assert status == "Emergency"
        assert payload["type"] == "TriageStatus"
        assert "incident_id" in payload
        assert "patient_id" in payload
        assert "status" in payload
        assert "metrics" in payload
        assert "location" in payload
        assert "timestamp" in payload


class TestTriageBusinessLogic:
    """Test triage decision logic."""

    @pytest.mark.parametrize(
        "metrics,expected_status,expected_reason",
        [
            # Normal cases
            (
                {
                    "heartRateBpm": 75,
                    "spO2Percentage": 98,
                    "bodyTemperatureCelsius": 37.0,
                    "respirationRateBpm": 16,
                },
                "Normal",
                "All vitals are within the normal range.",
            ),
            # Emergency cases - SpO2
            (
                {
                    "heartRateBpm": 75,
                    "spO2Percentage": 89,
                    "bodyTemperatureCelsius": 37.0,
                    "respirationRateBpm": 16,
                },
                "Emergency",
                "Critically low blood oxygen (Severe Hypoxia).",
            ),
            # Emergency cases - Heart Rate
            (
                {
                    "heartRateBpm": 160,
                    "spO2Percentage": 98,
                    "bodyTemperatureCelsius": 37.0,
                    "respirationRateBpm": 16,
                },
                "Emergency",
                "Critically abnormal heart rate.",
            ),
            (
                {
                    "heartRateBpm": 35,
                    "spO2Percentage": 98,
                    "bodyTemperatureCelsius": 37.0,
                    "respirationRateBpm": 16,
                },
                "Emergency",
                "Critically abnormal heart rate.",
            ),
            # Emergency cases - Temperature
            (
                {
                    "heartRateBpm": 75,
                    "spO2Percentage": 98,
                    "bodyTemperatureCelsius": 40.0,
                    "respirationRateBpm": 16,
                },
                "Emergency",
                "Critically abnormal body temperature.",
            ),
            # Abnormal cases - SpO2
            (
                {
                    "heartRateBpm": 75,
                    "spO2Percentage": 93,
                    "bodyTemperatureCelsius": 37.0,
                    "respirationRateBpm": 16,
                },
                "Abnormal",
                "Low blood oxygen (Mild Hypoxia).",
            ),
            # Abnormal cases - Heart Rate
            (
                {
                    "heartRateBpm": 110,
                    "spO2Percentage": 98,
                    "bodyTemperatureCelsius": 37.0,
                    "respirationRateBpm": 16,
                },
                "Abnormal",
                "Abnormal heart rate.",
            ),
        ],
    )
    def test_triage_decision_logic(self, metrics, expected_status, expected_reason):
        """Test triage decision logic with various metric combinations."""
        status, reason = determine_triage_status(metrics)
        assert status == expected_status
        assert reason == expected_reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
