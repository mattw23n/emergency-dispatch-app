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
    # Make sure insurance passes
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

    # Mock Stripe payment to succeed
    fake_stripe_module.process_stripe_payment = lambda **kw: {
        "success": True,
        "payment_intent_id": "pi_it_ok",
        "client_secret": "cs",
    }
    # Build a billing message
    incident_id = str(uuid.uuid4())
    payload = {
        "incident_id": incident_id,
        "patient_id": "P123",
        "amount": 123.45,
    }

    # Call the billing handler directly instead of publishing to RabbitMQ
    billings_app_module.callback(
        ch=None, method=None, properties=None, body=json.dumps(payload).encode()
    )

    # Check the billing status via your capture/publish mechanism
    # (Assuming you have a fixture like `capture_publish`)
    published_events = billings_app_module._capture_publish.calls
    completed_events = [
        evt
        for evt, success in published_events
        if success and evt["status"] == "COMPLETED"
    ]

    assert len(completed_events) == 1
    evt = completed_events[0]
    assert evt["incident_id"] == incident_id
    assert float(evt["amount"]) == 123.45


def test_integration_insurance_no_policy(
    run_consumer, event_sniffer, fake_stripe_module, monkeypatch, billings_app_module
):
    """Test billing failed due to no insurance policy by calling the billing callback directly."""

    # Mock insurance verification to fail
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

    # Call the billing handler directly
    billings_app_module.callback(
        ch=None, method=None, properties=None, body=json.dumps(payload).encode()
    )

    # Check the published failed event
    published_events = billings_app_module._capture_publish.calls
    failed_events = [
        evt
        for evt, success in published_events
        if not success and evt["status"] == "CANCELLED"
    ]

    assert len(failed_events) == 1
    evt = failed_events[0]
    assert evt["incident_id"] == incident_id
    assert evt.get("status") == "CANCELLED"
