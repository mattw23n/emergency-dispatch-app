import pytest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.app import app

@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

@patch('src.app.publisher_instance')
def test_health_check_when_connected(mock_publisher_instance, client):
    """
    Tests the /health endpoint when the publisher is 'connected'.
    This test mocks the publisher and does NOT require RabbitMQ.
    """
    # Configure the mock object to simulate a healthy, connected state
    mock_publisher_instance.is_connected = True
    mock_publisher_instance.scenario = 'normal'
    mock_publisher_instance.messages_sent = 10
    
    # Make the request using the test client
    response = client.get('/health')
    
    # Assert the results
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'ok'
    assert data['rabbitmq_connected'] is True
    assert data['scenario'] == 'normal'
    assert data['messages_sent'] == 10

@patch('src.app.publisher_instance')
def test_health_check_when_disconnected(mock_publisher_instance, client):
    """
    Tests the /health endpoint when the publisher is 'disconnected'.
    This test also mocks the publisher and does NOT require RabbitMQ.
    """
    # Configure the mock object to simulate a degraded, disconnected state
    mock_publisher_instance.is_connected = False
    mock_publisher_instance.scenario = 'normal'
    mock_publisher_instance.messages_sent = 0 # Assume 0 if never connected

    # Make the request
    response = client.get('/health')

    # Assert the results for a degraded service
    assert response.status_code == 503  # 503 Service Unavailable is appropriate
    data = response.get_json()
    assert data['status'] == 'degraded'
    assert data['rabbitmq_connected'] is False