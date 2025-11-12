# tests/test_integration.py
import pytest
from fastapi.testclient import TestClient
from src.app import app
import pika
import time
import json
import subprocess
import requests


def test_app_startup_and_healthcheck():
    """Starts the real FastAPI app using Uvicorn and checks /health endpoint"""
    process = subprocess.Popen(
        ["uvicorn", "src.app:app", "--host", "127.0.0.1", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Allow some time for the app to fully start
    time.sleep(3)

    try:
        response = requests.get("http://127.0.0.1:8000/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        process.terminate()
        process.wait(timeout=5)


# Create a reusable test client for FastAPI (in-memory)
@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def call(client, path, method="GET", body=None):
    """Helper function to call API endpoints with JSON body and headers"""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if method == "POST":
        response = client.post(path, json=body, headers=headers)
    else:
        response = client.get(path, headers=headers)
    return {"json": response.json(), "code": response.status_code}


@pytest.mark.dependency()
def test_health(client):
    """Test that /health endpoint returns 200 OK"""
    result = call(client, "/health")
    assert result["code"] == 200
    assert result["json"] == {"status": "ok"}


# @pytest.mark.dependency(depends=["test_health"])
# def test_publish_notification(client):
#     """Integration test: verify /notify endpoint publishes notification successfully"""
#     body = {
#         "patient_id": "P001",
#         "subject": "Integration Test",
#         "message": "Hello from integration test"
#     }
#     result = call(client, '/notify', method='POST', body=body)
#     assert result['code'] == 200
#     assert "status" in result['json']


@pytest.mark.dependency(depends=["test_health"])
def test_rabbitmq_integration():
    """Integration test to verify RabbitMQ exchange, binding, and message delivery."""
    hostname = "rabbitmq"
    port = 5672
    exchange_name = "amqp.topic"
    exchange_type = "topic"
    queue_name = "Notification"
    binding_key = "*.notification.#"

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=hostname, port=port)
    )
    channel = connection.channel()

    channel.exchange_declare(
        exchange=exchange_name, exchange_type=exchange_type, durable=True
    )
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(
        exchange=exchange_name, queue=queue_name, routing_key=binding_key
    )

    test_message = {
        "template": "BILLING_COMPLETED",
        "vars": {"patient_id": "P001", "amount": 123.45, "status": "PAID"},
    }
    routing_key = "test.notification.billing"

    channel.basic_publish(
        exchange=exchange_name, routing_key=routing_key, body=json.dumps(test_message)
    )

    time.sleep(1)

    method_frame, header_frame, body = channel.basic_get(
        queue=queue_name, auto_ack=True
    )

    assert body is not None, "Expected a message in the Notification queue"
    body_data = json.loads(body.decode())
    assert body_data["template"] == "BILLING_COMPLETED"
    assert body_data["vars"]["status"] == "PAID"

    channel.close()
    connection.close()
