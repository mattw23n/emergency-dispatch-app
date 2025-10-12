import pika
import json
import time
import random
import uuid
import sys

# --- RabbitMQ Configuration ---
RABBITMQ_HOST = 'localhost'
RABBITMQ_USER = 'user'
RABBITMQ_PASS = 'password'
# We use a 'topic' exchange for flexible routing
EXCHANGE_NAME = 'health_data_exchange' 
EXCHANGE_TYPE = 'topic'

# --- Routing Keys ---
# We'll use different routing keys to signify the data type
ROUTING_KEY_NORMAL = 'wearable.data.vitals'
ROUTING_KEY_EMERGENCY = 'wearable.data.emergency'

def generate_base_payload():
    """Generates the common parts of the JSON payload."""
    return {
      "userId": "1",
      "device": {
        "id": f"wearable-1",
        "model": "HealthTracker v1"
      },
      "schemaVersion": "1.0"
    }

def generate_normal_metrics():
    """Generates metrics for a healthy, resting individual."""
    return {
        "heartRateBpm": random.randint(60, 95),
        "spO2Percentage": round(random.uniform(96.0, 99.5), 2),
        "respirationRateBpm": random.randint(12, 20),
        "bodyTemperatureCelsius": round(random.uniform(36.5, 37.5), 2),
        "stepsSinceLastReading": random.randint(0, 30)
    }

def generate_emergency_metrics():
    """
    Generates metrics simulating a cardiac emergency.
    - High heart rate (Tachycardia)
    - Low blood oxygen (Hypoxia)
    - High respiration rate
    - No movement (steps = 0)
    """
    return {
        "heartRateBpm": random.randint(140, 190),
        "spO2Percentage": round(random.uniform(85.0, 92.5), 2),
        "respirationRateBpm": random.randint(22, 30),
        "bodyTemperatureCelsius": round(random.uniform(36.0, 37.0), 2),
        "stepsSinceLastReading": 0 # User is incapacitated
    }

def main(scenario):
    """Connects to RabbitMQ and publishes messages based on the scenario."""
    
    if scenario == 'normal':
        metric_generator = generate_normal_metrics
        routing_key = ROUTING_KEY_NORMAL
        print("--- Starting simulation in NORMAL mode ---")
    elif scenario == 'emergency':
        metric_generator = generate_emergency_metrics
        routing_key = ROUTING_KEY_EMERGENCY
        print("--- Starting simulation in EMERGENCY mode ---")
    else:
        print(f"Error: Invalid scenario '{scenario}'. Choose 'normal' or 'emergency'.")
        return

    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
    )
    channel = connection.channel()

    # Declare the topic exchange. It's safe to run this every time.
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type=EXCHANGE_TYPE)

    try:
        while True:
            payload = generate_base_payload()
            payload['timestampMs'] = int(time.time() * 1000)
            payload['metrics'] = metric_generator()
            
            message_body = json.dumps(payload, indent=2)

            channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key=routing_key,
                body=message_body,
                properties=pika.BasicProperties(
                    content_type='application/json',
                    delivery_mode=2, # make message persistent
                )
            )

            print(f" [x] Sent to routing key '{routing_key}':")
            print(message_body)
            print("-" * 20)
            
            time.sleep(4)

    except KeyboardInterrupt:
        print("Simulation stopped.")
    finally:
        connection.close()
        print("Connection closed.")

if __name__ == '__main__':
    # Get the scenario from the command line arguments
    if len(sys.argv) > 1:
        scenario_arg = sys.argv[1].lower()
        main(scenario_arg)
    else:
        print("Usage: python wearable_simulator.py <scenario>")
        print("Available scenarios: normal, emergency")