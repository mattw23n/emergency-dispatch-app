import pytest
import time
import sys
import os
from unittest.mock import Mock, patch


# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.app import app
import src.app as app_module

from dotenv import load_dotenv

load_dotenv()


@pytest.fixture
def client():
    """Flask test client fixture."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def mock_amqp():
    """Mock AMQP setup for testing."""
    with patch("src.app.amqp_setup") as mock:
        mock.connection = Mock()
        mock.connection.is_open = True
        mock.channel = Mock()
        mock.publish_wearable_data = Mock()
        mock.publish = Mock()
        mock.connect = Mock()
        mock.close = Mock()
        mock.RK_WEARABLE_DATA = "wearable.data"
        yield mock


@pytest.fixture
def mock_publisher():
    """Mock WearablePublisher for testing."""
    with patch("src.app.WearablePublisher") as mock_class:
        mock_instance = Mock()
        mock_instance.scenario = "normal"
        mock_instance.messages_sent = 5
        mock_instance.is_connected = True
        mock_instance.is_running = True
        mock_instance.stop = Mock()
        mock_class.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def sample_wearable_metrics():
    """Sample wearable metrics for different scenarios."""
    return {
        "normal": {
            "heartRateBpm": 75,
            "spO2Percentage": 98.5,
            "respirationRateBpm": 16,
            "bodyTemperatureCelsius": 37.0,
            "stepsSinceLastReading": 20,
        },
        "abnormal": {
            "heartRateBpm": 115,
            "spO2Percentage": 93.0,
            "respirationRateBpm": 22,
            "bodyTemperatureCelsius": 38.0,
            "stepsSinceLastReading": 5,
        },
        "emergency": {
            "heartRateBpm": 160,
            "spO2Percentage": 88.0,
            "respirationRateBpm": 28,
            "bodyTemperatureCelsius": 36.5,
            "stepsSinceLastReading": 0,
        },
    }


@pytest.fixture
def reset_global_publisher():
    """Reset global publisher instance before each test."""
    original_publisher = app_module.publisher_instance
    app_module.publisher_instance = None
    yield
    app_module.publisher_instance = original_publisher


class MockRabbitMQConnection:
    """Mock RabbitMQ connection for integration testing."""

    def __init__(self):
        self.published_messages = []
        self.is_connected = True

    def publish_message(self, routing_key, body, properties=None):
        self.published_messages.append(
            {
                "routing_key": routing_key,
                "body": body,
                "properties": properties,
                "timestamp": time.time(),
            }
        )

    def get_published_messages(self, routing_key=None):
        if routing_key:
            return [
                msg
                for msg in self.published_messages
                if msg["routing_key"] == routing_key
            ]
        return self.published_messages

    def clear_messages(self):
        self.published_messages.clear()


@pytest.fixture
def mock_rabbitmq():
    """Mock RabbitMQ connection for integration testing."""
    return MockRabbitMQConnection()
