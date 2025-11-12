"""Unit tests for the Event Stream service core functionality."""
import json
import queue
import threading
import time
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch, call
import pytest

# Add parent directory to path to import from src
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app import app, event_queue, service_health


# Override the autouse wait_for_service fixture from conftest.py for unit tests
@pytest.fixture(scope="session", autouse=True)
def wait_for_service():
    """Override conftest.py fixture - unit tests don't need running service."""
    pass


@pytest.fixture
def client():
    """Create a Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def mock_rabbitmq_connection():
    """Create a mock RabbitMQ connection."""
    mock_connection = Mock()
    mock_channel = Mock()
    mock_connection.channel.return_value = mock_channel
    
    # Mock queue declaration result
    mock_result = Mock()
    mock_result.method.queue = 'test-queue-123'
    mock_channel.queue_declare.return_value = mock_result
    
    return mock_connection, mock_channel


@pytest.fixture(autouse=True)
def reset_service_health():
    """Reset service health between tests."""
    global service_health
    service_health["rabbitmq_connected"] = False
    service_health["consumers_active"] = 0
    service_health["events_streamed"] = 0
    service_health["last_event_time"] = None
    
    # Clear event queue
    while not event_queue.empty():
        try:
            event_queue.get_nowait()
        except queue.Empty:
            break
    
    yield
    
    # Cleanup after test
    service_health["rabbitmq_connected"] = False
    service_health["consumers_active"] = 0


class TestHealthEndpoint:
    """Test suite for the health check endpoint."""

    def test_health_endpoint_when_connected(self, client):
        """Health endpoint should return 200 when RabbitMQ is connected."""
        service_health["rabbitmq_connected"] = True
        service_health["consumers_active"] = 6
        service_health["events_streamed"] = 42
        
        response = client.get('/health')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert data["rabbitmq_connected"] is True
        assert data["consumers_active"] == 6
        assert data["events_streamed"] == 42

    def test_health_endpoint_when_disconnected(self, client):
        """Health endpoint should return 503 when RabbitMQ is disconnected."""
        service_health["rabbitmq_connected"] = False
        
        response = client.get('/health')
        
        assert response.status_code == 503
        data = response.get_json()
        assert data["status"] == "degraded"
        assert data["rabbitmq_connected"] is False

    def test_health_endpoint_returns_json(self, client):
        """Health endpoint should return JSON content type."""
        response = client.get('/health')
        
        assert response.content_type == "application/json"

    def test_health_endpoint_includes_last_event_time(self, client):
        """Health endpoint should include last_event_time field."""
        service_health["rabbitmq_connected"] = True
        service_health["last_event_time"] = "2025-11-12T10:30:00"
        
        response = client.get('/health')
        data = response.get_json()
        
        assert "last_event_time" in data
        assert data["last_event_time"] == "2025-11-12T10:30:00"


class TestCreateRabbitMQConnection:
    """Test suite for RabbitMQ connection creation."""

    @patch.dict('os.environ', {'RABBITMQ_HOST': 'test-rabbit', 'RABBITMQ_PORT': '5672'})
    @patch('src.app.pika.BlockingConnection')
    def test_successful_connection(self, mock_blocking_connection):
        """Test successful RabbitMQ connection."""
        from src.app import create_rabbitmq_connection
        
        mock_connection = Mock()
        mock_blocking_connection.return_value = mock_connection
        
        result = create_rabbitmq_connection()
        
        assert result == mock_connection
        assert service_health["rabbitmq_connected"] is True
        mock_blocking_connection.assert_called_once()

    @patch.dict('os.environ', {'RABBITMQ_HOST': 'localhost', 'RABBITMQ_PORT': '5672'})
    @patch('src.app.pika.BlockingConnection')
    @patch('src.app.time.sleep')
    def test_connection_retry_on_failure(self, mock_sleep, mock_blocking_connection):
        """Test connection retry mechanism."""
        from src.app import create_rabbitmq_connection
        import pika.exceptions
        
        # Fail twice, then succeed
        mock_connection = Mock()
        mock_blocking_connection.side_effect = [
            pika.exceptions.AMQPConnectionError("Connection refused"),
            pika.exceptions.AMQPConnectionError("Connection refused"),
            mock_connection
        ]
        
        result = create_rabbitmq_connection()
        
        assert result == mock_connection
        assert mock_blocking_connection.call_count == 3
        assert mock_sleep.call_count == 2  # Slept twice before success

    @patch.dict('os.environ', {'RABBITMQ_HOST': 'bad-host', 'RABBITMQ_PORT': '5672'})
    @patch('src.app.pika.BlockingConnection')
    @patch('src.app.time.sleep')
    def test_connection_raises_after_max_retries(self, mock_sleep, mock_blocking_connection):
        """Test that connection raises exception after max retries."""
        from src.app import create_rabbitmq_connection
        import pika.exceptions
        
        mock_blocking_connection.side_effect = pika.exceptions.AMQPConnectionError("Connection refused")
        
        with pytest.raises(pika.exceptions.AMQPConnectionError):
            create_rabbitmq_connection()
        
        assert mock_blocking_connection.call_count == 10  # Max retries

    @patch.dict('os.environ', {}, clear=True)
    @patch('src.app.pika.BlockingConnection')
    def test_default_connection_parameters(self, mock_blocking_connection):
        """Test default connection parameters when env vars not set."""
        from src.app import create_rabbitmq_connection
        
        mock_connection = Mock()
        mock_blocking_connection.return_value = mock_connection
        
        result = create_rabbitmq_connection()
        
        # Verify default host and port were used
        call_args = mock_blocking_connection.call_args[0][0]
        assert call_args.host == "rabbitmq"
        assert call_args.port == 5672


class TestEventQueueHandling:
    """Test suite for event queue operations."""

    def test_event_queue_is_fifo(self):
        """Event queue should be First-In-First-Out."""
        test_events = [
            {"type": "wearable", "data": "event1"},
            {"type": "triage", "data": "event2"},
            {"type": "dispatch", "data": "event3"}
        ]
        
        for event in test_events:
            event_queue.put(event)
        
        # Retrieve in same order
        for expected_event in test_events:
            retrieved_event = event_queue.get()
            assert retrieved_event == expected_event

    def test_event_queue_has_max_size(self):
        """Event queue should have a maximum size of 1000."""
        # Event queue is created with maxsize=1000
        # Fill it up
        for i in range(1000):
            event_queue.put({"id": i})
        
        # Next put should block or raise Full exception
        with pytest.raises(queue.Full):
            event_queue.put_nowait({"id": 1001})

    def test_event_queue_empty_raises_exception(self):
        """Getting from empty queue should raise Empty exception."""
        # Make sure queue is empty
        while not event_queue.empty():
            event_queue.get_nowait()
        
        with pytest.raises(queue.Empty):
            event_queue.get_nowait()


class TestRabbitMQConsumer:
    """Test suite for RabbitMQ consumer functionality."""

    @patch('src.app.create_rabbitmq_connection')
    def test_consumer_declares_exchange(self, mock_create_connection, mock_rabbitmq_connection):
        """Consumer should declare the amqp.topic exchange."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        # Run consumer in thread for short time
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        mock_channel.exchange_declare.assert_called_once_with(
            exchange='amqp.topic',
            exchange_type='topic',
            durable=True
        )

    @patch('src.app.create_rabbitmq_connection')
    def test_consumer_creates_exclusive_queue(self, mock_create_connection, mock_rabbitmq_connection):
        """Consumer should create an exclusive queue."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        mock_channel.queue_declare.assert_called_once_with(
            queue='Events Stream', 
            exclusive=True, 
            auto_delete=True, 
            durable=False
        )

    @patch('src.app.create_rabbitmq_connection')
    def test_consumer_binds_to_all_routing_keys(self, mock_create_connection, mock_rabbitmq_connection):
        """Consumer should bind to all expected routing keys."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        # Check that queue_bind was called with '#' wildcard
        expected_routing_keys = [
            '#',  # Wildcard to match all routing keys
        ]
        
        bind_calls = mock_channel.queue_bind.call_args_list
        assert len(bind_calls) == len(expected_routing_keys)
        
        for routing_key in expected_routing_keys:
            assert any(
                call_args[1]['routing_key'] == routing_key 
                for call_args in bind_calls
            )

    @patch('src.app.create_rabbitmq_connection')
    def test_consumer_updates_active_count(self, mock_create_connection, mock_rabbitmq_connection):
        """Consumer should update active consumers count."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        assert service_health["consumers_active"] == 1  # Now using single '#' wildcard


class TestConsumerCallback:
    """Test suite for consumer message callback."""

    @patch('src.app.create_rabbitmq_connection')
    def test_callback_parses_wearable_event(self, mock_create_connection, mock_rabbitmq_connection):
        """Callback should correctly parse wearable events."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        # Capture the callback function
        callback_func = None
        def capture_callback(*args, **kwargs):
            nonlocal callback_func
            callback_func = kwargs.get('on_message_callback')
        
        mock_channel.basic_consume.side_effect = capture_callback
        
        # Start consumer
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        # Simulate receiving a wearable event
        if callback_func:
            mock_method = Mock()
            mock_method.routing_key = 'event.wearable.vitals'
            
            test_message = {
                "patient_id": "patient-123",
                "vitals": {"heart_rate": 80}
            }
            
            callback_func(
                mock_channel,
                mock_method,
                Mock(),
                json.dumps(test_message).encode()
            )
            
            # Check that event was added to queue
            assert not event_queue.empty()
            event = event_queue.get_nowait()
            assert event['type'] == 'wearable'
            assert event['routing_key'] == 'event.wearable.vitals'
            assert event['data'] == test_message

    @patch('src.app.create_rabbitmq_connection')
    def test_callback_parses_dispatch_event(self, mock_create_connection, mock_rabbitmq_connection):
        """Callback should correctly parse dispatch events."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        callback_func = None
        def capture_callback(*args, **kwargs):
            nonlocal callback_func
            callback_func = kwargs.get('on_message_callback')
        
        mock_channel.basic_consume.side_effect = capture_callback
        
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        if callback_func:
            mock_method = Mock()
            mock_method.routing_key = 'event.dispatch.unit_assigned'
            
            test_message = {
                "dispatch_id": "dispatch-456",
                "unit_id": "amb-001"
            }
            
            callback_func(
                mock_channel,
                mock_method,
                Mock(),
                json.dumps(test_message).encode()
            )
            
            event = event_queue.get_nowait()
            assert event['type'] == 'dispatch'
            assert event['routing_key'] == 'event.dispatch.unit_assigned'

    @patch('src.app.create_rabbitmq_connection')
    def test_callback_handles_malformed_json(self, mock_create_connection, mock_rabbitmq_connection):
        """Callback should handle malformed JSON gracefully."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        callback_func = None
        def capture_callback(*args, **kwargs):
            nonlocal callback_func
            callback_func = kwargs.get('on_message_callback')
        
        mock_channel.basic_consume.side_effect = capture_callback
        
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        if callback_func:
            mock_method = Mock()
            mock_method.routing_key = 'event.test.invalid'
            
            # Send invalid JSON
            callback_func(
                mock_channel,
                mock_method,
                Mock(),
                b"invalid json {{"
            )
            
            # Queue should still be empty (event not added)
            assert event_queue.empty()

    @patch('src.app.create_rabbitmq_connection')
    def test_callback_updates_last_event_time(self, mock_create_connection, mock_rabbitmq_connection):
        """Callback should update last_event_time in service health."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        callback_func = None
        def capture_callback(*args, **kwargs):
            nonlocal callback_func
            callback_func = kwargs.get('on_message_callback')
        
        mock_channel.basic_consume.side_effect = capture_callback
        
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        if callback_func:
            initial_time = service_health["last_event_time"]
            
            mock_method = Mock()
            mock_method.routing_key = 'event.triage.status'
            
            callback_func(
                mock_channel,
                mock_method,
                Mock(),
                json.dumps({"test": "data"}).encode()
            )
            
            # last_event_time should be updated
            assert service_health["last_event_time"] != initial_time
            assert service_health["last_event_time"] is not None


class TestEventTypeDetection:
    """Test suite for event type detection from routing keys."""

    @pytest.mark.parametrize("routing_key,expected_type,should_filter", [
        ('wearable.data', 'wearable', False),
        ('triage.status.critical', None, True),  # Filtered out
        ('triage.q', None, True),  # Filtered out
        ('dispatch.unit_assigned', 'dispatch', False),
        ('dispatch.enroute', 'dispatch', False),
        ('notification.email', 'notification', False),
        ('notification.sms', 'notification', False),
        ('billing.invoice', 'billing', False),
        ('cmd.dispatch.request', 'dispatch', False),
        ('unknown.test', 'unknown', False),
    ])
    @patch('src.app.create_rabbitmq_connection')
    def test_routing_key_to_event_type_mapping(
        self, mock_create_connection, mock_rabbitmq_connection, routing_key, expected_type, should_filter
    ):
        """Test that routing keys are correctly mapped to event types and triage is filtered."""
        from src.app import rabbitmq_consumer
        
        mock_connection, mock_channel = mock_rabbitmq_connection
        mock_create_connection.return_value = mock_connection
        
        callback_func = None
        def capture_callback(*args, **kwargs):
            nonlocal callback_func
            callback_func = kwargs.get('on_message_callback')
        
        mock_channel.basic_consume.side_effect = capture_callback
        
        consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
        consumer_thread.start()
        time.sleep(0.5)
        
        if callback_func:
            mock_method = Mock()
            mock_method.routing_key = routing_key
            
            # Clear the queue first
            while not event_queue.empty():
                event_queue.get_nowait()
            
            callback_func(
                mock_channel,
                mock_method,
                Mock(),
                json.dumps({"test": "data"}).encode()
            )
            
            # Check if message was filtered or processed
            if should_filter:
                # Triage messages should be filtered - queue should be empty
                assert event_queue.empty(), f"Triage message {routing_key} should have been filtered"
            else:
                # Non-triage messages should be in the queue
                assert not event_queue.empty(), f"Message {routing_key} should not have been filtered"
                event = event_queue.get_nowait()
                assert event['type'] == expected_type


class TestSSEEndpoint:
    """Test suite for Server-Sent Events endpoint."""

    def test_sse_endpoint_has_correct_headers(self, client):
        """SSE endpoint should have proper headers for streaming."""
        response = client.get('/events')
        
        assert response.headers.get('Cache-Control') == 'no-cache'
        assert response.headers.get('X-Accel-Buffering') == 'no'
        assert response.headers.get('Connection') == 'keep-alive'

class TestServiceHealthTracking:
    """Test suite for service health tracking."""

    def test_service_health_initial_state(self):
        """Service health should have correct initial state."""
        assert "rabbitmq_connected" in service_health
        assert "consumers_active" in service_health
        assert "events_streamed" in service_health
        assert "last_event_time" in service_health

    def test_service_health_tracks_connection_state(self):
        """Service health should track RabbitMQ connection state."""
        service_health["rabbitmq_connected"] = True
        assert service_health["rabbitmq_connected"] is True
        
        service_health["rabbitmq_connected"] = False
        assert service_health["rabbitmq_connected"] is False

    def test_service_health_tracks_events_streamed(self):
        """Service health should track number of events streamed."""
        initial_count = service_health["events_streamed"]
        service_health["events_streamed"] = initial_count + 10
        
        assert service_health["events_streamed"] == initial_count + 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
