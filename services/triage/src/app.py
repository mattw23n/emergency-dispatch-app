import json
import os
import uuid
import time
import threading
import signal
from flask import Flask, jsonify


from amqp_setup import amqp_setup

# --- Flask App for Health Check ---
app = Flask(__name__)

# Global state for health check
service_state = {
    "is_connected": False,
    "messages_processed": 0,
    "emergencies_detected": 0,
    "abnormalities_detected": 0,
}


def determine_triage_status(metrics):
    """
    Analyzes health metrics and returns a status: "Normal", "Abnormal", or "Emergency".
    This is the core business logic of the Triage Service.
    """
    hr = metrics.get("heartRateBpm", 0)
    spo2 = metrics.get("spO2Percentage", 100)
    temp = metrics.get("bodyTemperatureCelsius", 37.0)
    resp_rate = metrics.get("respirationRateBpm", 16)

    # --- Emergency Conditions (Highest Priority) ---
    if spo2 < 91:
        return "Emergency", "Critically low blood oxygen (Severe Hypoxia)."
    if hr > 150 or hr < 40:
        return "Emergency", "Critically abnormal heart rate."
    if temp > 39.0 or temp < 35.0:
        return "Emergency", "Critically abnormal body temperature."
    if resp_rate > 30 or resp_rate < 8:
        return "Emergency", "Critically abnormal respiration rate."

    # --- Abnormal Conditions (Medium Priority) ---
    if spo2 < 95:
        return "Abnormal", "Low blood oxygen (Mild Hypoxia)."
    if hr > 100 or hr < 50:
        return "Abnormal", "Abnormal heart rate."
    if temp > 37.5 or temp < 36.0:
        return "Abnormal", "Abnormal body temperature."
    if resp_rate > 24 or resp_rate < 10:
        return "Abnormal", "Abnormal respiration rate."

    # --- Normal Condition ---
    return "Normal", "All vitals are within the normal range."


def process_message(channel, method, properties, body):
    """Callback function to handle incoming wearable data messages."""
    try:
        data = json.loads(body)
        print(f"[+] Received user data for ID: {data.get('userId')}")
        service_state["messages_processed"] += 1

        metrics = data.get("metrics", {})
        status, reason = determine_triage_status(metrics)

        print(f"    -> Triage Status: {status} ({reason})")

        # Only publish if status is Emergency or Abnormal
        if status in ["Emergency", "Abnormal"]:
            if status == "Emergency":
                service_state["emergencies_detected"] += 1
            else:
                service_state["abnormalities_detected"] += 1

            # Create the triage event payload
            incident_id = str(uuid.uuid4())
            triage_event = {
                "type": "TriageStatus",
                "incident_id": incident_id,
                "user_id": data.get("userId"),
                "patient_id": data.get("userId"),  # Map userId to patient_id
                "status": status.lower(),
                "metrics": metrics,
                "location": data.get("location"),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "timestampMs": int(time.time() * 1000),
            }

            # Publish via amqp_setup
            amqp_setup.publish_triage_status(incident_id, status, triage_event)
            print(
                f"    [!] {status.upper()} DETECTED! Published event with Incident ID: {incident_id}"
            )

        # Acknowledge the message
        channel.basic_ack(delivery_tag=method.delivery_tag)

    except json.JSONDecodeError:
        print("[!] Error: Could not decode JSON message.")
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except Exception as e:
        print(f"[!] An unexpected error occurred: {e}")
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def start_consumer():
    """Starts the RabbitMQ consumer."""
    try:
        service_state["is_connected"] = True
        amqp_setup.connect()
        amqp_setup.start_consumer(process_message)
    except Exception as e:
        print(f"[!] Consumer error: {e}")
        service_state["is_connected"] = False


@app.route("/health", methods=["GET"])
def health_check():
    """Provides the health status of the Triage Service."""
    status_code = 200 if service_state["is_connected"] else 503
    return jsonify(service_state), status_code


@app.route("/status", methods=["GET"])
def status():
    """Check AMQP connection status."""
    ready = bool(amqp_setup.connection and amqp_setup.connection.is_open)
    return jsonify(amqp_connected=ready), (200 if ready else 503)


def _graceful_shutdown(*_):
    try:
        amqp_setup.close()
    except Exception:
        pass


if __name__ == "__main__":
    # Set up graceful shutdown
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    # Start the RabbitMQ consumer in a background thread
    consumer_thread = threading.Thread(target=start_consumer, daemon=True)
    consumer_thread.start()

    # Run the Flask health check server
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")))
