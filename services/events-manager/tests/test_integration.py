# tests/test_integration.py
import os
import json
import time
import pika
import app as events_app


# ---- robust connection params w/ retries to avoid race with broker startup ----
def _conn_params():
    return pika.ConnectionParameters(
        host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
        port=int(os.getenv("RABBITMQ_PORT", "5672")),
        virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
        credentials=pika.PlainCredentials(
            os.getenv("RABBITMQ_USER", "guest"),
            os.getenv("RABBITMQ_PASSWORD", "guest"),
        ),
        connection_attempts=12,  # ~6s
        retry_delay=0.5,
        socket_timeout=5,
        blocked_connection_timeout=5,
        heartbeat=0,
    )


def _declare_exchange(ch):
    ex = os.getenv("AMQP_EXCHANGE_NAME", "amqp.topic")
    ex_type = os.getenv("AMQP_EXCHANGE_TYPE", "topic")
    ch.exchange_declare(exchange=ex, exchange_type=ex_type, durable=True)
    return ex


def _tap_bind(ch, exchange, *routing_keys):
    q = ch.queue_declare(queue="", exclusive=True).method.queue
    for rk in routing_keys:
        ch.queue_bind(queue=q, exchange=exchange, routing_key=rk)
    return q


def _poll_basic_get(ch, queue, timeout=5.0, want=None, max_msgs=50):
    """
    Poll a queue; if want is set (set of rks), returns dict rk->(method,props,body) when satisfied.
    Otherwise returns the first (method,props,body) or (None,None,None) on timeout.
    """
    end = time.time() + timeout
    seen = {}
    while time.time() < end:
        method, props, body = ch.basic_get(queue=queue, auto_ack=True)
        if method:
            if want is None:
                return method, props, body
            seen[method.routing_key] = (method, props, body)
            if want.issubset(set(seen.keys())):
                return seen
            if len(seen) >= max_msgs:
                break
        else:
            time.sleep(0.1)
    return (None, None, None) if want is None else seen


# ---------------- Scenario 3 (kept) ----------------
def test_publish_initiate_billing_real_exchange(em_module):
    conn = pika.BlockingConnection(_conn_params())
    ch = conn.channel()
    ex = _declare_exchange(ch)

    q = _tap_bind(ch, ex, "cmd.billing.initiate")

    em = em_module.AMQPSetup()
    em.connect()

    dispatch_payload = {
        "incident_id": "it-int-001",
        "patient_id": "P999",
        "dest_hospital_id": "HOSP-X",
        "unit_id": "AMB-77",
        "ts": "2025-01-01T00:00:00Z",
    }
    em.publish_initiate_billing(dispatch_payload)

    method, props, body = _poll_basic_get(ch, q, timeout=5.0)
    assert method is not None, "did not receive cmd.billing.initiate"
    assert method.routing_key == "cmd.billing.initiate"
    got = json.loads(body)
    assert got["incident_id"] == "it-int-001"

    conn.close()


# ---------------- Scenario 1: triage -> alert (+ dispatch if emergency) ----------------
def test_s1_triage_abnormal_emits_alert_only(em_runner):
    # tap for EM output
    conn = pika.BlockingConnection(_conn_params())
    ch = conn.channel()
    ex = _declare_exchange(ch)
    q = _tap_bind(
        ch, ex, "cmd.notification.send_alert", "cmd.dispatch.request_ambulance"
    )

    # publish inbound triage abnormal
    triage_conn = pika.BlockingConnection(_conn_params())
    tch = triage_conn.channel()
    tch.exchange_declare(exchange=ex, exchange_type="topic", durable=True)
    payload = {
        "type": "TriageStatus",
        "incident_id": "s1-abn-001",
        "patient_id": "P123",
        "status": "abnormal",
        "metrics": {"hr": 150, "spo2": 90},
        "location": {"lat": 1.3, "lng": 103.8},
        "ts": "2025-01-01T00:00:00Z",
    }
    tch.basic_publish(
        exchange=ex,
        routing_key="triage.status.abnormal",
        body=json.dumps(payload),
        properties=pika.BasicProperties(
            content_type="application/json",
            correlation_id=payload["incident_id"],
            type="TriageStatus",
        ),
    )

    # expect alert; and specifically no dispatch command for abnormal
    end = time.time() + 3
    saw_alert = False
    saw_dispatch = False
    while time.time() < end:
        m, p, b = ch.basic_get(queue=q, auto_ack=True)
        if not m:
            time.sleep(0.1)
            continue
        if m.routing_key == "cmd.notification.send_alert":
            body = json.loads(b)
            if body.get("incident_id") == "s1-abn-001":
                assert body.get("template") == "TRIAGE_ABNORMAL"
                saw_alert = True
        elif m.routing_key == "cmd.dispatch.request_ambulance":
            # should not happen for abnormal
            saw_dispatch = True
    triage_conn.close()
    conn.close()
    assert saw_alert, "Expected SendAlert for abnormal triage"
    assert not saw_dispatch, "Did NOT expect dispatch request for abnormal triage"


