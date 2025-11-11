# app.py — Events Manager (Scenario 1)
import json
import os
import signal
import threading
from typing import Any, Dict, Optional

from flask import Flask, jsonify
import pika

from amqp_setup import amqp_setup  # your AMQPSetup singleton

DEV_TAP_OUTBOUND = os.environ.get("DEV_TAP_OUTBOUND") in {"1", "true", "True"}

_t_consumer: Optional[threading.Thread] = None
_t_tap: Optional[threading.Thread] = None
_tap_conn: Optional[pika.BlockingConnection] = None


def _start_consumer():
    amqp_setup.connect()
    amqp_setup.start_consumers()


def _publisher_params() -> pika.ConnectionParameters:
    """Create params for a short-lived, thread-local publisher connection."""
    return pika.ConnectionParameters(
        host=os.environ["RABBITMQ_HOST"],
        port=int(os.environ["RABBITMQ_PORT"]),
        virtual_host=os.environ["RABBITMQ_VHOST"],
        credentials=pika.PlainCredentials(
            os.environ["RABBITMQ_USER"], os.environ["RABBITMQ_PASSWORD"]
        ),
    )


def _publish_once(routing_key: str, body: Dict[str, Any], correlation_id: str):
    """Thread-safe one-shot publish (new connection/channel per call)."""
    ex = os.environ["AMQP_EXCHANGE_NAME"]
    ex_type = os.environ.get("AMQP_EXCHANGE_TYPE")

    conn = pika.BlockingConnection(_publisher_params())
    try:
        ch = conn.channel()
        ch.exchange_declare(exchange=ex, exchange_type=ex_type, durable=True)
        ch.basic_publish(
            exchange=ex,
            routing_key=routing_key,
            body=json.dumps(body),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
                correlation_id=correlation_id,
                type=body.get("type", ""),
                app_id="events-manager-sim",
            ),
        )
    finally:
        conn.close()


def _start_tap():
    """Dev helper: print what EM publishes."""
    global _tap_conn
    params = _publisher_params()  # same creds/host/vhost
    ex = os.environ["AMQP_EXCHANGE_NAME"]
    ex_type = os.environ.get("AMQP_EXCHANGE_TYPE")

    _tap_conn = pika.BlockingConnection(params)
    ch = _tap_conn.channel()
    ch.exchange_declare(exchange=ex, exchange_type=ex_type, durable=True)

    q = ch.queue_declare(queue="", exclusive=True).method.queue
    ch.queue_bind(queue=q, exchange=ex, routing_key="cmd.notification.send_alert")
    ch.queue_bind(queue=q, exchange=ex, routing_key="cmd.dispatch.request_ambulance")
    print(
        "[tap] listening on cmd.notification.send_alert + cmd.dispatch.request_ambulance"
    )
    ch.queue_bind(queue=q, exchange=ex, routing_key="cmd.billing.initiate")
    ch.queue_bind(queue=q, exchange=ex, routing_key="event.billing.*")
    print(
        "[tap] listening on cmd.notification.send_alert + cmd.dispatch.request_ambulance + cmd.billing.initiate + event.billing.*"
    )

    def _cb(ch_, method, props, body):
        print(
            f"\n[tap] rk={method.routing_key} corr_id={props.correlation_id} type={props.type}"
        )
        try:
            print(json.dumps(json.loads(body), indent=2))
        except Exception:
            print(body)

    ch.basic_consume(queue=q, on_message_callback=_cb, auto_ack=True)
    try:
        ch.start_consuming()
    except Exception:
        pass


def _should_start_worker() -> bool:
    wm = os.environ.get("WERKZEUG_RUN_MAIN")
    return True if wm is None else (wm == "true")


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(status="ok", service="events-manager"), 200

    @app.get("/status")
    def status():
        ready = bool(amqp_setup.connection and amqp_setup.connection.is_open)
        return jsonify(amqp_connected=ready), (200 if ready else 503)

    # Start background threads once
    if _should_start_worker():
        global _t_consumer, _t_tap
        if _t_consumer is None or not _t_consumer.is_alive():
            _t_consumer = threading.Thread(
                target=_start_consumer, name="amqp-consumer", daemon=True
            )
            _t_consumer.start()
        if DEV_TAP_OUTBOUND and (_t_tap is None or not _t_tap.is_alive()):
            _t_tap = threading.Thread(target=_start_tap, name="amqp-tap", daemon=True)
            _t_tap.start()

    def _graceful_shutdown(*_):
        try:
            if _tap_conn and _tap_conn.is_open:
                _tap_conn.close()
        except Exception:
            pass
        try:
            amqp_setup.close()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    return app


app = create_app()

if __name__ == "__main__":
    # nosemgrep: python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8011")), debug=False)
