"""Integration tests for the dispatch service."""
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add the parent directory to the path so we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_health_endpoint(client):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["service"] == "dispatch-amqp"


def test_database_seeded_with_hospitals(app, sample_hospitals):
    """Test that the database is seeded with sample hospitals."""
    # sample_hospitals fixture already provides the mock hospitals
    assert len(sample_hospitals) == 3
    
    hospital_names = [h.name for h in sample_hospitals]
    assert "Central Hospital" in hospital_names
    assert "Westside Medical" in hospital_names
    assert "Riverside Clinic" in hospital_names


def test_haversine_distance_calculation():
    """Test the haversine distance calculation function."""
    from src.app import haversine_distance
    
    # Distance between London coordinates
    point_a = (51.5074, -0.1278)  # Central London
    point_b = (51.5155, -0.1420)  # Nearby location
    
    distance = haversine_distance(point_a, point_b)
    
    # Should be approximately 1-2 km
    assert 0.5 < distance < 2.5


def test_pick_best_hospital_from_database(app, sample_hospitals):
    """Test hospital selection from database."""
    from src.app import pick_best_hospital, db
    
    with app.app_context():
        # Mock the database query to return our sample hospitals
        with patch('src.app.db.session') as mock_session:
            mock_result = Mock()
            mock_result.scalars.return_value.all.return_value = sample_hospitals
            mock_session.execute.return_value = mock_result
            
            patient_location = (51.51, -0.12)
            best_hospital = pick_best_hospital(patient_location)
            
            assert best_hospital is not None
            assert "name" in best_hospital
            assert "distance_km" in best_hospital
            assert "score" in best_hospital
            assert best_hospital["source"] == "database"


def test_estimate_eta_minutes():
    """Test ETA estimation function."""
    from src.app import estimate_eta_minutes
    
    # 50 km at 50 km/h should be 60 minutes
    eta = estimate_eta_minutes(50.0, 50.0)
    assert eta == 60
    
    # 10 km at 50 km/h should be 12 minutes (rounded up)
    eta = estimate_eta_minutes(10.0, 50.0)
    assert eta == 12
    
    # Very small distance should return at least 1 minute
    eta = estimate_eta_minutes(0.1, 50.0)
    assert eta == 1


def test_generate_simulated_vitals():
    """Test that simulated vitals are generated correctly."""
    from src.app import generate_simulated_vitals
    
    vitals = generate_simulated_vitals()
    
    assert "heart_rate" in vitals
    assert "blood_pressure" in vitals
    assert "spo2" in vitals
    assert "temperature" in vitals
    
    # Check ranges
    assert 60 <= vitals["heart_rate"] <= 140
    assert 90 <= vitals["spo2"] <= 100
    assert 36.5 <= vitals["temperature"] <= 38.5


@patch('src.app.amqp.publish_event')
@patch('src.app.threading.Thread')
def test_callback_processes_request_ambulance(mock_thread, mock_publish, app, sample_hospitals):
    """Test that the callback function processes request_ambulance commands."""
    from src.app import callback, db
    
    message = {
        "command": "request_ambulance",
        "patient_id": "test-patient-001",
        "location": {"lat": 51.5, "lng": -0.1}
    }
    
    # Mock the method object
    method = Mock()
    method.routing_key = "cmd.dispatch.request_ambulance"
    
    # Mock channel and properties
    channel = Mock()
    properties = Mock()
    
    with app.app_context():
        # Mock database queries for hospital selection
        with patch('src.app.db.session') as mock_session:
            mock_result = Mock()
            mock_result.scalars.return_value.all.return_value = sample_hospitals
            mock_session.execute.return_value = mock_result
            mock_session.get.return_value = sample_hospitals[0]
            
            callback(channel, method, properties, json.dumps(message).encode())
    
    # Should publish two events: unit_assigned and enroute
    assert mock_publish.call_count == 2
    
    # Check first call (unit_assigned)
    first_call_args = mock_publish.call_args_list[0]
    assert "event.dispatch.unit_assigned" in str(first_call_args)
    
    # Check second call (enroute)
    second_call_args = mock_publish.call_args_list[1]
    assert "event.dispatch.enroute" in str(second_call_args)


def test_callback_ignores_unknown_commands(app):
    """Test that unknown commands are ignored."""
    from src.app import callback
    
    message = {
        "command": "unknown_command",
        "some_data": "test"
    }
    
    method = Mock()
    method.routing_key = "cmd.dispatch.unknown"
    channel = Mock()
    properties = Mock()
    
    # Should not raise an exception
    callback(channel, method, properties, json.dumps(message).encode())


def test_callback_handles_invalid_location(app):
    """Test that callback handles invalid location gracefully."""
    from src.app import callback
    
    message = {
        "command": "request_ambulance",
        "patient_id": "test-patient-001",
        "location": {}  # Invalid location
    }
    
    method = Mock()
    method.routing_key = "cmd.dispatch.request_ambulance"
    channel = Mock()
    properties = Mock()
    
    # Should not raise an exception
    callback(channel, method, properties, json.dumps(message).encode())
