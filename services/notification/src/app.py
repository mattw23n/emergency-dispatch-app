import json
import threading
import pika
import time
from os import environ
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .send_sns import send_notification

# --- FastAPI Setup ---
app = FastAPI(title="Notification Service")

class Notification(BaseModel):
    patient_id: str
    subject: str
    message: str


# --- RabbitMQ Setup ---
hostname = environ.get('RABBITMQ_HOST') or 'localhost'
port = int(environ.get('RABBITMQ_PORT') or 5672)
exchange_name = "amqp.topic"
exchange_type = "topic"
queue_name = "Notification"
binding_key = "*.notify.#"

def connect_to_rabbitmq():
    """Retry RabbitMQ connection until successful."""
    parameters = pika.ConnectionParameters(host=hostname, port=port)
    while True:
        try:
            connection = pika.BlockingConnection(parameters)
            print(f"Connected to RabbitMQ at {hostname}:{port}")
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            print(f"RabbitMQ connection failed: {e}. Retrying in 2s...")
            time.sleep(2)


def setup_rabbitmq():
    """Declare exchange, queue, and binding."""
    connection = connect_to_rabbitmq()
    channel = connection.channel()
    channel.exchange_declare(exchange=exchange_name, exchange_type=exchange_type, durable=True)
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=binding_key)
    print(f"Exchange '{exchange_name}', queue '{queue_name}', and binding '{binding_key}' are set up.")
    return connection, channel


# --- Publisher ---
def publish_billing_complete(patient_id: str, subject: str, message: str):
    """Publish notification event to RabbitMQ topic."""
    connection = connect_to_rabbitmq()
    channel = connection.channel()
    channel.exchange_declare(exchange=exchange_name, exchange_type=exchange_type, durable=True)

    body = json.dumps({
        "patient_id": patient_id,
        "subject": subject,
        "message": message
    })

    routing_key = "notification.billings.complete"

    channel.basic_publish(
        exchange=exchange_name,
        routing_key=routing_key,
        body=body,
        properties=pika.BasicProperties(delivery_mode=2)
    )

    print(f"[x] Published to '{routing_key}': {body}")
    connection.close()


# --- Consumer ---
def consume_notifications():
    """Consume messages from *.notify.# and process them."""
    connection = connect_to_rabbitmq()
    channel = connection.channel()
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=binding_key)

    def callback(ch, method, properties, body):
        print(f"[x] Received message from {method.routing_key}: {body.decode()}")

    channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=True)
    print(f"[*] Waiting for messages on {queue_name}. To exit press CTRL+C.")
    channel.start_consuming()


@app.on_event("startup")
def startup_event():
    """Initialize RabbitMQ and start consumer in background."""
    setup_rabbitmq()
    threading.Thread(target=consume_notifications, daemon=True).start()
    print("Background consumer started.")


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/notify")
def notify(req: Notification):
    """
    Sends notification through SNS and publishes event to RabbitMQ.
    """
    try:
        msg_id = send_notification(req.patient_id, req.subject, req.message)
        publish_billing_complete(req.patient_id, req.subject, req.message)

        return {
            "status": "sent",
            "message_id": msg_id,
            "patient_id": req.patient_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending notification: {str(e)}")
