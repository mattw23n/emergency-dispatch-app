import pika
import json
import os
import uuid
import time
import threading
from flask import Flask, jsonify

# --- RabbitMQ Configuration ---
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', 5672))

EXCHANGE_NAME = "amqp.topic"
EXCHANGE_TYPE = "topic"

CONSUME_QUEUE = "amqp.topic"
PRODUCE_QUEUE = "triage.status"

CONSUME_ROUTING_KEY = "wearable.data"
PRODUCE_ROUTING_KEY = "triage.status."


# --- Flask App for Health Check ---
app = Flask(__name__)
# Global state for health check
service_state = {
    "is_connected": False,
    "messages_processed": 0,
    "emergencies_detected": 0,
    "abnormalities_detected": 0
}

def determine_triage_status(metrics):
    """
    Analyzes health metrics and returns a status: "Normal", "Abnormal", or "Emergency".
    This is the core business logic of the Triage Service.
    """
    hr = metrics.get("heartRateBpm", 0)
    spo2 = metrics.get("spO2Percentage", 100)
    
    # --- Emergency Conditions (Highest Priority) ---
    # These conditions are immediately life-threatening.
    if spo2 < 91:
        return "Emergency", "Critically low blood oxygen (Hypoxia)."
    if hr > 150 or hr < 40:
        return "Emergency", "Critically abnormal heart rate."

    # --- Abnormal Conditions (Medium Priority) ---
    # These are concerning but may not be immediately critical.
    if spo2 < 95:
        return "Abnormal", "Low blood oxygen."
    if hr > 120 or hr < 50:
        return "Abnormal", "Abnormal heart rate."
        
    # --- Normal Condition ---
    return "Normal", "Vitals are within the normal range."

def process_message(channel, method, properties, body):
    """Callback function to handle incoming messages."""
    try:
        data = json.loads(body)
        print(f"[+] Received user data for ID: {data.get('userId')}")
        service_state["messages_processed"] += 1

        metrics = data.get("metrics", {})
        status, reason = determine_triage_status(metrics)
        
        print(f"    -> Triage Status: {status} ({reason})")

        # Only publish a new message if the status is an Emergency
        if status == "Emergency" or status == "Abnormal":
            if status == "Emergency":
                service_state["emergencies_detected"] += 1
            else:
                service_state["abnormalities_detected"] += 1
            
            # Create the new event payload
            triage_event = {
                "type": "TriageStatus",
                "incident_id": str(uuid.uuid4()),
                "user_id": data.get("userId"),
                "status": status,
                "metrics": metrics,
                "location": data.get("location"),
                "timestampMs": int(time.time() * 1000)
            }
            
            message_body = json.dumps(triage_event, indent=4)

            # Publish the event
            channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key=PRODUCE_ROUTING_KEY + status.lower(),
                body=message_body,
                properties=pika.BasicProperties(
                    content_type='application/json', delivery_mode=2
                ),
            )
            print(f"    [!] EMERGENCY DETECTED! Published event with Incident ID: {triage_event['incident_id']}")

    except json.JSONDecodeError:
        print("[!] Error: Could not decode JSON message.")
    except Exception as e:
        print(f"[!] An unexpected error occurred: {e}")
    finally:
        # Acknowledge the message so RabbitMQ removes it from the queue
        channel.basic_ack(delivery_tag=method.delivery_tag)

def start_consumer():
    """Starts the RabbitMQ consumer loop."""
    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
            )
            channel = connection.channel()

            # Declare exchanges and queue for consuming
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type=EXCHANGE_TYPE)
            channel.queue_declare(queue=CONSUME_QUEUE, durable=True)
            channel.queue_bind(queue=CONSUME_QUEUE, exchange=EXCHANGE_NAME, routing_key=CONSUME_ROUTING_KEY)


            service_state["is_connected"] = True
            print("[*] Triage Service connected to RabbitMQ. Waiting for messages...")

            channel.basic_consume(queue=CONSUME_QUEUE, on_message_callback=process_message)
            channel.start_consuming()

        except pika.exceptions.AMQPConnectionError as e:
            print(f"[!] Connection failed: {e}. Retrying in 5 seconds...")
            service_state["is_connected"] = False
            time.sleep(5)
        except KeyboardInterrupt:
            print("[-] Shutting down consumer.")
            break

@app.route('/health', methods=['GET'])
def health_check():
    """Provides the health status of the Triage Service."""
    status_code = 200 if service_state["is_connected"] else 503
    return jsonify(service_state), status_code

if __name__ == '__main__':
    # Run the RabbitMQ consumer in a background thread
    consumer_thread = threading.Thread(target=start_consumer, daemon=True)
    consumer_thread.start()
    
    # Run the Flask health check server in the main thread
    app.run(host='0.0.0.0', port=5001)