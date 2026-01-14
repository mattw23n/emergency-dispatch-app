import pytest
import time
from unittest.mock import Mock, patch
import sys
import os


# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.app import app, service_state
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture
def client():
    """Flask test client fixture."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def reset_service_state():
    """Reset the global service state before each test."""
    global service_state
    service_state.clear()
    service_state.update(
        {
            "is_connected": False,
            "messages_processed": 0,
            "emergencies_detected": 0,
            "abnormalities_detected": 0,
            "patient_states": {},
        }
    )
    yield
    service_state.clear()


@pytest.fixture
def mock_amqp():
    """Mock AMQP setup for testing."""
    with patch("src.app.amqp_setup") as mock:
        mock.connection = Mock()
        mock.connection.is_open = True
        mock.channel = Mock()
        mock.publish_triage_status = Mock()
        yield mock


@pytest.fixture
def sample_wearable_data():
    """Sample wearable data for testing."""
    return {
        "normal": {
            "patient_id": "P123",
            "userId": "P123",
            "device": {"id": "wearable-1", "model": "HealthTracker v1"},
            "location": {"lat": 1.290270, "lng": 103.851959},
            "timestamp": int(time.time() * 1000),
            "metrics": {
                "heartRateBpm": 75,
                "spO2Percentage": 98.5,
                "respirationRateBpm": 16,
                "bodyTemperatureCelsius": 37.0,
                "stepsSinceLastReading": 20,
            },
        },
        "abnormal": {
            "patient_id": "P123",
            "userId": "P123",
            "device": {"id": "wearable-1", "model": "HealthTracker v1"},
            "location": {"lat": 1.290270, "lng": 103.851959},
            "timestamp": int(time.time() * 1000),
            "metrics": {
                "heartRateBpm": 110,  # Abnormal
                "spO2Percentage": 93.0,  # Abnormal
                "respirationRateBpm": 22,
                "bodyTemperatureCelsius": 38.0,
                "stepsSinceLastReading": 5,
            },
        },
        "emergency": {
            "patient_id": "P123",
            "userId": "P123",
            "device": {"id": "wearable-1", "model": "HealthTracker v1"},
            "location": {"lat": 1.290270, "lng": 103.851959},
            "timestamp": int(time.time() * 1000),
            "metrics": {
                "heartRateBpm": 160,  # Emergency
                "spO2Percentage": 88.0,  # Emergency
                "respirationRateBpm": 32,  # Emergency
                "bodyTemperatureCelsius": 40.0,  # Emergency
                "stepsSinceLastReading": 0,
            },
        },
    }


@pytest.fixture
def mock_channel():
    """Mock RabbitMQ channel for message processing tests."""
    channel = Mock()
    method = Mock()
    method.delivery_tag = "test-tag"
    properties = Mock()
    return channel, method, properties


class MockRabbitMQ:
    """Mock RabbitMQ connection for integration testing."""

    def __init__(self):
        self.messages = []
        self.queues = {}
        self.exchanges = {}
        self.bindings = {}

    def publish(self, exchange, routing_key, body, properties=None):
        """Mock message publishing."""
        message = {
            "exchange": exchange,
            "routing_key": routing_key,
            "body": body,
            "properties": properties,
            "timestamp": time.time(),
        }
        self.messages.append(message)

        # Route to bound queues
        for queue_name, bindings in self.bindings.items():
            for binding in bindings:
                if binding["exchange"] == exchange and self._matches_routing_key(
                    binding["routing_key"], routing_key
                ):
                    if queue_name not in self.queues:
                        self.queues[queue_name] = []
                    self.queues[queue_name].append(message)

    def bind_queue(self, queue, exchange, routing_key):
        """Mock queue binding."""
        if queue not in self.bindings:
            self.bindings[queue] = []
        self.bindings[queue].append({"exchange": exchange, "routing_key": routing_key})

    def _matches_routing_key(self, pattern, key):
        """Simple routing key matching (supports exact match and # wildcard)."""
        if pattern == key:
            return True
        if pattern.endswith("#"):
            prefix = pattern[:-1]
            return key.startswith(prefix)
        return False

    def get_messages(self, routing_key_pattern=None):
        """Get messages matching routing key pattern."""
        if routing_key_pattern is None:
            return self.messages
        return [
            msg
            for msg in self.messages
            if self._matches_routing_key(routing_key_pattern, msg["routing_key"])
        ]


@pytest.fixture
def mock_rabbitmq():
    """Mock RabbitMQ instance for integration testing."""
    return MockRabbitMQ()