def test_s1_triage_emergency_emits_alert_and_dispatch(em_runner):
    conn = pika.BlockingConnection(_conn_params())
    ch = conn.channel()
    ex = _declare_exchange(ch)
    q = _tap_bind(
        ch, ex, "cmd.notification.send_alert", "cmd.dispatch.request_ambulance"
    )

    triage_conn = pika.BlockingConnection(_conn_params())
    tch = triage_conn.channel()
    tch.exchange_declare(exchange=ex, exchange_type="topic", durable=True)
    payload = {
        "type": "TriageStatus",
        "incident_id": "s1-emg-001",
        "patient_id": "P456",
        "status": "emergency",
        "location": {"lat": 1.3, "lng": 103.8},
    }
    tch.basic_publish(
        exchange=ex,
        routing_key="triage.status.emergency",
        body=json.dumps(payload),
        properties=pika.BasicProperties(
            content_type="application/json",
            correlation_id=payload["incident_id"],
            type="TriageStatus",
        ),
    )

    seen = _poll_basic_get(
        ch,
        q,
        timeout=5.0,
        want={"cmd.notification.send_alert", "cmd.dispatch.request_ambulance"},
    )
    triage_conn.close()
    conn.close()

    assert "cmd.notification.send_alert" in seen
    assert "cmd.dispatch.request_ambulance" in seen

    # optional: check template
    _, _, body = seen["cmd.notification.send_alert"]
    body = json.loads(body)
    assert body.get("template") == "TRIAGE_EMERGENCY"
    assert body.get("incident_id") == "s1-emg-001"


# ---------------- Scenario 2: dispatch arrived -> alert + billing initiate -------------
def test_s2_dispatch_arrived_emits_alert_and_billing_initiate(em_runner):
    conn = pika.BlockingConnection(_conn_params())
    ch = conn.channel()
    ex = _declare_exchange(ch)
    q = _tap_bind(ch, ex, "cmd.notification.send_alert", "cmd.billing.initiate")

    dconn = pika.BlockingConnection(_conn_params())
    dch = dconn.channel()
    dch.exchange_declare(exchange=ex, exchange_type="topic", durable=True)

    payload = {
        "type": "DispatchStatus",
        "incident_id": "s2-arr-001",
        "unit_id": "AMB-12",
        "status": "arrived_at_hospital",
        "dest_hospital_id": "HOSP123",
        "patient_id": "P999",
        "ts": "2025-01-01T00:10:00Z",
    }
    dch.basic_publish(
        exchange=ex,
        routing_key="event.dispatch.arrived_at_hospital",
        body=json.dumps(payload),
        properties=pika.BasicProperties(
            content_type="application/json",
            correlation_id=payload["incident_id"],
            type="DispatchStatus",
        ),
    )

    seen = _poll_basic_get(
        ch, q, timeout=5.0, want={"cmd.notification.send_alert", "cmd.billing.initiate"}
    )
    dconn.close()
    conn.close()

    assert "cmd.notification.send_alert" in seen
    assert "cmd.billing.initiate" in seen

    # check alert template
    _, _, body = seen["cmd.notification.send_alert"]
    alert = json.loads(body)
    assert alert.get("template") in (
        "DISPATCH_ARRIVED_AT_HOSPITAL",
        "DISPATCH_ARRIVED_AT_HOSPITAL",
    )
    assert alert.get("incident_id") == "s2-arr-001"

    # check billing initiate payload
    _, _, b2 = seen["cmd.billing.initiate"]
    cmd = json.loads(b2)
    assert cmd["type"] == "InitiateBilling"
    assert cmd["incident_id"] == "s2-arr-001"
    assert cmd["patient_id"] == "P999"


def test_health_ok():
    """Test the health check endpoint returns 200 OK when service is healthy."""
    # Create a test client
    client = events_app.app.test_client()

    # Make a request to the health endpoint
    response = client.get("/health")

    # Verify the response
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["service"] == "events-manager"
