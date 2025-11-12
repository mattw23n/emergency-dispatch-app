import json
import os
import sys
import threading
import time
from typing import Any, Dict
from json import JSONDecodeError

import pika


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"Missing required env: {name}", file=sys.stderr)
        sys.exit(1)
    return v


class AMQPSetup:
    # scenario 1: triage-related
    RK_TRIAGE_ABNORMAL = "triage.status.abnormal"
    RK_TRIAGE_EMERGENCY = "triage.status.emergency"
    RK_SEND_ALERT = "cmd.notification.send_alert"
    RK_DISPATCH_REQUEST = "cmd.dispatch.request_ambulance"

    Q_TRIAGE_ACTIONABLE = "events-manager.q.triage-actionable"

    # scenario 2: dispatch-related
    RK_DISPATCH_UNIT_ASSIGNED = "event.dispatch.unit_assigned"
    RK_DISPATCH_ENROUTE = "event.dispatch.enroute"  # optional but useful
    RK_DISPATCH_PATIENT_ONBOARD = "event.dispatch.patient_onboard"
    RK_DISPATCH_ARRIVED = "event.dispatch.arrived_at_hospital"

    Q_DISPATCH_STATUS = "events-manager.q.dispatch-status"

    _TEMPLATE_BY_RK = {
        RK_DISPATCH_UNIT_ASSIGNED: "DISPATCH_UNIT_ASSIGNED",
        RK_DISPATCH_ENROUTE: "DISPATCH_ENROUTE",
        RK_DISPATCH_PATIENT_ONBOARD: "DISPATCH_PATIENT_ONBOARD",
        RK_DISPATCH_ARRIVED: "DISPATCH_ARRIVED_AT_HOSPITAL",
    }

    # scenario 3: billing-related
    RK_BILLING_INITIATE = "cmd.billing.initiate"
    RK_BILLING_COMPLETED = "event.billing.completed"
    RK_BILLING_FAILED = "event.billing.failed"

    Q_BILLING_STATUS = "events-manager.q.billing-status"

    _TEMPLATE_BILLING_BY_RK = {
        RK_BILLING_COMPLETED: "BILLING_COMPLETED",
        RK_BILLING_FAILED: "BILLING_FAILED",
    }

    _billing_initiated_incidents: set[str] = set()
    _dedup_lock = threading.Lock()
    _closing = False

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
        # Scenario 1: queue/bindings
        ch.queue_declare(queue=self.Q_TRIAGE_ACTIONABLE, durable=True, arguments={"x-single-active-consumer": True})
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

        # Scenario 2 queue/bindings
        ch.queue_declare(queue=self.Q_DISPATCH_STATUS, durable=True, arguments={"x-single-active-consumer": True})
        ch.queue_bind(
            queue=self.Q_DISPATCH_STATUS,
            exchange=self.exchange_name,
            routing_key=self.RK_DISPATCH_UNIT_ASSIGNED,
        )
        ch.queue_bind(
            queue=self.Q_DISPATCH_STATUS,
            exchange=self.exchange_name,
            routing_key=self.RK_DISPATCH_ENROUTE,
        )
        ch.queue_bind(
            queue=self.Q_DISPATCH_STATUS,
            exchange=self.exchange_name,
            routing_key=self.RK_DISPATCH_PATIENT_ONBOARD,
        )
        ch.queue_bind(
            queue=self.Q_DISPATCH_STATUS,
            exchange=self.exchange_name,
            routing_key=self.RK_DISPATCH_ARRIVED,
        )

        # Scenario 3 queue/bindings
        ch.queue_declare(queue=self.Q_BILLING_STATUS, durable=True, arguments={"x-single-active-consumer": True})
        ch.queue_bind(
            queue=self.Q_BILLING_STATUS,
            exchange=self.exchange_name,
            routing_key=self.RK_BILLING_COMPLETED,
        )
        ch.queue_bind(
            queue=self.Q_BILLING_STATUS,
            exchange=self.exchange_name,
            routing_key=self.RK_BILLING_FAILED,
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
                "command": "request_ambulance",
                "location": payload.get("location"),
                "reason": "TRIAGE_EMERGENCY",
            },
            incident_id,
        )

    def publish_dispatch_status_alert(self, routing_key: str, payload: dict) -> None:
        incident_id = payload["incident_id"]
        patient_id = payload["patient_id"]
        template = self._TEMPLATE_BY_RK.get(routing_key)
        if not template:
            return
        vars_obj = {
            "unit_id": payload.get("unit_id"),
            "patient_id": patient_id,
            "status": payload.get("status"),
            "eta_minutes": payload.get("eta_minutes"),
            "location": payload.get("location"),
            "dest_hospital_id": payload.get("dest_hospital_id")
            or payload.get("hospital_id"),
            "ts": payload.get("ts"),
        }
        vars_obj = {k: v for k, v in vars_obj.items() if v is not None}
        self.publish(
            self.RK_SEND_ALERT,
            {
                "type": "SendAlert",
                "incident_id": incident_id,
                "template": template,
                "vars": vars_obj,
            },
            incident_id,
        )

    def publish_initiate_billing(self, dispatch_payload: dict) -> None:
        """
        Publish a single, idempotent billing initiation command.
        Relies on incident_id uniqueness; Billing should be idempotent on incident_id too.
        """
        incident_id = dispatch_payload["incident_id"]
        with self._dedup_lock:
            if incident_id in self._billing_initiated_incidents:
                return
            self._billing_initiated_incidents.add(incident_id)
            # NOTE: consider TTL/cleanup if incidents can be large in number.

        self.publish(
            self.RK_BILLING_INITIATE,
            {
                "type": "InitiateBilling",
                "incident_id": incident_id,
                "patient_id": dispatch_payload.get("patient_id"),
                "hospital_id": dispatch_payload.get("dest_hospital_id")
                or dispatch_payload.get("hospital_id"),
                "summary": {
                    "unit_id": dispatch_payload.get("unit_id"),
                    "arrived_ts": dispatch_payload.get("ts"),
                },
            },
            incident_id,
        )

    def publish_billing_status_alert(self, routing_key: str, payload: dict) -> None:
        """
        Optional: keep NOK informed via Notification; if you prefer Notification
        to read billing.* directly, you can remove this to avoid duplicates.
        """
        incident_id = payload["incident_id"]
        template = self._TEMPLATE_BILLING_BY_RK.get(routing_key)
        if not template:
            return
        vars_obj = {
            "billing_id": payload.get("billing_id"),
            "status": payload.get("status"),
            "amount": payload.get("amount"),
            "patient_id": payload.get("patient_id"),
        }
        vars_obj = {k: v for k, v in vars_obj.items() if v is not None}
        self.publish(
            self.RK_SEND_ALERT,
            {
                "type": "SendAlert",
                "incident_id": incident_id,
                "template": template,
                "vars": vars_obj,
            },
            incident_id,
        )

    def start_consumers(self):
        ch = self._ch()
        ch.basic_consume(
            queue=self.Q_TRIAGE_ACTIONABLE,
            on_message_callback=self._on_triage_status,
            auto_ack=False,
        )
        ch.basic_consume(
            queue=self.Q_DISPATCH_STATUS,
            on_message_callback=self._on_dispatch_update,
            auto_ack=False,
        )
        ch.basic_consume(
            queue=self.Q_BILLING_STATUS,
            on_message_callback=self._on_billing_status,
            auto_ack=False,
        )
        print(
            "Events Manager consuming: triage actionable + dispatch status + billing status"
        )
        try:
            ch.start_consuming()
        except (
            pika.exceptions.StreamLostError,
            pika.exceptions.ConnectionClosed,
            pika.exceptions.AMQPConnectionError,
            IndexError,
        ) as e:
            if self._closing:
                print("Consumers stopped cleanly.")
            else:
                print(f"Consumer loop error: {e}", file=sys.stderr)
        except KeyboardInterrupt:
            pass

    def _on_triage_status(self, ch, method, properties, body):
        try:
            payload = json.loads(body)
            incident_id = payload["incident_id"]
            status = payload["status"]

            self.publish_alert(incident_id, status, payload)
            self.publish_dispatch_request_if_emergency(incident_id, status, payload)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except (KeyError, JSONDecodeError) as e:
            print(f"Bad message ({e}); dropping.", file=sys.stderr)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        except Exception as e:
            print(f"Handler error: {e}", file=sys.stderr)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    def _on_dispatch_update(self, ch, method, properties, body):
        try:
            payload = json.loads(body)

            self.publish_dispatch_status_alert(method.routing_key, payload)

            if method.routing_key == self.RK_DISPATCH_ARRIVED:
                self.publish_initiate_billing(payload)

            ch.basic_ack(delivery_tag=method.delivery_tag)
        except (KeyError, JSONDecodeError) as e:
            print(f"Bad dispatch message ({e}); dropping.", file=sys.stderr)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception as e:
            print(f"Dispatch handler error: {e}", file=sys.stderr)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    def _on_billing_status(self, ch, method, properties, body):
        try:
            payload = json.loads(body)
            # Optionally notify NOK via templates (can be removed if Notification listens directly)
            self.publish_billing_status_alert(method.routing_key, payload)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except (KeyError, JSONDecodeError) as e:
            print(f"Bad billing message ({e}); dropping.", file=sys.stderr)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception as e:
            print(f"Billing handler error: {e}", file=sys.stderr)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    # -------- utils --------

    def _ch(self):
        if not self.channel or not self.channel.is_open:
            self.connect()
        return self.channel

    def close(self):
        """Graceful shutdown: stop consuming before closing socket (prevents StreamLostError)."""
        self._closing = True
        if self.connection and self.connection.is_open:
            try:
                if self.channel and self.channel.is_open:
                    try:
                        # stop the blocking start_consuming loop on the IO thread
                        self.connection.add_callback_threadsafe(
                            lambda: self.channel.stop_consuming()
                        )
                    except Exception:
                        pass
                time.sleep(0.1)
                self.connection.close()
            except Exception:
                pass
            print("RabbitMQ connection closed.")


# Create a singleton instance
amqp_setup = AMQPSetup()
