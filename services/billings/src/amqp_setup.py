import json
import time
from os import environ

import pika


class AMQPSetup:
    def __init__(self):
        # Connection details (explicit vhost & creds to match compose)
        self.hostname = environ.get("RABBITMQ_HOST", "localhost")
        self.port = int(environ.get("RABBITMQ_PORT", "5672"))
        self.virtual_host = environ.get("RABBITMQ_VHOST", "/")
        self.user = environ.get("RABBITMQ_USER", "guest")
        self.password = environ.get("RABBITMQ_PASSWORD", "guest")

        # Exchange/queues (read from env for determinism)
        self.exchange_name = environ.get("AMQP_EXCHANGE_NAME", "amqp.topic")
        self.exchange_type = environ.get("AMQP_EXCHANGE_TYPE", "topic")
        self.queue_name = environ.get("AMQP_QUEUE_NAME", "Billings")

        # Routing keys
        self.initiate_routing_key = "cmd.billing.initiate"
        self.completed_routing_key = "event.billing.completed"
        self.failed_routing_key = "event.billing.failed"

        # Optional “fanout” status queue (harmless if no consumer)
        self.status_queue_name = environ.get(
            "AMQP_STATUS_QUEUE", "events-manager.q.billing-status"
        )

        self.connection = None
        self.channel = None

    def _conn_params(self) -> pika.ConnectionParameters:
        return pika.ConnectionParameters(
            host=self.hostname,
            port=self.port,
            virtual_host=self.virtual_host,
            credentials=pika.PlainCredentials(self.user, self.password),
            heartbeat=60,
            blocked_connection_timeout=30,
            client_properties={"connection_name": "billings-service"},
        )

    def connect(self, timeout_s: int = 60):
        print(f"Attempting to connect to RabbitMQ at {self.hostname}:{self.port}")
        params = self._conn_params()
        start = time.time()
        print("Connecting to RabbitMQ...")
        while True:
            try:
                self.connection = pika.BlockingConnection(params)
                self.channel = self.connection.channel()
                print("Successfully connected to RabbitMQ!")
                self.setup()
                print("AMQP setup completed successfully!")
                return
            except pika.exceptions.AMQPConnectionError as e:
                if time.time() - start > timeout_s:
                    raise RuntimeError(
                        f"Could not connect to RabbitMQ within {timeout_s}s: {e}"
                    )
                print(f"Connection failed: {e}; retrying in 2 seconds...")
                time.sleep(2)

    def setup(self):
        if not self.channel or not self.channel.is_open:
            raise RuntimeError("Channel not open; call connect() first.")

        # Exchange
        print(f"Creating exchange: {self.exchange_name}")
        self.channel.exchange_declare(
            exchange=self.exchange_name,
            exchange_type=self.exchange_type,
            durable=True,
        )
        print(f"Exchange {self.exchange_name} created successfully!")

        # Main consumer queue
        print(f"Creating queue: {self.queue_name}")
        self.channel.queue_declare(queue=self.queue_name, durable=True)
        print(f"Queue {self.queue_name} created successfully!")

        # Bind consumer queue to command key
        print(f"Binding queue {self.queue_name} to exchange {self.exchange_name}")
        self.channel.queue_bind(
            exchange=self.exchange_name,
            queue=self.queue_name,
            routing_key=self.initiate_routing_key,
        )

        # Optional durable status queue (so downstream can consume later)
        self.channel.queue_declare(queue=self.status_queue_name, durable=True)
        self.channel.queue_bind(
            exchange=self.exchange_name,
            queue=self.status_queue_name,
            routing_key=self.completed_routing_key,
        )
        self.channel.queue_bind(
            exchange=self.exchange_name,
            queue=self.status_queue_name,
            routing_key=self.failed_routing_key,
        )

        print("Queue bindings completed successfully!")

    def publish_status_update(self, message, is_success: bool = True) -> bool:
        """Publish billing status to topic exchange."""
        routing_key = (
            self.completed_routing_key if is_success else self.failed_routing_key
        )
        try:
            if not self.channel or not self.channel.is_open:
                self.connect()

            if isinstance(message, (bytes, bytearray)):
                body = bytes(message)
            elif isinstance(message, str):
                body = message.encode("utf-8")
            else:
                body = json.dumps(message).encode("utf-8")

            self.channel.basic_publish(
                exchange=self.exchange_name,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(
                    delivery_mode=2,  # persistent
                    content_type="application/json",
                    timestamp=int(time.time()),
                ),
                mandatory=False,
            )
            print(f"[NOTIFICATION] Published to {routing_key}: {body.decode('utf-8')}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to publish notification: {e}")
            return False

    def close(self):
        try:
            if self.channel and self.channel.is_open:
                self.channel.close()
        except Exception:
            pass
        if self.connection and self.connection.is_open:
            self.connection.close()
            print("RabbitMQ connection closed.")
