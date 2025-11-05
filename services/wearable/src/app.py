import time
import random
import sys
import os
import threading
import signal
from flask import Flask, jsonify, request

from amqp_setup import amqp_setup

# --- Flask App for Health Check ---
app = Flask(__name__)
# Global reference to our publisher instance for the health check
publisher_instance = None


class WearablePublisher:
    """
    Manages the RabbitMQ connection and publishing loop in a separate thread.
    """

    def __init__(self, scenario):
        if scenario not in ["normal", "abnormal", "emergency"]:
            raise ValueError("Scenario must be 'normal' or 'abnormal' or 'emergency'")

        self.scenario = scenario
        self.messages_sent = 0
        self.is_running = True
        self.is_connected = False

        if self.scenario == "normal":
            self.metric_generator = self._generate_normal_metrics
        elif self.scenario == "abnormal":
            self.metric_generator = self._generate_abnormal_metrics
        else:
            self.metric_generator = self._generate_emergency_metrics

    def _connect(self):
        """Establishes connection to RabbitMQ."""
        try:
            amqp_setup.connect()
            self.is_connected = True
            print("--- Successfully connected to RabbitMQ ---")
        except Exception as e:
            print(f"Error: Could not connect to RabbitMQ: {e}")
            self.is_connected = False
            time.sleep(5)  # Wait before retrying

    def run_publisher_loop(self):
        """The main loop that generates and publishes messages."""
        print(f"--- Starting simulation in {self.scenario.upper()} mode ---")
        while self.is_running:
            if not self.is_connected:
                self._connect()
                continue  # Retry connection on the next loop iteration

            try:
                payload = self._generate_base_payload()
                payload["timestamp"] = int(time.time() * 1000)
                payload["metrics"] = self.metric_generator()

                amqp_setup.publish_wearable_data(payload)

                self.messages_sent += 1
                print(
                    f" [x] Sent message #{self.messages_sent} "
                    f"to routing key '{amqp_setup.RK_WEARABLE_DATA}'"
                )
                time.sleep(10)  # Publish every 10 seconds

            except Exception as e:
                print(f"Connection lost or error occurred: {e}. Reconnecting...")
                self.is_connected = False

    def stop(self):
        """Stops the publishing loop and closes the connection."""
        self.is_running = False
        amqp_setup.close()
        print("--- Publisher stopped and connection closed ---")

    # --- Data Generation Methods ---
    def _generate_base_payload(self):
        return {
            "patient_id": "P123",
            "device": {"id": "wearable-1", "model": "HealthTracker v1"},
            "location": {"lat": 1.2974, "lng": 103.8493},
            "schemaVersion": "1.0",
        }

    def _generate_normal_metrics(self):
        return {
            "heartRateBpm": random.randint(50, 100),
            "spO2Percentage": round(random.uniform(95.0, 99.5), 2),
            "respirationRateBpm": random.randint(10, 24),
            "bodyTemperatureCelsius": round(random.uniform(36.0, 37.5), 2),
            "stepsSinceLastReading": random.randint(0, 30),
        }

    def _generate_abnormal_metrics(self):
        return {
            "heartRateBpm": random.choice(
                [random.randint(101, 150), random.randint(40, 49)]
            ),
            "spO2Percentage": round(random.uniform(91.0, 94.9), 2),
            "respirationRateBpm": random.choice(
                [random.randint(25, 30), random.randint(8, 9)]
            ),
            "bodyTemperatureCelsius": random.choice(
                [
                    round(random.uniform(37.6, 39.0), 2),
                    round(random.uniform(35.1, 35.9), 2),
                ]
            ),
            "stepsSinceLastReading": random.randint(0, 10),
        }

    def _generate_emergency_metrics(self):
        return {
            "heartRateBpm": random.choice(
                [random.randint(151, 190), random.randint(20, 39)]
            ),
            "spO2Percentage": round(random.uniform(80.0, 90.9), 2),
            "respirationRateBpm": random.choice(
                [random.randint(31, 40), random.randint(4, 7)]
            ),
            "bodyTemperatureCelsius": random.choice(
                [
                    round(random.uniform(39.1, 42.0), 2),
                    round(random.uniform(32.0, 34.9), 2),
                ]
            ),
            "stepsSinceLastReading": 0,
        }


