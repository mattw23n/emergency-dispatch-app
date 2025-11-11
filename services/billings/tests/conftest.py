"""Pytest configuration and fixtures for billings service tests.

This module provides test fixtures and configuration for the billings service,
including AMQP connection helpers, mock services, and test utilities.
"""

import json
import os
import sys
import threading
import time
from types import ModuleType

import pika
import pytest

# ---- Global test env defaults ----
os.environ.setdefault("RABBITMQ_HOST", "rabbitmq")
os.environ.setdefault("RABBITMQ_PORT", "5672")
os.environ.setdefault("AMQP_EXCHANGE_NAME", "amqp.topic")
os.environ.setdefault("AMQP_EXCHANGE_TYPE", "topic")

os.environ.setdefault("DB_HOST", "mysql")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_USER", "cs302")
os.environ.setdefault("DB_PASSWORD", "cs302pw")
os.environ.setdefault("DB_NAME", "cs302DB")

os.environ.setdefault("insurance_service_url_internal", "http://insurance:5200")
os.environ.setdefault("PORT", "5100")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")


# ---- AMQP helpers ----
def _amqp_params() -> pika.ConnectionParameters:
    """Create connection parameters for RabbitMQ.

    Returns:
        pika.ConnectionParameters: Configured connection parameters.
    """
    return pika.ConnectionParameters(
        host=os.environ["RABBITMQ_HOST"],
        port=int(os.environ["RABBITMQ_PORT"]),
        virtual_host="/",  # compose uses default vhost
        # Using default guest/guest; compose relaxes loopback restriction
    )


@pytest.fixture(scope="session")
def amqp_conn():
    """Create a session-scoped AMQP connection for testing.

    Yields:
        pika.BlockingConnection: Connection to RabbitMQ.

    Raises:
        RuntimeError: If connection cannot be established within timeout.
    """
    # Wait for broker just in case healthcheck hasn't fully opened port
    deadline = time.time() + 30
    last_err = None
    params = _amqp_params()
    while time.time() < deadline:
        try:
            conn = pika.BlockingConnection(params)
            yield conn
            conn.close()
            return
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    raise RuntimeError(f"Cannot connect to RabbitMQ: {last_err}")


@pytest.fixture
def amqp_channel(amqp_conn):
    """Create a test channel with exchange declared.

    Args:
        amqp_conn: Active AMQP connection fixture.

    Yields:
        pika.Channel: Configured channel with exchange declared.
    """
    ch = amqp_conn.channel()
    ch.exchange_declare(
        exchange=os.environ["AMQP_EXCHANGE_NAME"],
        exchange_type=os.environ["AMQP_EXCHANGE_TYPE"],
        durable=True,
    )
    yield ch
    try:
        ch.close()
    except Exception:
        pass


# ---- Stripe fake module (prevents real import & API calls) ----
# CHANGED: Make this session-scoped so it's available BEFORE billings_app_module imports
@pytest.fixture(scope="session")
def fake_stripe_module():
    """Create a fake stripe_service module for testing.

    Inserts a fake 'stripe_service' module into sys.modules so that
    billings.process_payment imports this fake one. You can customize
    return values per-test by setting attributes later.

    Returns:
        ModuleType: The fake stripe_service module.
    """
    fake = ModuleType("stripe_service")

    def _process_payment(amount, description=""):
        """Mock Stripe payment processing."""
        print(f"[FAKE STRIPE] Processing payment of {amount} for: {description}")
        return {
            "success": True,
            "client_secret": "cs_test_123",
            "payment_intent_id": "pi_test_123",
        }

    def _refund_payment(payment_reference):
        """Mock Stripe refund."""
        print(f"[FAKE STRIPE] Refunding payment: {payment_reference}")
        return {
            "success": True,
            "refund_id": "re_test_123",
        }

    fake.process_stripe_payment = _process_payment
    fake.refund_payment = _refund_payment

    # CRITICAL: Insert BEFORE app.py imports it
    sys.modules["stripe_service"] = fake

    yield fake

    # Cleanup after session
    if "stripe_service" in sys.modules:
        del sys.modules["stripe_service"]


# ---- Import app AFTER we set env and fakes ----
@pytest.fixture(scope="session")
def billings_app_module(fake_stripe_module):  # CHANGED: Add dependency
    """Provide the billings application module with test configuration.

    Args:
        fake_stripe_module: Ensures fake Stripe is loaded first.

    Returns:
        module: The imported billings app module.
    """
    # Import after env is set AND fake_stripe_module is in sys.modules
    import app as billings_app  # your billings app.py

    return billings_app


# ---- Start/stop the consumer in the background (integration tests) ----
@pytest.fixture
def run_consumer(billings_app_module):
    """Start and manage the billings consumer in a background thread.

    The consumer runs until the test completes, with its lifetime controlled
    by the global `should_stop` flag in the billings app module.

    Args:
        billings_app_module: The billings application module.
    """
    # Ensure fresh stop flag
    billings_app_module.should_stop = False
    t = threading.Thread(
        target=billings_app_module.consume, name="billings-consumer", daemon=True
    )
    t.start()
    # Give it a moment to connect and declare
    time.sleep(0.7)
    yield
    billings_app_module.should_stop = True
    # Let it exit its loop
    time.sleep(0.5)


@pytest.fixture
def event_sniffer(amqp_channel):
    """Create a function to capture AMQP messages for testing.

    The returned function can be used to wait for and capture messages
    published to a specific routing key.

    Args:
        amqp_channel: Active AMQP channel fixture.

    Returns:
        A function that takes a routing key and returns a message getter.

    Example:
        get_completed = event_sniffer("event.billing.completed")
        # ... publish ...
        msg = get_completed(timeout_s=8.0)
    """
    ex = os.environ["AMQP_EXCHANGE_NAME"]

    def start(bind_key: str):
        q = amqp_channel.queue_declare(queue="", exclusive=True).method.queue
        amqp_channel.queue_bind(queue=q, exchange=ex, routing_key=bind_key)

        def get(timeout_s: float = 8.0):
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                meth, props, body = amqp_channel.basic_get(queue=q, auto_ack=True)
                if meth:
                    try:
                        return json.loads(body.decode("utf-8"))
                    except Exception:
                        return {"_raw": body}
                time.sleep(0.2)
            return None

        return get

    return start


@pytest.fixture
def bind_and_get_one(amqp_channel):
    """Fixture to bind to a routing key and wait for a single message.

    Creates a temporary queue bound to the specified routing key and waits
    for a message to arrive on that queue.

    Args:
        amqp_channel: Active AMQP channel fixture.

    Returns:
        A function that takes a routing key and optional timeout.

    Example:
        # In test:
        message = bind_and_get_one("some.routing.key")
    """
    ex = os.environ["AMQP_EXCHANGE_NAME"]

    def _fn(bind_key: str, timeout_s: float = 8.0):
        # Bind an exclusive temp queue *before* the publish happens
        q = amqp_channel.queue_declare(queue="", exclusive=True).method.queue
        amqp_channel.queue_bind(queue=q, exchange=ex, routing_key=bind_key)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            meth, props, body = amqp_channel.basic_get(queue=q, auto_ack=True)
            if meth:
                try:
                    return json.loads(body.decode("utf-8"))
                except Exception:
                    return {"_raw": body}
            time.sleep(0.2)
        return None

    return _fn
