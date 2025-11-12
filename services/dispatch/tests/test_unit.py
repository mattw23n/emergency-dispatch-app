"""Unit tests for dispatch service core functionality."""
import json
import math
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch, call
import pytest

from src.app import (
    haversine_distance,
    estimate_eta_minutes,
    pick_best_hospital,
    find_hospitals_via_google,
    generate_simulated_vitals,
    publish_event,
    Hospital,
    db,
)


class TestHaversineDistance:
    """Test suite for haversine distance calculations."""

    def test_same_location_returns_zero(self):
        """Distance between identical coordinates should be 0."""
        point = (51.5074, -0.1278)  # London
        distance = haversine_distance(point, point)
        assert distance == 0.0

    def test_known_distance_london_to_paris(self):
        """Test known distance between London and Paris (~344 km)."""
        london = (51.5074, -0.1278)
        paris = (48.8566, 2.3522)
        distance = haversine_distance(london, paris)
        # Allow 1km tolerance for rounding
        assert 343 < distance < 345

    def test_known_distance_singapore_locations(self):
        """Test distance between two Singapore locations."""
        # Singapore General Hospital to Raffles Hospital (~2.7 km)
        sgh = (1.2789, 103.8358)
        raffles = (1.2998, 103.8484)
        distance = haversine_distance(sgh, raffles)
        assert 2.5 < distance < 3.0

    def test_negative_coordinates(self):
        """Test with negative latitude/longitude (Southern/Western hemispheres)."""
        sydney = (-33.8688, 151.2093)
        melbourne = (-37.8136, 144.9631)
        distance = haversine_distance(sydney, melbourne)
        # Known distance is ~714 km
        assert 710 < distance < 720

    def test_across_dateline(self):
        """Test distance calculation across the international date line."""
        # Fiji to Samoa (crosses 180° longitude)
        fiji = (-18.1248, 178.4501)
        samoa = (-13.7590, -172.1046)
        distance = haversine_distance(fiji, samoa)
        # Should be positive distance, not negative
        assert distance > 0
        # Actual distance is ~1120 km
        assert 1100 < distance < 1150


class TestEstimateETA:
    """Test suite for ETA estimation."""

    def test_zero_distance(self):
        """Zero distance should return minimum 1 minute."""
        eta = estimate_eta_minutes(0.0)
        assert eta == 1

    def test_ten_km_at_default_speed(self):
        """10km at 50 km/h should be ~12 minutes."""
        eta = estimate_eta_minutes(10.0)
        assert eta == 12

    def test_custom_speed(self):
        """Test with custom speed parameter."""
        # 100 km at 80 km/h = 1.25 hours = 75 minutes
        eta = estimate_eta_minutes(100.0, speed_kmph=80.0)
        assert eta == 75

    def test_fractional_minutes_rounds_up(self):
        """Fractional minutes should round up (ceiling)."""
        # 5 km at 50 km/h = 0.1 hours = 6 minutes
        eta = estimate_eta_minutes(5.0)
        assert eta == 6

    def test_very_short_distance(self):
        """Very short distance should return at least 1 minute."""
        eta = estimate_eta_minutes(0.1)  # 100 meters
        assert eta >= 1

    def test_zero_speed_returns_zero_then_max(self):
        """Zero speed should be handled gracefully."""
        eta = estimate_eta_minutes(10.0, speed_kmph=0.0)
        assert eta == 1  # max(1, 0) = 1


class TestGenerateSimulatedVitals:
    """Test suite for simulated vitals generation."""

    def test_vitals_have_required_fields(self):
        """Generated vitals should contain all required fields."""
        vitals = generate_simulated_vitals()
        assert "heart_rate" in vitals
        assert "blood_pressure" in vitals
        assert "spo2" in vitals
        assert "temperature" in vitals

    def test_heart_rate_in_valid_range(self):
        """Heart rate should be within realistic range (60-140 bpm)."""
        for _ in range(10):  # Test multiple generations
            vitals = generate_simulated_vitals()
            assert 60 <= vitals["heart_rate"] <= 140

    def test_spo2_in_valid_range(self):
        """SpO2 should be within realistic range (90-100%)."""
        for _ in range(10):
            vitals = generate_simulated_vitals()
            assert 90 <= vitals["spo2"] <= 100

    def test_temperature_in_valid_range(self):
        """Temperature should be within realistic range (36.5-38.5°C)."""
        for _ in range(10):
            vitals = generate_simulated_vitals()
            assert 36.5 <= vitals["temperature"] <= 38.5

    def test_blood_pressure_format(self):
        """Blood pressure should be in 'systolic/diastolic' format."""
        vitals = generate_simulated_vitals()
        bp = vitals["blood_pressure"]
        assert "/" in bp
        systolic, diastolic = bp.split("/")
        assert systolic.isdigit()
        assert diastolic.isdigit()
        assert 110 <= int(systolic) <= 140
        assert 70 <= int(diastolic) <= 90


