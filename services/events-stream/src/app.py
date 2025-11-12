"""
Event Stream Service - Provides Server-Sent Events (SSE) for real-time RabbitMQ event monitoring.
This service consumes from all queues and streams events to the browser dashboard.
"""
import json
import os
import queue
import threading
import time
from datetime import datetime

import pika
from flask import Flask, Response, jsonify
# from flask_cors import CORS

app = Flask(__name__)
# CORS(app)

# Thread-safe queue for passing messages from RabbitMQ consumer to SSE
event_queue = queue.Queue(maxsize=1000)

# Track service health
service_health = {
    "rabbitmq_connected": False,
    "consumers_active": 0,
    "events_streamed": 0,
    "last_event_time": None
}


def create_rabbitmq_connection():
    """Create a RabbitMQ connection."""
    rabbit_host = os.environ.get("RABBITMQ_HOST", "rabbitmq")
    rabbit_port = int(os.environ.get("RABBITMQ_PORT", "5672"))

    max_retries = 10
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=rabbit_host,
                    port=rabbit_port,
                    heartbeat=600,
                    blocked_connection_timeout=300
                )
            )
            print(f"✓ Connected to RabbitMQ at {rabbit_host}:{rabbit_port}")
            service_health["rabbitmq_connected"] = True
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            print(f"Connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise


def rabbitmq_consumer():
    """
    Consume from the exchange using a temporary, exclusive queue.
    This service is a pure listener/monitor - it creates NO durable queues.

    Uses '#' wildcard to bind to all routing keys, then filters out:
    - triage.* messages (excluded from stream)

    Streams all other messages including:
    - wearable.data
    - dispatch.*
    - billing.*
    - notification.*
    - cmd.*
    - events-manager.q.*
    """
    print("[Consumer] Starting RabbitMQ consumer thread...")

    try:
        connection = create_rabbitmq_connection()
        channel = connection.channel()

        # Declare exchange (should already exist from events-manager)
        exchange_name = os.environ.get("AMQP_EXCHANGE_NAME", "amqp.topic")
        exchange_type = os.environ.get("AMQP_EXCHANGE_TYPE", "topic")

        channel.exchange_declare(
            exchange=exchange_name,
            exchange_type=exchange_type,
            durable=True
        )

        # Create a temporary, exclusive, auto-delete queue
        # This queue will be automatically deleted when the connection closes
        # queue='' means RabbitMQ generates a unique name (amqp.gen-*)
        result = channel.queue_declare(
            queue='Events Stream',
            exclusive=True,  # Only this connection can access it
            auto_delete=True,  # Auto-delete when consumer disconnects
            durable=False  # Not durable (temporary)
        )
        queue_name = result.method.queue
        print(f"[Consumer] Created temporary queue: {queue_name}")

        # Bind to all event patterns we want to monitor
        # Using '#' wildcard to match all messages (will filter triage in callback)
        routing_keys = [
            '#',  # Match all routing keys
        ]

        for routing_key in routing_keys:
            channel.queue_bind(
                exchange=exchange_name,
                queue=queue_name,
                routing_key=routing_key
            )
            print(f"[Consumer] Bound to routing key: {routing_key}")

        service_health["consumers_active"] = len(routing_keys)

        def callback(ch, method, properties, body):
            """Handle incoming messages and push to SSE queue."""
            try:
                # Extract routing key
                routing_key = method.routing_key
 
                # Filter out triage messages - we don't want to stream these
                if routing_key.startswith('triage'):
                    return  # Skip triage messages

                # Parse message
                message_data = json.loads(body)

                # Determine event type from routing key
                event_type = 'unknown'
                if 'wearable' in routing_key:
                    event_type = 'wearable'
                elif 'dispatch' in routing_key:
                    event_type = 'dispatch'
                elif 'notification' in routing_key:
                    event_type = 'notification'
                elif 'billing' in routing_key:
                    event_type = 'billing'

                # Create event for dashboard
                dashboard_event = {
                    'type': event_type,
                    'routing_key': routing_key,
                    'timestamp': datetime.utcnow().isoformat(),
                    'data': message_data
                }

                # Push to SSE queue (non-blocking)
                try:
                    event_queue.put_nowait(dashboard_event)
                    service_health["last_event_time"] = datetime.utcnow().isoformat()
                    print(f"[Consumer] Event queued: {routing_key}")
                except queue.Full:
                    print("[Consumer] Warning: Event queue is full, dropping event")

            except json.JSONDecodeError:
                print(f"[Consumer] Warning: Could not decode message: {body}")
            except Exception as e:
                print(f"[Consumer] Error processing message: {e}")

        # Start consuming
        channel.basic_consume(
            queue=queue_name,
            on_message_callback=callback,
            auto_ack=True
        )

        print("[Consumer] ✓ Waiting for events...")
        channel.start_consuming()

    except KeyboardInterrupt:
        print("[Consumer] Interrupted")
    except Exception as e:
        print(f"[Consumer] Error: {e}")
        service_health["rabbitmq_connected"] = False
        service_health["consumers_active"] = 0


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok" if service_health["rabbitmq_connected"] else "degraded",
        **service_health
    }), 200 if service_health["rabbitmq_connected"] else 503


@app.route('/events', methods=['GET'])
def stream_events():
    """
    Server-Sent Events (SSE) endpoint that streams RabbitMQ events to the browser.
    The browser opens a persistent connection and receives events in real-time.
    """
    def events_stream():
        """Generator that yields SSE formatted messages."""
        print("[SSE] Client connected to event stream")

        # Send initial connection message
        yield f"data: {json.dumps({'type': 'connection', 'message': 'Connected to event stream'})}\n\n"

        # Keep track of events sent to this client
        events_sent = 0

        try:
            while True:
                try:
                    # Wait for an event from the queue (timeout to allow heartbeat)
                    event = event_queue.get(timeout=30)

                    # Send event to client
                    yield f"data: {json.dumps(event)}\n\n"

                    events_sent += 1
                    service_health["events_streamed"] = events_sent

                except queue.Empty:
                    # Send heartbeat to keep connection alive
                    yield ": heartbeat\n\n"

        except GeneratorExit:
            print(f"[SSE] Client disconnected (sent {events_sent} events)")

    return Response(
        events_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


if __name__ == '__main__':
    # Start RabbitMQ consumer in background thread
    consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
    consumer_thread.start()

    # Give consumer a moment to connect
    time.sleep(2)

    # Start Flask app
    port = int(os.environ.get('PORT', '8090'))
    print(f"[Flask] Starting Event Stream Service on port {port}")
    # nosemgrep: python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host
    app.run(host='0.0.0.0', port=port, threaded=True)
