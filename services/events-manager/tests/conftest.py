# tests/conftest.py
import os
import sys
import time
import importlib
import threading
import pytest

# ---- default env for tests (overridden by compose env) ----
DEFAULT_ENV = {
    "RABBITMQ_HOST": os.getenv("RABBITMQ_HOST", "rabbitmq"),
    "RABBITMQ_PORT": os.getenv("RABBITMQ_PORT", "5672"),
    "RABBITMQ_USER": os.getenv("RABBITMQ_USER", "guest"),
    "RABBITMQ_PASSWORD": os.getenv("RABBITMQ_PASSWORD", "guest"),
    "RABBITMQ_VHOST": os.getenv("RABBITMQ_VHOST", "/"),
    "AMQP_EXCHANGE_NAME": os.getenv("AMQP_EXCHANGE_NAME", "amqp.topic"),
    "AMQP_EXCHANGE_TYPE": os.getenv("AMQP_EXCHANGE_TYPE", "topic"),
}


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    for k, v in DEFAULT_ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def em_module():
    # ensure env is set BEFORE import
    if "amqp_setup" in sys.modules:
        del sys.modules["amqp_setup"]
    import amqp_setup  # noqa

    return importlib.reload(sys.modules["amqp_setup"])


@pytest.fixture
def em(em_module):
    return em_module.AMQPSetup()


# ------------------ fakes for unit tests ------------------


class FakeProps:
    def __init__(self, correlation_id=None, typ=None):
        self.correlation_id = correlation_id
        self.type = typ


class FakeMethod:
    def __init__(self, delivery_tag=1, routing_key=None):
        self.delivery_tag = delivery_tag
        self.routing_key = routing_key


class FakeChannel:
    def __init__(self):
        self.publishes = []
        self.acks = []
        self.nacks = []

    def basic_publish(self, exchange, routing_key, body, properties, mandatory=False):
        self.publishes.append(
            dict(
                exchange=exchange, routing_key=routing_key, body=body, props=properties
            )
        )

    def basic_ack(self, delivery_tag):
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue=False):
        self.nacks.append((delivery_tag, requeue))


@pytest.fixture
def fake_channel():
    return FakeChannel()


# ---------------- background consumer runner (integration) ----------------


@pytest.fixture
def em_runner(em_module):
    """
    Spins up Events Manager consumers in a background thread and tears down.
    """
    em = em_module.AMQPSetup()
    em.connect()

    t = threading.Thread(target=em.start_consumers, name="em-consumers", daemon=True)
    t.start()
    # small warm-up to let basic_consume register
    time.sleep(0.4)

    yield em

    # closing the connection unblocks start_consuming()
    try:
        em.close()
    except Exception:
        pass
    t.join(timeout=2)
