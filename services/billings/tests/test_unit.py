import datetime
import json
import types

import pytest

import app as billings_app


# ---- Dummy MySQL objects for unit tests (no real DB) ----
class DummyCursor:
    def __init__(self, dictionary=False):
        self.dictionary = dictionary
        self._lastrowid = 101
        self._executed = []

    @property
    def lastrowid(self):
        return self._lastrowid

    def execute(self, sql, params=None):
        self._executed.append((sql, params))

    def fetchone(self):
        # Return a row for "SELECT amount ..." paths if needed
        if self.dictionary:
            return {"amount": 100.00}
        return (1,)

    def close(self):
        pass


class DummyConn:
    def __init__(self):
        self._open = True
        self._cursor = DummyCursor()

    def cursor(self, dictionary=False, buffered=False):
        return DummyCursor(dictionary=dictionary)

    def commit(self):
        pass

    def is_connected(self):
        return self._open

    def close(self):
        self._open = False


@pytest.fixture
def fake_mysql_connect(monkeypatch):
    def _fake_connect(**kwargs):
        return DummyConn()
    import mysql
    import mysql.connector
    monkeypatch.setattr(mysql.connector, "connect", _fake_connect)
    return _fake_connect


@pytest.fixture
def capture_publish(monkeypatch):
    published = {"calls": []}

    def _fake_publish_status_update(message, is_success=True):
        # message may be dict or str in your code; normalize to dict
        if isinstance(message, str):
            try:
                message = json.loads(message)
            except Exception:
                message = {"_raw": message}
        published["calls"].append((message, is_success))
        return True

    monkeypatch.setattr(billings_app.amqp, "publish_status_update", _fake_publish_status_update)
    return published


def _initiate_msg(incident_id="inc-u-1", patient_id="P123", amount=100):
    return json.dumps({
        "incident_id": incident_id,
        "patient_id": patient_id,
        "amount": amount
    }).encode("utf-8")


def test_callback_success_paid(fake_mysql_connect, capture_publish, fake_stripe_module, monkeypatch):
    # Insurance OK
    monkeypatch.setattr(billings_app, "verify_insurance",
                        lambda incident_id, patient_id, amount=None: {"verified": True, "reason": "OK", "message": "ok", "http_status": 200})

    # Stripe returns success via fake module (already provided in conftest)
    fake_stripe_module.process_stripe_payment = lambda **kw: {
        "success": True, "payment_intent_id": "pi_test_OK", "client_secret": "cs"
    }

    # Call the callback directly
    billings_app.callback(ch=None, method=None, properties=None, body=_initiate_msg())

    # One publish, to event.billing.completed
    assert len(capture_publish["calls"]) == 1
    msg, is_success = capture_publish["calls"][0]
    assert is_success is True
    assert msg["status"] == "COMPLETED"
    assert msg["amount"] == 100


def test_callback_insurance_no_policy(fake_mysql_connect, capture_publish, fake_stripe_module, monkeypatch):
    # Insurance fails with NO_POLICY
    monkeypatch.setattr(billings_app, "verify_insurance",
                        lambda incident_id, patient_id, amount=None: {
                            "verified": False, "reason": "NO_POLICY", "message": "not found", "http_status": 404
                        })

    billings_app.callback(ch=None, method=None, properties=None, body=_initiate_msg())

    assert len(capture_publish["calls"]) == 1
    msg, is_success = capture_publish["calls"][0]
    assert is_success is False
    assert msg["status"] == "INSURANCE_NOT_FOUND"


def test_callback_payment_declined(fake_mysql_connect, capture_publish, fake_stripe_module, monkeypatch):
    # Insurance OK
    monkeypatch.setattr(billings_app, "verify_insurance",
                        lambda incident_id, patient_id, amount=None: {"verified": True, "reason": "OK", "message": "ok", "http_status": 200})

    # Stripe fails like "card was declined"
    def _decline(**kw):
        return {"success": False, "error": "Your card was declined", "payment_intent_id": None, "client_secret": None}
    fake_stripe_module.process_stripe_payment = _decline

    billings_app.callback(ch=None, method=None, properties=None, body=_initiate_msg())

    assert len(capture_publish["calls"]) == 1
    msg, is_success = capture_publish["calls"][0]
    assert is_success is False
    assert msg["status"] == "PAYMENT_DECLINED"