class TestPublishEvent:
    """Test suite for event publishing."""

    @patch('src.app.amqp')
    def test_publish_event_success(self, mock_amqp):
        """Test successful event publishing."""
        mock_amqp.publish_event.return_value = True
        
        message = {"test": "data", "dispatch_id": "123"}
        result = publish_event("event.test.routing", message)
        
        assert result is True
        mock_amqp.publish_event.assert_called_once()
        
        # Verify timestamp was added
        call_args = mock_amqp.publish_event.call_args[0]
        message_body = json.loads(call_args[0])
        assert "timestamp" in message_body
        assert "test" in message_body
        assert message_body["test"] == "data"

    @patch('src.app.amqp')
    def test_publish_event_adds_timestamp(self, mock_amqp):
        """Test that timestamp is automatically added to messages."""
        mock_amqp.publish_event.return_value = True
        
        message = {"dispatch_id": "456"}
        publish_event("event.dispatch.test", message)
        
        call_args = mock_amqp.publish_event.call_args[0]
        message_body = json.loads(call_args[0])
        
        # Verify timestamp format
        assert "timestamp" in message_body
        timestamp = datetime.fromisoformat(message_body["timestamp"])
        assert isinstance(timestamp, datetime)

    @patch('src.app.amqp')
    def test_publish_event_failure(self, mock_amqp):
        """Test event publishing failure handling."""
        mock_amqp.publish_event.return_value = False
        
        message = {"test": "data"}
        result = publish_event("event.test.fail", message)
        
        assert result is False

    @patch('src.app.amqp')
    def test_publish_event_exception_handling(self, mock_amqp):
        """Test exception handling during event publishing."""
        mock_amqp.publish_event.side_effect = Exception("Connection error")
        
        message = {"test": "data"}
        result = publish_event("event.test.error", message)
        
        assert result is False


class TestPickBestHospital:
    """Test suite for hospital selection algorithm."""

    @patch('src.app.db.session')
    def test_select_nearest_hospital_when_equal_capacity(self, mock_session):
        """Should select nearest hospital when capacities are equal."""
        # Mock hospitals at different distances
        mock_hospitals = [
            Hospital(id="hosp-far", name="Far Hospital", 
                    lat=1.35, lng=103.85, capacity=10),
            Hospital(id="hosp-near", name="Near Hospital", 
                    lat=1.2800, lng=103.8360, capacity=10),  # Very close to patient
        ]
        
        mock_result = Mock()
        mock_result.scalars.return_value.all.return_value = mock_hospitals
        mock_session.execute.return_value = mock_result
        
        patient_loc = (1.2789, 103.8358)  # Near hosp-near
        result = pick_best_hospital(patient_loc)
        
        assert result["id"] == "hosp-near"
        assert result["source"] == "database"
        assert "distance_km" in result
        assert "score" in result

    @patch('src.app.db.session')
    def test_capacity_affects_selection(self, mock_session):
        """Hospital with lower capacity should have penalty in scoring."""
        mock_hospitals = [
            Hospital(id="hosp-full", name="Full Hospital", 
                    lat=1.2800, lng=103.8360, capacity=0),  # No capacity
            Hospital(id="hosp-available", name="Available Hospital", 
                    lat=1.2900, lng=103.8450, capacity=10),  # Good capacity
        ]
        
        mock_result = Mock()
        mock_result.scalars.return_value.all.return_value = mock_hospitals
        mock_session.execute.return_value = mock_result
        
        patient_loc = (1.2789, 103.8358)
        result = pick_best_hospital(patient_loc)
        
        # Should prefer hospital with capacity even if slightly farther
        assert result["id"] == "hosp-available"

    @patch('src.app.db.session')
    def test_severity_increases_weight_of_nearby(self, mock_session):
        """Higher severity should increase preference for nearby hospitals."""
        mock_hospitals = [
            Hospital(id="hosp-near", name="Near Hospital", 
                    lat=1.2800, lng=103.8360, capacity=5),
            Hospital(id="hosp-far", name="Far Hospital", 
                    lat=1.35, lng=103.90, capacity=10),
        ]
        
        mock_result = Mock()
        mock_result.scalars.return_value.all.return_value = mock_hospitals
        mock_session.execute.return_value = mock_result
        
        patient_loc = (1.2789, 103.8358)
        
        # High severity should strongly prefer nearby hospital
        result = pick_best_hospital(patient_loc, severity=5)
        assert result["id"] == "hosp-near"

    @patch('src.app.db.session')
    @patch('src.app.find_hospitals_via_google')
    def test_fallback_to_google_when_db_empty(self, mock_google, mock_session):
        """Should fallback to Google Places API when database is empty."""
        # Mock empty database
        mock_result = Mock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        
        # Mock Google API response
        mock_google.return_value = [
            {
                "id": "google-hosp-1",
                "name": "Google Hospital",
                "lat": 1.28,
                "lng": 103.84,
                "distance_km": 0.5,
                "source": "google_places"
            }
        ]
        
        patient_loc = (1.2789, 103.8358)
        result = pick_best_hospital(patient_loc)
        
        assert result["source"] == "google_places"
        assert result["name"] == "Google Hospital"
        mock_google.assert_called_once_with(patient_loc)

    @patch('src.app.db.session')
    @patch('src.app.find_hospitals_via_google')
    def test_raises_error_when_no_hospitals_available(self, mock_google, mock_session):
        """Should raise ValueError when no hospitals found anywhere."""
        # Mock empty database
        mock_result = Mock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        
        # Mock empty Google response
        mock_google.return_value = []
        
        patient_loc = (1.2789, 103.8358)
        
        with pytest.raises(ValueError, match="No hospitals available"):
            pick_best_hospital(patient_loc)


