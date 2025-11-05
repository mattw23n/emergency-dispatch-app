import json
import os
import sys
import threading
import time
from types import ModuleType
from typing import Callable

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
    return pika.ConnectionParameters(
        host=os.environ["RABBITMQ_HOST"],
        port=int(os.environ["RABBITMQ_PORT"]),
        virtual_host="/",  # compose uses default vhost
        # Using default guest/guest; compose relaxes loopback restriction
    )

@pytest.fixture(scope="session")
def amqp_conn():
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
@pytest.fixture
def fake_stripe_module(monkeypatch):
    """
    Inserts a fake 'stripe_service' module into sys.modules so that
    billings.process_payment imports this fake one.
    You can customize return values per-test by setting attributes later.
    """
    fake = ModuleType("stripe_service")

    def _ok(amount, currency="usd", description=""):
        # mirror your real return structure
        return {
            "success": True,
            "client_secret": "cs_test_123",
            "payment_intent_id": "pi_test_123",
        }

    fake.process_stripe_payment = _ok
    monkeypatch.setitem(sys.modules, "stripe_service", fake)
    return fake


# ---- Import app AFTER we set env and fakes ----
@pytest.fixture(scope="session")
def billings_app_module():
    # Import after env is set
    import app as billings_app  # your billings app.py
    return billings_app


# ---- Start/stop the consumer in the background (integration tests) ----
@pytest.fixture
def run_consumer(billings_app_module):
    """
    Start the billings consume() loop in a background thread.
    In tests we control its lifetime via the global `should_stop` flag.
    """
    # Ensure fresh stop flag
    billings_app_module.should_stop = False
    t = threading.Thread(target=billings_app_module.consume, name="billings-consumer", daemon=True)
    t.start()
    # Give it a moment to connect and declare
    time.sleep(0.7)
    yield
    billings_app_module.should_stop = True
    # Let it exit its loop
    time.sleep(0.5)

@pytest.fixture
def event_sniffer(amqp_channel):
    """
    Usage:
        get_completed = event_sniffer("event.billing.completed")  # binds now
        # ... publish ...
        msg = get_completed(timeout_s=8.0)  # polls same queue
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

