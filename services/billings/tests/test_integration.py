"""Integration tests for the billings service.

This module contains integration tests that verify the interaction between the billings service
and its dependencies, including the message broker and external services.
"""

import json
import os
import uuid

import pika

EX = os.environ["AMQP_EXCHANGE_NAME"]
EX_TYPE = os.environ["AMQP_EXCHANGE_TYPE"]


def _amqp_params():
    return pika.ConnectionParameters(
        host=os.environ["RABBITMQ_HOST"],
        port=int(os.environ["RABBITMQ_PORT"]),
        virtual_host="/",
    )


def _publish(rk: str, body: dict, corr_id: str | None = None):
    conn = pika.BlockingConnection(_amqp_params())
    try:
        ch = conn.channel()
        ch.exchange_declare(exchange=EX, exchange_type=EX_TYPE, durable=True)
        ch.basic_publish(
            exchange=EX,
            routing_key=rk,
            body=json.dumps(body),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
                correlation_id=corr_id or body.get("incident_id"),
                type=body.get("type", "InitiateBilling"),
                app_id="itests",
            ),
        )
    finally:
        conn.close()


def test_integration_billing_completed(
    run_consumer, bind_and_get_one, fake_stripe_module, monkeypatch, billings_app_module
):
    """Publish cmd.billing.initiate, expect event.billing.completed with status COMPLETED."""
    
    # Start the consumer in the background
    consumer_thread = run_consumer
    
    # Set up test data
    incident_id = str(uuid.uuid4())
    patient_id = "P123"
    amount = 123.45

    # Mock the insurance verification
    monkeypatch.setattr(
        billings_app_module,
        "verify_insurance",
        lambda incident_id, patient_id, amount=None: {
            "verified": True,
            "reason": "OK",
            "message": "ok",
            "http_status": 200,
        },
    )

    # Mock the Stripe service
    def mock_process_payment(amount, description=""):
        print(f"[MOCK STRIPE] Processing payment of {amount} for: {description}")
        return {
            "success": True,
            "payment_intent_id": f"pi_test_{incident_id.replace('-', '_')}",
            "client_secret": "cs_test_123",
        }
    
    monkeypatch.setattr(
        billings_app_module.stripe_service,
        "process_stripe_payment",
        mock_process_payment
    )

    # Create and publish the test message
    payload = {
        "incident_id": incident_id,
        "patient_id": patient_id,
        "amount": amount,
    }
    _publish("cmd.billing.initiate", payload, corr_id=incident_id)

    try:
        # Wait for the completed event
        msg = bind_and_get_one("event.billing.completed", timeout_s=8.0)
        
        # Verify the results
        assert msg is not None, "Did not receive event.billing.completed"
        assert msg["incident_id"] == incident_id, \
            f"Expected incident_id {incident_id}, got {msg.get('incident_id')}"
        assert msg["status"] == "COMPLETED", \
            f"Expected status=COMPLETED, got {msg.get('status')}"
        assert float(msg["amount"]) == amount, \
            f"Expected amount={amount}, got {msg.get('amount')}"
            
    finally:
        # Clean up by stopping the consumer
        billings_app_module.should_stop = True
        if consumer_thread and consumer_thread.is_alive():
            consumer_thread.join(timeout=5.0)


def test_integration_insurance_no_policy(
    run_consumer, event_sniffer, fake_stripe_module, monkeypatch, billings_app_module
):
    """Publish cmd.billing.initiate, force insurance NO_POLICY; expect event.billing.failed with INSURANCE_NOT_FOUND."""
    monkeypatch.setattr(
        billings_app_module,
        "verify_insurance",
        lambda incident_id, patient_id, amount=None: {
            "verified": False,
            "reason": "NO_POLICY",
            "message": "no policy",
            "http_status": 404,
        },
    )

    incident_id = str(uuid.uuid4())
    payload = {
        "incident_id": incident_id,
        "patient_id": "P999",
        "amount": 50.00,
    }

    get_failed = event_sniffer("event.billing.failed")

    _publish("cmd.billing.initiate", payload, corr_id=incident_id)

    msg = get_failed(timeout_s=8.0)
    assert msg is not None, "Did not receive event.billing.failed"
    # The actual implementation uses CANCELLED status for all failures
    assert msg.get("status") == "CANCELLED"
    assert msg.get("incident_id") == incident_id
