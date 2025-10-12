import pytest
import pika
import time
import subprocess
import json
from jsonschema import validate, ValidationError

RABBITMQ_HOST = 'localhost'
SIMULATOR_URL = 'http://localhost:5000'

# A formal definition of your data contract
WEARABLE_DATA_SCHEMA = {
    "type": "object",
    "properties": {
        "userId": {"type": "string"},
        "device": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "model": {"type": "string"}},
            "required": ["id", "model"]
        },
        "schemaVersion": {"type": "string"},
        "timestampMs": {"type": "integer"},
        "metrics": {
            "type": "object",
            "properties": {
                "heartRateBpm": {"type": "integer"},
                "spO2Percentage": {"type": "number"},
                "respirationRateBpm": {"type": "integer"},
                "bodyTemperatureCelsius": {"type": "number"},
                "stepsSinceLastReading": {"type": "integer"}
            },
            "required": [
                "heartRateBpm", "spO2Percentage", "respirationRateBpm",
                "bodyTemperatureCelsius", "stepsSinceLastReading"
            ]
        }
    },
    "required": ["userId", "device", "schemaVersion", "timestampMs", "metrics"]
}

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
    """Fixture to run the app.py script in the background."""
    process = subprocess.Popen(['python', 'src/app.py', 'normal'])
    time.sleep(3) # Give the app time to start and connect
    yield process
    process.terminate()
    process.wait()

@pytest.mark.integration
def test_message_publishing_and_schema(rabbitmq_connection, simulator_process):
    """
    Integration test: verifies that a message is published to RabbitMQ
    and that its structure conforms to the defined schema.
    """
    channel = rabbitmq_connection.channel()
    exchange_name = 'amqp.topic'
    routing_key = 'wearable.data'
    
    channel.exchange_declare(exchange=exchange_name, exchange_type='topic')
    result = channel.queue_declare(queue='', exclusive=True)
    queue_name = result.method.queue
    channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=routing_key)
    
    print(f"Test queue '{queue_name}' is waiting for a message...")
    
    # Wait up to 10 seconds to get one message from the queue.
    body = None
    for _ in range(10):
        _, _, body = channel.basic_get(queue=queue_name, auto_ack=True)
        if body:
            break
        time.sleep(1)

    assert body is not None, "Did not receive any message from RabbitMQ in time"

    try:
        message_data = json.loads(body)
        validate(instance=message_data, schema=WEARABLE_DATA_SCHEMA)
        print("SUCCESS: Received message conforms to the JSON schema.")
    except json.JSONDecodeError:
        pytest.fail("Received message is not valid JSON")
    except ValidationError as e:
        pytest.fail(f"Schema validation failed: {e.message}")