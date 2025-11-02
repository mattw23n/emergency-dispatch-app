import json
import os
import sys
import time
import pika
from typing import Any, Dict
from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"Missing required env: {name}", file=sys.stderr)
        sys.exit(1)
    return v


class AMQPSetup:
    # Output routing key for wearable data
    RK_WEARABLE_DATA = "wearable.data"

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

        # Declare the exchange
        ch.exchange_declare(
            exchange=self.exchange_name, exchange_type=self.exchange_type, durable=True
        )

    def publish_wearable_data(self, payload: Dict[str, Any]) -> None:
        """
        Publish wearable data to the exchange
        """
        self.publish(self.RK_WEARABLE_DATA, payload)

    def publish(self, routing_key: str, body: Dict[str, Any]) -> None:
        ch = self._ch()
        ch.basic_publish(
            exchange=self.exchange_name,
            routing_key=routing_key,
            body=json.dumps(body),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,  # persistent
                app_id="wearable-service",
            ),
            mandatory=False,
        )

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