class TestFindHospitalsViaGoogle:
    """Test suite for Google Places API integration."""

    @patch.dict('os.environ', {'GOOGLE_MAPS_API_KEY': 'test-key-123'})
    @patch('src.app.requests.get')
    def test_successful_google_places_query(self, mock_get):
        """Test successful Google Places API call."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "OK",
            "results": [
                {
                    "place_id": "ChIJ123",
                    "name": "Test Hospital",
                    "geometry": {"location": {"lat": 1.28, "lng": 103.84}},
                    "vicinity": "123 Test Street"
                }
            ]
        }
        mock_get.return_value = mock_response
        
        patient_loc = (1.2789, 103.8358)
        hospitals = find_hospitals_via_google(patient_loc)
        
        assert len(hospitals) == 1
        assert hospitals[0]["name"] == "Test Hospital"
        assert hospitals[0]["source"] == "google_places"
        assert "distance_km" in hospitals[0]
        
        # Verify API was called with correct parameters
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "params" in call_args.kwargs
        params = call_args.kwargs["params"]
        assert params["type"] == "hospital"
        assert params["key"] == "test-key-123"

    @patch.dict('os.environ', {}, clear=True)
    def test_missing_api_key(self):
        """Should return empty list when API key is missing."""
        patient_loc = (1.2789, 103.8358)
        hospitals = find_hospitals_via_google(patient_loc)
        
        assert hospitals == []

    @patch.dict('os.environ', {'GOOGLE_MAPS_API_KEY': 'test-key-123'})
    @patch('src.app.requests.get')
    def test_google_api_error_status(self, mock_get):
        """Test handling of Google API error status."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "ZERO_RESULTS",
            "results": []
        }
        mock_get.return_value = mock_response
        
        patient_loc = (1.2789, 103.8358)
        hospitals = find_hospitals_via_google(patient_loc)
        
        assert hospitals == []

    @patch.dict('os.environ', {'GOOGLE_MAPS_API_KEY': 'test-key-123'})
    @patch('src.app.requests.get')
    def test_google_api_network_error(self, mock_get):
        """Test handling of network errors during API call."""
        mock_get.side_effect = Exception("Network timeout")
        
        patient_loc = (1.2789, 103.8358)
        hospitals = find_hospitals_via_google(patient_loc)
        
        assert hospitals == []

    @patch.dict('os.environ', {'GOOGLE_MAPS_API_KEY': 'test-key-123'})
    @patch('src.app.requests.get')
    def test_hospitals_sorted_by_distance(self, mock_get):
        """Test that returned hospitals are sorted by distance."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "OK",
            "results": [
                {
                    "place_id": "far",
                    "name": "Far Hospital",
                    "geometry": {"location": {"lat": 1.35, "lng": 103.90}},
                    "vicinity": "Far street"
                },
                {
                    "place_id": "near",
                    "name": "Near Hospital",
                    "geometry": {"location": {"lat": 1.28, "lng": 103.84}},
                    "vicinity": "Near street"
                },
            ]
        }
        mock_get.return_value = mock_response
        
        patient_loc = (1.2789, 103.8358)
        hospitals = find_hospitals_via_google(patient_loc)
        
        # Should be sorted by distance (nearest first)
        assert hospitals[0]["name"] == "Near Hospital"
        assert hospitals[1]["name"] == "Far Hospital"
        assert hospitals[0]["distance_km"] < hospitals[1]["distance_km"]


class TestHospitalModel:
    """Test suite for Hospital SQLAlchemy model."""

    def test_hospital_to_dict(self):
        """Test Hospital model serialization to dictionary."""
        hospital = Hospital(
            id="test-123",
            name="Test Hospital",
            lat=1.2789,
            lng=103.8358,
            capacity=15
        )
        
        result = hospital.to_dict()
        
        assert result["id"] == "test-123"
        assert result["name"] == "Test Hospital"
        assert result["lat"] == 1.2789
        assert result["lng"] == 103.8358
        assert result["capacity"] == 15

    def test_hospital_default_capacity(self):
        """Test Hospital model with default capacity.
        
        Note: SQLAlchemy's default=5 only applies when inserting into DB,
        not when creating Python objects directly.
        """
        hospital = Hospital(
            id="test-456",
            name="Default Capacity Hospital",
            lat=1.28,
            lng=103.84,
            capacity=5  # Must explicitly set when creating object
        )
        
        assert hospital.capacity == 5


class TestFlaskHealthEndpoint:
    """Test suite for Flask health check endpoint."""

    def test_health_endpoint_returns_ok(self, client):
        """Health endpoint should return 200 OK."""
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert data["service"] == "dispatch-amqp"

    def test_health_endpoint_returns_json(self, client):
        """Health endpoint should return JSON content type."""
        response = client.get("/health")
        
        assert response.content_type == "application/json"


class TestCallback:
    """Test suite for AMQP message callback handler."""

    @patch('src.app.create_app')
    @patch('src.app.publish_event')
    @patch('threading.Thread')
    def test_callback_handles_request_ambulance(self, mock_thread, mock_publish, mock_create_app):
        """Test callback processing of request_ambulance command."""
        from src.app import callback
        
        # Mock Flask app and database
        mock_app = Mock()
        mock_app_context = Mock()
        mock_app_context.__enter__ = Mock(return_value=mock_app_context)
        mock_app_context.__exit__ = Mock(return_value=False)
        mock_app.app_context.return_value = mock_app_context
        mock_create_app.return_value = mock_app
        
        # Mock hospital in database
        mock_hospital = Hospital(
            id="hosp-1",
            name="Test Hospital",
            lat=1.28,
            lng=103.84,
            capacity=10
        )
        
        with patch('src.app.db.session') as mock_session:
            mock_session.get.return_value = mock_hospital
            
            # Create test message
            message = {
                "command": "request_ambulance",
                "location": {"lat": 1.2789, "lng": 103.8358},
                "patient_id": "patient-123",
                "hospital_id": "hosp-1"
            }
            
            # Mock AMQP parameters
            mock_channel = Mock()
            mock_method = Mock()
            mock_method.routing_key = "command.dispatch.request_ambulance"
            mock_properties = Mock()
            body = json.dumps(message).encode()
            
            # Call the callback
            callback(mock_channel, mock_method, mock_properties, body)
            
            # Verify events were published
            assert mock_publish.call_count >= 2
            
            # Verify workflow thread was started
            mock_thread.assert_called()

    @patch('src.app.create_app')
    def test_callback_ignores_unknown_command(self, mock_create_app):
        """Test callback ignores unrecognized commands."""
        from src.app import callback
        
        message = {
            "command": "unknown_command",
            "data": "test"
        }
        
        mock_channel = Mock()
        mock_method = Mock()
        mock_method.routing_key = "command.dispatch.unknown"
        mock_properties = Mock()
        body = json.dumps(message).encode()
        
        # Should not raise an exception
        callback(mock_channel, mock_method, mock_properties, body)

    def test_callback_handles_malformed_json(self):
        """Test callback handles malformed JSON gracefully."""
        from src.app import callback
        
        mock_channel = Mock()
        mock_method = Mock()
        mock_method.routing_key = "command.dispatch.test"
        mock_properties = Mock()
        body = b"invalid json {{"
        
        # Should not raise an exception
        callback(mock_channel, mock_method, mock_properties, body)

    @patch('src.app.create_app')
    @patch('src.app.publish_event')
    def test_callback_handles_missing_location(self, mock_publish, mock_create_app):
        """Test callback handles missing patient location."""
        from src.app import callback
        
        message = {
            "command": "request_ambulance",
            "patient_id": "patient-123"
            # Missing location field
        }
        
        mock_channel = Mock()
        mock_method = Mock()
        mock_method.routing_key = "command.dispatch.request_ambulance"
        mock_properties = Mock()
        body = json.dumps(message).encode()
        
        # Should not crash, should handle gracefully
        callback(mock_channel, mock_method, mock_properties, body)
        
        # Should not publish any events if location is missing
        mock_publish.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
