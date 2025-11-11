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
    # Stripe success via fake module
    fake_stripe_module.process_stripe_payment = lambda **kw: {
        "success": True,
        "payment_intent_id": "pi_it_ok",
        "client_secret": "cs",
    }

    incident_id = str(uuid.uuid4())
    payload = {
        "incident_id": incident_id,
        "patient_id": "P123",
        "amount": 123.45,
    }

    _publish("cmd.billing.initiate", payload, corr_id=incident_id)

    # Bind and wait for the completed event
    msg = bind_and_get_one("event.billing.completed", timeout_s=8.0)
    assert msg is not None, "Did not receive event.billing.completed"
    assert msg["incident_id"] == incident_id
    assert msg["status"] == "COMPLETED"
    assert float(msg["amount"]) == 123.45


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
    assert msg.get("status") == "INSURANCE_NOT_FOUND"
    assert msg.get("incident_id") == incident_id
