import pika
import json
import time
import random
import sys
import os
import threading
from flask import Flask, jsonify

# --- RabbitMQ Configuration ---
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', 5672))

EXCHANGE_NAME = "amqp.topic"
EXCHANGE_TYPE = "topic"
ROUTING_KEY = "wearable.data"

# --- Flask App for Health Check ---
app = Flask(__name__)
# Global reference to our publisher instance for the health check
publisher_instance = None

class WearablePublisher:
    """
    Manages the RabbitMQ connection and publishing loop in a separate thread.
    """
    def __init__(self, scenario):
        if scenario not in ['normal', 'emergency']:
            raise ValueError("Scenario must be 'normal' or 'emergency'")
        
        self.scenario = scenario
        self.messages_sent = 0
        self.connection = None
        self.channel = None
        self.is_running = True
        self.is_connected = False
        self.routing_key = ROUTING_KEY
        
        if self.scenario == 'normal':
            self.metric_generator = self._generate_normal_metrics
        else:
            self.metric_generator = self._generate_emergency_metrics

    def _connect(self):
        """Establishes connection and channel to RabbitMQ."""
        try:
            self.connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
            )
            self.channel = self.connection.channel()
            self.channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type=EXCHANGE_TYPE, durable=True)
            self.is_connected = True
            print("--- Successfully connected to RabbitMQ ---")
        except pika.exceptions.AMQPConnectionError as e:
            print(f"Error: Could not connect to RabbitMQ: {e}")
            self.is_connected = False
            time.sleep(5) # Wait before retrying

    def run_publisher_loop(self):
        """The main loop that generates and publishes messages."""
        print(f"--- Starting simulation in {self.scenario.upper()} mode ---")
        while self.is_running:
            if not self.is_connected:
                self._connect()
                continue # Retry connection on the next loop iteration
            
            try:
                payload = self._generate_base_payload()
                payload['timestampMs'] = int(time.time() * 1000)
                payload['metrics'] = self.metric_generator()
                message_body = json.dumps(payload, indent=2)

                self.channel.basic_publish(
                    exchange=EXCHANGE_NAME,
                    routing_key=self.routing_key,
                    body=message_body,
                    properties=pika.BasicProperties(
                        content_type='application/json', delivery_mode=2
                    ),
                )
                self.messages_sent += 1
                print(f" [x] Sent message #{self.messages_sent} to routing key '{self.routing_key}'")
                time.sleep(4)
            except (pika.exceptions.StreamLostError, pika.exceptions.AMQPConnectionError) as e:
                print(f"Connection lost: {e}. Reconnecting...")
                self.is_connected = False
            except Exception as e:
                print(f"An unexpected error occurred: {e}")
                self.stop()

    def stop(self):
        """Stops the publishing loop and closes the connection."""
        self.is_running = False
        if self.connection and self.connection.is_open:
            self.connection.close()
        print("--- Publisher stopped and connection closed ---")

    # --- Data Generation Methods ---
    def _generate_base_payload(self):
        return {"userId": "1", "device": {"id": "wearable-1", "model": "HealthTracker v1"}, "schemaVersion": "1.0"}
    
    def _generate_normal_metrics(self):
        return {
            "heartRateBpm": random.randint(60, 95), "spO2Percentage": round(random.uniform(96.0, 99.5), 2),
            "respirationRateBpm": random.randint(12, 20), "bodyTemperatureCelsius": round(random.uniform(36.5, 37.5), 2),
            "stepsSinceLastReading": random.randint(0, 30)
        }

    def _generate_emergency_metrics(self):
        return {
            "heartRateBpm": random.randint(140, 190), "spO2Percentage": round(random.uniform(85.0, 92.5), 2),
            "respirationRateBpm": random.randint(22, 30), "bodyTemperatureCelsius": round(random.uniform(36.0, 37.0), 2),
            "stepsSinceLastReading": 0
        }

@app.route('/health', methods=['GET'])
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

def main():
    global publisher_instance
    if len(sys.argv) < 2:
        print("Usage: python wearable_simulator.py <scenario>")
        print("Available scenarios: normal, emergency")
        return

    scenario = sys.argv[1].lower()
    
    try:
        publisher_instance = WearablePublisher(scenario)
        # Run the publisher in a daemon thread so it exits when the main app exits
        publisher_thread = threading.Thread(target=publisher_instance.run_publisher_loop, daemon=True)
        publisher_thread.start()
        
        # Start the Flask server in the main thread
        # Use host='0.0.0.0' to make it accessible from outside a container
        app.run(host='0.0.0.0', port=5000)

    except ValueError as e:
        print(f"Error: {e}")
    except KeyboardInterrupt:
        print("\n--- Shutting down service ---")
    finally:
        if publisher_instance:
            publisher_instance.stop()

if __name__ == '__main__':
    main()