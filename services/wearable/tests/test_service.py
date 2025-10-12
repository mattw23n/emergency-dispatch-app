import pytest
import pika
import requests
import time
import subprocess
import json

RABBITMQ_HOST = 'localhost'
SIMULATOR_URL = 'http://localhost:5000'

@pytest.fixture(scope="module")
def rabbitmq_connection():
    """Fixture to provide a connection to RabbitMQ for the tests."""
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
        yield connection
        connection.close()
    except pika.exceptions.AMQPConnectionError:
        pytest.fail("Cannot connect to RabbitMQ. Is it running?")

@pytest.fixture
def simulator_process():
    """Fixture to run the wearable_simulator.py script in the background."""
    process = subprocess.Popen(['python', 'wearable_simulator.py', 'normal'])
    # Give the simulator a moment to start up and connect
    time.sleep(3)
    yield process
    # Teardown: stop the simulator process
    process.terminate()
    process.wait()

def test_health_check_endpoint(simulator_process):
    """
    Tests if the /health endpoint is up and returns a successful response.
    """
    try:
        response = requests.get(f'{SIMULATOR_URL}/health', timeout=5)
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'ok'
        assert data['rabbitmq_connected'] is True
        assert data['scenario'] == 'normal'
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Health check endpoint is not accessible: {e}")

def test_message_publishing(rabbitmq_connection, simulator_process):
    """
    Tests if the simulator is actually publishing messages to RabbitMQ.
    This is an integration test.
    """
    channel = rabbitmq_connection.channel()
    
    exchange_name = 'amqp.topic'
    channel.exchange_declare(exchange=exchange_name, exchange_type='topic')
    
    result = channel.queue_declare(queue='', exclusive=True)
    queue_name = result.method.queue
    
    routing_key = 'wearable.data'
    channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=routing_key)
    
    print(f"Test queue '{queue_name}' is waiting for a message...")
    
    # Try to get one message from the queue. Wait up to 10 seconds.
    # The simulator sends a message every 4 seconds, so this should be enough time.
    method_frame, header_frame, body = None, None, None
    for _ in range(10):
        method_frame, header_frame, body = channel.basic_get(queue=queue_name, auto_ack=True)
        if method_frame:
            break
        time.sleep(1)

    # Assert that we actually received a message
    assert method_frame is not None, "Did not receive any message from RabbitMQ"
    assert body is not None, "Received an empty message body"

    # Assert that the message content is valid JSON
    try:
        message_data = json.loads(body)
        print("Received message:", message_data)
        assert 'userId' in message_data
        assert 'device' in message_data
        assert 'schemaVersion' in message_data
        assert 'timestampMs' in message_data
        assert 'metrics' in message_data
        # Check that metrics contain expected keys
        assert 'heartRateBpm' in message_data['metrics']
        assert 'spO2Percentage' in message_data['metrics']
        assert 'respirationRateBpm' in message_data['metrics']
        assert 'bodyTemperatureCelsius' in message_data['metrics']
        assert 'stepsSinceLastReading' in message_data['metrics']
        
    except json.JSONDecodeError:
        pytest.fail("Received message is not valid JSON")
        