@app.route("/health", methods=["GET"])
def health_check():
    """Provides the health status of the publisher service."""
    if publisher_instance:
        status = {
            "status": "ok" if publisher_instance.is_connected else "degraded",
            "rabbitmq_connected": publisher_instance.is_connected,
            "scenario": publisher_instance.scenario,
            "messages_sent": publisher_instance.messages_sent,
        }
        status_code = 200 if publisher_instance.is_connected else 503
        return jsonify(status), status_code
    else:
        return jsonify({"status": "error", "message": "Publisher not initialized"}), 500


@app.route("/status", methods=["GET"])
def status():
    """Check AMQP connection status."""
    ready = bool(amqp_setup.connection and amqp_setup.connection.is_open)
    return jsonify(amqp_connected=ready), (200 if ready else 503)


@app.route("/scenario", methods=["GET"])
def get_scenario():
    """Get current scenario."""
    if publisher_instance:
        return jsonify({"scenario": publisher_instance.scenario}), 200
    return jsonify({"error": "Publisher not initialized"}), 500


@app.route("/scenario", methods=["PUT"])
def change_scenario():
    """Change the current scenario."""
    if not publisher_instance:
        return jsonify({"error": "Publisher not initialized"}), 500

    data = request.get_json()
    new_scenario = data.get("scenario", "").lower()

    if new_scenario not in ["normal", "abnormal", "emergency"]:
        return jsonify(
            {"error": "Invalid scenario. Must be 'normal', 'abnormal', or 'emergency'"}
        ), 400

    # Update the scenario and metric generator
    publisher_instance.scenario = new_scenario
    if new_scenario == "normal":
        publisher_instance.metric_generator = (
            publisher_instance._generate_normal_metrics
        )
    elif new_scenario == "abnormal":
        publisher_instance.metric_generator = (
            publisher_instance._generate_abnormal_metrics
        )
    else:
        publisher_instance.metric_generator = (
            publisher_instance._generate_emergency_metrics
        )

    print(f"[SCENARIO CHANGE] Switched to {new_scenario.upper()} mode")
    return jsonify(
        {"message": f"Scenario changed to {new_scenario}", "scenario": new_scenario}
    ), 200


def _graceful_shutdown(*_):
    if publisher_instance:
        publisher_instance.stop()


def main():
    global publisher_instance
    # Default to normal if no argument provided
    scenario = "normal"
    if len(sys.argv) > 1:
        scenario = sys.argv[1].lower()

    if scenario not in ["normal", "abnormal", "emergency"]:
        print(
            f"Error: Invalid scenario '{scenario}'. Must be 'normal', 'abnormal', or 'emergency'"
        )
        return

    print(f"Starting wearable service with scenario: {scenario}")

    try:
        # Set up graceful shutdown
        signal.signal(signal.SIGTERM, _graceful_shutdown)
        signal.signal(signal.SIGINT, _graceful_shutdown)

        publisher_instance = WearablePublisher(scenario)
        # Run the publisher in a daemon thread
        # so it exits when the main app exits
        publisher_thread = threading.Thread(
            target=publisher_instance.run_publisher_loop, daemon=True
        )
        publisher_thread.start()

        # Start the Flask server in the main thread
        # Use host='0.0.0.0' to make it accessible from outside a container
        # nosemgrep: python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))

    except ValueError as e:
        print(f"Error: {e}")
    except KeyboardInterrupt:
        print("\n--- Shutting down service ---")
    finally:
        _graceful_shutdown()


if __name__ == "__main__":
    main()
