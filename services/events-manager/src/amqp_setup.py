import json
import os
import sys
import time
from typing import Any, Dict

import pika


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"Missing required env: {name}", file=sys.stderr)
        sys.exit(1)
    return v


class AMQPSetup:
    RK_TRIAGE_ABNORMAL = "triage.status.abnormal"
    RK_TRIAGE_EMERGENCY = "triage.status.emergency"
    RK_SEND_ALERT = "cmd.notification.send_alert"
    RK_DISPATCH_REQUEST = "cmd.dispatch.request_ambulance"

    # queue owned by Events Manager for triage actionable
    Q_TRIAGE_ACTIONABLE = "events-manager.q.triage-actionable"

    def __init__(self):
        self.hostname = _req("RABBITMQ_HOST")
        self.port = int(_req("RABBITMQ_PORT"))
        self.username = _req("RABBITMQ_USER")
        self.password = _req("RABBITMQ_PASSWORD")
        self.vhost = _req("RABBITMQ_VHOST")
        self.exchange_name = _req("AMQP_EXCHANGE_NAME")  # e.g. amqp.topic
        self.exchange_type = _req("AMQP_EXCHANGE_TYPE")  # e.g. topic

        self.connection: pika.BlockingConnection | None = None
        self.channel: pika.adapters.blocking_connection.BlockingChannel | None = None

    def connect(self, max_retry_time: int = 60):
        print(f"Attempting to connect to RabbitMQ at {self.hostname}:{self.port}")
        params = pika.ConnectionParameters(
            host=self.hostname,
            port=self.port,
            virtual_host=self.vhost,
            credentials=pika.PlainCredentials(self.username, self.password),
        )
        start = time.time()
        while True:
            try:
                self.connection = pika.BlockingConnection(params)
                self.channel = self.connection.channel()
                print("Successfully connected to RabbitMQ!")
                self.setup_topology()
                print("AMQP topology declared.")
                break
            except pika.exceptions.AMQPConnectionError as e:
                if time.time() - start > max_retry_time:
                    print(f"Max retry time exceeded: {e}", file=sys.stderr)
                    sys.exit(1)
                print("Connection failed. Retrying in 2s...")
                time.sleep(2)

    def setup_topology(self):
        ch = self._ch()
        ch.exchange_declare(
            exchange=self.exchange_name, exchange_type=self.exchange_type, durable=True
        )
        ch.queue_declare(queue=self.Q_TRIAGE_ACTIONABLE, durable=True)
        ch.queue_bind(
            queue=self.Q_TRIAGE_ACTIONABLE,
            exchange=self.exchange_name,
            routing_key=self.RK_TRIAGE_ABNORMAL,
        )
        ch.queue_bind(
            queue=self.Q_TRIAGE_ACTIONABLE,
            exchange=self.exchange_name,
            routing_key=self.RK_TRIAGE_EMERGENCY,
        )
        ch.basic_qos(prefetch_count=16)

    def publish(self, routing_key: str, body: Dict[str, Any], incident_id: str) -> None:
        ch = self._ch()
        ch.basic_publish(
            exchange=self.exchange_name,
            routing_key=routing_key,
            body=json.dumps(body),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,  # persistent
                correlation_id=incident_id,
                type=body.get("type", ""),
                app_id="events-manager",
            ),
            mandatory=False,
        )

    def publish_alert(self, incident_id: str, status: str, payload: dict) -> None:
        self.publish(
            self.RK_SEND_ALERT,
            {
                "type": "SendAlert",
                "incident_id": incident_id,
                "template": f"TRIAGE_{status.upper()}",
                "vars": {
                    "patient_id": payload.get("patient_id"),
                    "status": status,
                    "metrics": payload.get("metrics"),
                    "location": payload.get("location"),
                    "ts": payload.get("ts"),
                },
            },
            incident_id,
        )

    def publish_dispatch_request_if_emergency(
        self, incident_id: str, status: str, payload: dict
    ) -> None:
        if status != "emergency":
            return
        self.publish(
            self.RK_DISPATCH_REQUEST,
            {
                "type": "RequestAmbulance",
                "incident_id": incident_id,
                "patient_id": payload.get("patient_id"),
                "location": payload.get("location"),
                "reason": "TRIAGE_EMERGENCY",
            },
            incident_id,
        )

    def start_triage_consumer(self):
        """
        Consumes actionable triage statuses and orchestrates:
        - Always publishes SendAlert
        - Additionally publishes RequestAmbulance on emergency
        """
        ch = self._ch()
        ch.basic_consume(
            queue=self.Q_TRIAGE_ACTIONABLE,
            on_message_callback=self._on_triage_status,
            auto_ack=False,
        )
        print("Events Manager (Scenario 1) consuming actionable triage statuses...")
        try:
            ch.start_consuming()
        except KeyboardInterrupt:
            ch.stop_consuming()

    def _on_triage_status(self, ch, method, properties, body):
        try:
            payload = json.loads(body)
            # explicit validation + use the fields
            incident_id = payload["incident_id"]
            status = payload["status"]  # "abnormal" or "emergency"

            self.publish_alert(incident_id, status, payload)
            self.publish_dispatch_request_if_emergency(incident_id, status, payload)

            ch.basic_ack(delivery_tag=method.delivery_tag)
        except KeyError as e:
            print(f"Missing field {e}; dropping message.", file=sys.stderr)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception as e:
            print(f"Handler error: {e}", file=sys.stderr)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    # -------- utils --------

    def _ch(self):
        if not self.channel or not self.channel.is_open:
            self.connect()
        return self.channel

    def close(self):
        if self.connection and self.connection.is_open:
            self.connection.close()
            print("RabbitMQ connection closed.")


# Create a singleton instance
amqp_setup = AMQPSetup()
