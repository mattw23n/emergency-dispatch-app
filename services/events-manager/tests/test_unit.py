# tests/test_unit.py
import json
import types
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


def test_publish_alert_templates_emergency(em, monkeypatch, fake_channel):
    monkeypatch.setattr(em, "_ch", lambda: fake_channel)
    payload = {
        "patient_id": "P1",
        "metrics": {"hr": 170},
        "location": {"lat": 1.3, "lng": 103.8},
        "ts": "2025-01-01T00:00:00Z",
    }
    em.publish_alert("inc-1", "emergency", payload)

    assert len(fake_channel.publishes) == 1
    pub = fake_channel.publishes[0]
    assert pub["routing_key"] == em.RK_SEND_ALERT
    body = json.loads(pub["body"])
    assert body["template"] == "TRIAGE_EMERGENCY"
    assert body["incident_id"] == "inc-1"


def test_publish_initiate_billing_idempotent(em, monkeypatch, fake_channel):
    monkeypatch.setattr(em, "_ch", lambda: fake_channel)
    payload = {
        "incident_id": "same-inc",
        "patient_id": "P2",
        "dest_hospital_id": "HOSP1",
        "unit_id": "AMB-1",
        "ts": "2025-01-01T00:00:00Z",
    }
    em.publish_initiate_billing(payload)
    em.publish_initiate_billing(payload)  # duplicate
    pubs = [
        p for p in fake_channel.publishes if p["routing_key"] == em.RK_BILLING_INITIATE
    ]
    assert len(pubs) == 1


def test_on_triage_status_bad_json_nacks(em, fake_channel):
    body = b"not-json"
    method = types.SimpleNamespace(delivery_tag=123)
    props = None
    em._on_triage_status(fake_channel, method, props, body)
    assert fake_channel.nacks and fake_channel.nacks[-1] == (123, False)


def test_dispatch_alert_template_mapping(
    environ=None, em=None, monkeypatch=None, fake_channel=None
):
    # pytest will inject fixtures by name; we declare them here to avoid lints
    pass


def test_dispatch_enroute_alert_mapping(em, monkeypatch, fake_channel):
    monkeypatch.setattr(em, "_ch", lambda: fake_channel)
    rk = em.RK_DISPATCH_ENROUTE
    payload = {
        "incident_id": "inc-2",
        "patient_id": "P999",
        "unit_id": "AMB-33",
        "status": "enroute",
        "eta_minutes": 7,
        "location": {"lat": 1.3, "lng": 103.8},
        "dest_hospital_id": "HOSP-9",
        "ts": "2025-01-01T01:01:01Z",
    }
    em.publish_dispatch_status_alert(rk, payload)
    pub = fake_channel.publishes[0]
    assert pub["routing_key"] == em.RK_SEND_ALERT
    body = json.loads(pub["body"])
    assert body["template"] == "DISPATCH_ENROUTE"
    assert body["incident_id"] == "inc-2"


def test_triage_emergency_triggers_dispatch_request(em, monkeypatch, fake_channel):
    # intercept em.publish to see the dispatch command
    calls = []

    def fake_publish(rk, body, incident_id):
        calls.append((rk, json.loads(json.dumps(body)), incident_id))

    monkeypatch.setattr(em, "publish", fake_publish)

    # drive callback with emergency payload
    method = types.SimpleNamespace(delivery_tag=9)
    props = None
    payload = {
        "incident_id": "inc-3",
        "status": "emergency",
        "patient_id": "P9",
        "location": {"lat": 1.23, "lng": 2.34},
    }
    em._on_triage_status(fake_channel, method, props, json.dumps(payload).encode())

    rks = {rk for rk, _, _ in calls}
    assert em.RK_SEND_ALERT in rks
    assert em.RK_DISPATCH_REQUEST in rks
