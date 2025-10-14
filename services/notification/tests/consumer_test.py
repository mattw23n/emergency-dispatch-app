import pika
import json

def callback(ch, method, properties, body):
    """Process received messages"""
    print(f"✅ MESSAGE CONSUMED! Routing key '{method.routing_key}' matched binding pattern '*.notify.#'")
    print(f"Received message from exchange '{method.exchange}' with routing key '{method.routing_key}':")
    try:
        message = json.loads(body)
        print(json.dumps(message, indent=2))
    except json.JSONDecodeError:
        print(f"Raw message: {body}")
    
    # Acknowledge the message
    ch.basic_ack(delivery_tag=method.delivery_tag)
    print("Message acknowledged\n")

hostname = 'localhost'
port = 5672

parameters = pika.ConnectionParameters(host=hostname, port=port)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

# Use existing queue from billings service setup
queue_name = 'Notification'  # This queue already exists from amqp_setup.py

# Set up consumer - no need to declare or bind since queue already exists
channel.basic_consume(queue=queue_name, on_message_callback=callback)

print(f"Connecting to existing queue '{queue_name}'...")
print("This queue is already bound to 'amqp.topic' exchange with pattern '*.notify.#'")
print("Messages will only be consumed if their routing key matches this pattern")
print("To exit press CTRL+C")
print()

try:
    channel.start_consuming()
except KeyboardInterrupt:
    print("\nStopping consumer...")
    channel.stop_consuming()
    connection.close()