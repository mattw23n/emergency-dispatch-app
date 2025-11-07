import threading
import pika
import time
import json
from os import environ
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from src.send_sns import send_notification
import uvicorn

# --- FastAPI Setup ---
app = FastAPI(title="Notification Service")


class Notification(BaseModel):
    patient_id: str
    subject: str
    message: str


# --- RabbitMQ Setup ---
hostname = environ.get("RABBITMQ_HOST") or "localhost"
port = int(environ.get("RABBITMQ_PORT") or 5672)
exchange_name = "amqp.topic"
exchange_type = "topic"
queue_name = "Notification"
binding_key = "*.notification.#"


def connect_to_rabbitmq():
    """Retry RabbitMQ connection until successful."""
    parameters = pika.ConnectionParameters(host=hostname, port=port)
    while True:
        try:
            connection = pika.BlockingConnection(parameters)
            print(f"✅ Connected to RabbitMQ at {hostname}:{port}")
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            print(f"⚠️ RabbitMQ connection failed: {e}. Retrying in 2s...")
            time.sleep(2)


def setup_rabbitmq():
    """Declare exchange, queue, and binding."""
    connection = connect_to_rabbitmq()
    channel = connection.channel()
    channel.exchange_declare(
        exchange=exchange_name, exchange_type=exchange_type, durable=True
    )
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(
        exchange=exchange_name, queue=queue_name, routing_key=binding_key
    )
    print(
        f"Exchange '{exchange_name}', queue '{queue_name}', and binding '{binding_key}' set up."
    )
    return connection, channel


# # --- Publisher ---
# def publish_billing_complete(patient_id: str, subject: str, message: str):
#     """Publish notification event to RabbitMQ topic."""
#     connection = connect_to_rabbitmq()
#     channel = connection.channel()
#     channel.exchange_declare(exchange=exchange_name, exchange_type=exchange_type, durable=True)

#     body = json.dumps({
#         "patient_id": patient_id,
#         "subject": subject,
#         "message": message
#     })

#     routing_key = "notification.billings.complete"

#     channel.basic_publish(
#         exchange=exchange_name,
#         routing_key=routing_key,
#         body=body,
#         properties=pika.BasicProperties(delivery_mode=2)
#     )

#     print(f"[x] Published to '{routing_key}': {body}")
#     connection.close()


# --- Consumer ---
def consume_notifications():
    """Consume messages from *.notification.# and process them."""
    connection = connect_to_rabbitmq()
    channel = connection.channel()
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(
        exchange=exchange_name, queue=queue_name, routing_key=binding_key
    )

    def callback(ch, method, properties, body):
        print(f"[x] Received message from {method.routing_key}: {body.decode()}")

        try:
            data = json.loads(body.decode())

            msg_type = data.get("type")
            template = data.get("template")
            vars = data.get("vars", {})

            patient_id = vars.get("patient_id", "UNKNOWN")

            # Compose subject & message depending on template
            if template == "TRIAGE_EMERGENCY":
                subject = "Emergency Alert"
                message = (
                    f"Patient {patient_id} is in EMERGENCY!\n"
                    f"HR: {vars['metrics']['heartRateBpm']} bpm, "
                    f"SpO2: {vars['metrics']['spO2Percentage']}%, "
                    f"Temp: {vars['metrics']['bodyTemperatureCelsius']}°C"
                )

            elif template == "TRIAGE_ABNORMAL":
                subject = "Abnormal Vitals Alert"
                message = (
                    f"Patient {patient_id} data\n"
                    f"HR: {vars['metrics']['heartRateBpm']} bpm, "
                    f"SpO2: {vars['metrics']['spO2Percentage']}%, "
                    f"Temp: {vars['metrics']['bodyTemperatureCelsius']}°C"
                )

            elif template == "DISPATCH_UNIT_ASSIGNED":
                subject = "Ambulance Unit Assigned"
                message = (
                    f"Unit {vars['unit_id']} has been assigned to patient {patient_id}.\n"
                    f"ETA: {vars['eta_minutes']} minutes to hospital {vars['dest_hospital_id']}."
                )

            elif template == "DISPATCH_ENROUTE":
                subject = "En Route to Patient"
                message = (
                    f"Ambulance {vars['unit_id']} is en route to patient {patient_id}.\n"
                    f"ETA: {vars['eta_minutes']} minutes."
                )

            elif template == "DISPATCH_PATIENT_ONBOARD":
                subject = "Patient Onboard"
                message = (
                    f"Patient {patient_id} is onboard ambulance {vars['unit_id']}."
                )

            elif template == "DISPATCH_ARRIVED_AT_HOSPITAL":
                subject = "Arrived at Hospital"
                message = (
                    f"Ambulance {vars['unit_id']} has arrived at hospital {vars['dest_hospital_id']} "
                    f"with patient {patient_id}."
                )

            elif template == "BILLING_COMPLETED":
                subject = "Billing Completed"
                message = (
                    f"Billing completed for patient {patient_id}.\n"
                    f"Amount: ${vars['amount']} | Status: {vars['status']}"
                )

            else:
                subject = f"Notification: {template or msg_type}"
                message = json.dumps(vars, indent=2)

            # Send notification via SNS
            msg_id = send_notification(patient_id, subject, message)
            print(f"Notification sent (MessageId: {msg_id})")

        except Exception as e:
            print(f"❌ Error processing message: {e}")

    channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=True)
    print(f"[*] Waiting for messages on {queue_name}. To exit press CTRL+C.")
    channel.start_consuming()


# --- FastAPI Routes ---
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/notify")
def notify(req: Notification):
    """Sends notification through SNS and publishes event to RabbitMQ."""
    try:
        msg_id = send_notification(req.patient_id, req.subject, req.message)
        # publish_billing_complete(req.patient_id, req.subject, req.message)
        return {"status": "sent", "message_id": msg_id, "patient_id": req.patient_id}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error sending notification: {str(e)}"
        )


# --- Entry Point ---
# Hello
def main():
    """Start the FastAPI app and background RabbitMQ consumer."""
    print("🚀 Starting Notification microservice...")
    setup_rabbitmq()

    # Start the consumer in a background thread
    consumer_thread = threading.Thread(target=consume_notifications, daemon=True)
    consumer_thread.start()

    # Start FastAPI (Uvicorn) server
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
