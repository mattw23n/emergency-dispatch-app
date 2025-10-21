import time
from os import environ

import pika


class AMQPSetup:
    def __init__(self):
        self.hostname = environ.get("RABBITMQ_HOST") or "localhost"
        self.port = int(environ.get("RABBITMQ_PORT") or 5672)
        self.connection = None
        self.channel = None
        self.queue_name = "Billings"
        self.exchange_name = "amqp.topic"
        self.exchange_type = "topic"
        self.routing_key = "billing.*"

    def connect(self):
        print(
            f"Attempting to connect to RabbitMQ at {
                self.hostname}:{
                self.port}"
        )
        parameters = pika.ConnectionParameters(
            host=self.hostname,
            port=self.port,
            heartbeat=600,
            blocked_connection_timeout=300,
        )

        connected = False
        start_time = time.time()
        max_retry_time = 60

        print("Connecting to RabbitMQ...")

        while not connected:
            try:
                self.connection = pika.BlockingConnection(parameters)
                self.channel = self.connection.channel()
                connected = True
                print("Successfully connected to RabbitMQ!")
                self.setup()
                print("AMQP setup completed successfully!")
            except pika.exceptions.AMQPConnectionError as e:
                print(f"Connection failed: {e}")
                if time.time() - start_time > max_retry_time:
                    print("Max retry time exceeded. Exiting.")
                    exit(1)
                print("Retrying in 2 seconds...")
                time.sleep(2)

    def setup(self):
        if not self.channel or not self.channel.is_open:
            self.connect()

        # Declare Exchange
        print(f"Creating exchange: {self.exchange_name}")
        self.channel.exchange_declare(
            exchange=self.exchange_name, exchange_type=self.exchange_type, durable=True
        )
        print(f"Exchange {self.exchange_name} created successfully!")

        # Declare Queue
        print(f"Creating queue: {self.queue_name}")
        self.channel.queue_declare(queue=self.queue_name, durable=True)
        print(f"Queue {self.queue_name} created successfully!")

        # Set binding for the Queue
        print(
            f"Binding queue {
                self.queue_name} to exchange {
                self.exchange_name}"
        )
        self.channel.queue_bind(
            exchange=self.exchange_name,
            queue=self.queue_name,
            routing_key=self.routing_key,
        )
        print("Queue binding completed successfully!")

    def publish_notification(self, message, routing_key):
        """Publish a notification to the exchange.

        Args:
            message: The message to send (will be converted to JSON)
            routing_key: The routing key to use for the message
        """
        try:
            if not self.channel or not self.channel.is_open:
                self.connect()

            self.channel.basic_publish(
                exchange=self.exchange_name,
                routing_key=routing_key,
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Make message persistent
                    content_type="application/json",
                ),
            )
            print(f"[NOTIFICATION] Published to {routing_key}: {message}")
            return True

        except Exception as e:
            print(f"[ERROR] Failed to publish notification: {str(e)}")
            return False

    def close(self):
        if self.connection and self.connection.is_open:
            self.connection.close()
            print("RabbitMQ connection closed.")


# Create a singleton instance
amqp_setup = AMQPSetup()
