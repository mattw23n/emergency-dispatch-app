import datetime
import json
import os
import signal
import sys
from threading import Thread

import mysql.connector
import pika
from flask import Flask, jsonify

import amqp_setup

# Create a singleton instance
amqp = amqp_setup.AMQPSetup()

# Database configuration
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 3306)),
    'user': os.environ.get('DB_USER', 'cs302'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME','cs302DB')
}


# Flag to control the consumer loop
should_stop = False


def signal_handler(sig, frame):
    global should_stop
    print("Stopping consumer...")
    should_stop = True


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


INSURANCE_API_URL = os.environ.get(
    "INSURANCE_API_URL", "http://insurance:5200/insurance/verify"
)


def callback(ch, method, properties, body):
    """Process billing initiation message."""
    try:
        # Remove any trailing semicolons and whitespace before parsing JSON
        body_str = body.decode('utf-8').strip().rstrip(';').strip()
        message_body = json.loads(body_str)
        incident_id = message_body["incident_id"]
        patient_id = message_body["patient_id"]
        amount = 100

        # Connect to DB and create billing record
        cnx = mysql.connector.connect(**DB_CONFIG)
        cursor = cnx.cursor()

        # Insert new billing
        cursor.execute(
            """
            INSERT INTO billings (incident_id, patient_id, amount)
            VALUES (%s, %s, %s)
        """,
            (incident_id, patient_id, amount),
        )
        cnx.commit()

        # Get the inserted id
        id = cursor.lastrowid
        cnx.close()

        print(
            f"SUCCESS: Created billings {id} for patient {patient_id}, amount {amount}"
        )

        # 1. Call insurance verification
        insurance_verified = verify_insurance(incident_id, patient_id)

        # 2. Process payment if insurance is verified
        payment_reference = None
        if insurance_verified:
            try:
                # Convert amount to cents for Stripe
                amount_in_cents = int(float(amount) * 100)
                payment_reference = process_payment(
                    patient_id=patient_id,
                    amount=amount_in_cents,
                    description=f"Billing for incident {incident_id}",
                )
                status = "PAID"
                print(
                    f"SUCCESS: Payment processed for billings {id}, reference: {payment_reference}"
                )

                # Publish payment completion notification and event
                status_msg = {
                    "id": id,
                    "incident_id": incident_id,
                    "patient_id": patient_id,
                    "amount": amount,
                    "status": "COMPLETED",
                    "payment_reference": payment_reference,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                # Publish status update using the new method
                amqp.publish_status_update(status_msg, is_success=True)

            except Exception as e:
                error_msg = str(e)
                print(f"FAIL: Payment processing failed: {error_msg}")
                if "card was declined" in error_msg.lower():
                    status = "PAYMENT_DECLINED"
                else:
                    status = "PAYMENT_FAILED"

                # Publish payment failure notification
                try:
                    status_msg = {
                        "id": id,
                        "incident_id": incident_id,
                        "patient_id": patient_id,
                        "amount": amount,
                        "status": status,  # This will be either PAYMENT_DECLINED or PAYMENT_FAILED
                        "error": error_msg,
                        "timestamp": datetime.datetime.utcnow().isoformat()
                    }
                    # Publish status update using the new method
                    amqp.publish_status_update(status_msg, is_success=False)
                except Exception as notify_err:
                    print(f"WARNING: Failed to send failure notification: {notify_err}")
        else:
            status = "INSURANCE_VERIFICATION_FAILED"
            print(f"INFO: Insurance verification failed for billings {id}")

            # Publish insurance verification failure notification
            try:
                status_msg = {
                    "id": id,
                    "incident_id": incident_id,
                    "patient_id": patient_id,
                    "amount": amount,
                    "status": "INSURANCE_VERIFICATION_FAILED",
                    "error": "Insurance verification failed",
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                amqp.publish_status_update(status_msg, is_success=False)
            except Exception as notify_err:
                print(f"WARNING: Failed to send insurance failure notification: {notify_err}")

        # 3. Update billing record with verification and payment status
        update_billing_status(id, insurance_verified, payment_reference, status)

    except Exception as e:
        print(f"FAIL: Unexpected error: {str(e)}")


def update_billing_status(id, insurance_verified, payment_reference, status):
    """Update billing record with verification and payment status."""
    try:
        cnx = mysql.connector.connect(**DB_CONFIG)
        cursor = cnx.cursor()

        cursor.execute(
            """
            UPDATE billings
            SET status = %s,
                insurance_verified = %s,
                payment_reference = %s,
                updated_at = NOW()
            WHERE id = %s
        """,
            (status, insurance_verified, payment_reference, id),
        )

        cnx.commit()
        print(f"Updated billings {id} with status: {status}")

    except mysql.connector.Error as err:
        print(f"Error updating billings status: {err}")
        raise
    finally:
        if "cnx" in locals() and cnx.is_connected():
            cursor.close()
            cnx.close()
    try:
        # Ensure connection is established
        amqp.connect()

        # Set up consumer
        amqp.channel.basic_consume(
            queue=amqp.queue_name, on_message_callback=callback, auto_ack=True
        )

        print(" [*] Waiting for messages. To exit press CTRL+C")

        # Start consuming
        while not should_stop:
            try:
                # Process any pending events and sleep for 1 second
                amqp.connection.process_data_events()
                amqp.connection.sleep(1)
            except pika.exceptions.AMQPConnectionError:
                print("Connection lost. Attempting to reconnect...")
                amqp.connect()
                amqp.channel.basic_consume(
                    queue=amqp.queue_name, on_message_callback=callback, auto_ack=True
                )

    except KeyboardInterrupt:
        print("Interrupted")
    except Exception as e:
        print(f"Error in consumer: {e}")
    finally:
        if hasattr(amqp, "connection") and amqp.connection and amqp.connection.is_open:
            amqp.close()
        sys.exit(0)


def verify_insurance(incident_id, patient_id, amount=None):
    """
    Verify insurance coverage with the insurance service.

    Args:
        incident_id (str): The ID of the incident being billed
        patient_id (str): The ID of the patient
        amount (float, optional): The amount to verify coverage for. If not provided,
                                will be fetched from the billing record.

    Returns:
        bool: True if insurance verification is successful, False otherwise
    """
    import requests

    try:
        # If amount is not provided, get it from the billing record
        if amount is None:
            cnx = mysql.connector.connect(**DB_CONFIG)
            cursor = cnx.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT amount FROM billings
                WHERE incident_id = %s AND patient_id = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (incident_id, patient_id),
            )

            result = cursor.fetchone()
            cursor.close()
            cnx.close()

            if not result:
                print(f"No billing record found for incident {incident_id} and patient {patient_id}")
                return False

            try:
                amount = float(result['amount'])
            except (ValueError, KeyError) as e:
                print(f"Invalid amount in database: {str(e)}")
                return False

        # Prepare the request to insurance service
        headers = {"Content-Type": "application/json"}
        payload = {
            "patient_id": patient_id,
            "incident_id": incident_id,
            "amount": amount,
        }
        
        response = requests.post(
            INSURANCE_API_URL,
            json=payload,
            headers=headers,
            timeout=10,  # 10 seconds timeout
        )
        
        # Check if the request was successful
        response.raise_for_status()

        # Parse the response
        result = response.json()

        if result.get("verified"):
            print(
                f"Insurance verification successful: {
                    result.get(
                        'message',
                        '')}"
            )
            return True
        else:
            print(
                f"Insurance verification failed: {
                    result.get(
                        'message',
                        'Unknown error')}"
            )
            return False

    except requests.exceptions.RequestException as e:
        print(f"Error communicating with insurance service: {str(e)}")
        return False
    except Exception as e:
        print(f"Unexpected error during insurance verification: {str(e)}")
        return False


def process_payment(patient_id, amount, description):
    """Process payment using Stripe."""
    from stripe_service import process_stripe_payment

    try:
        # Convert amount from cents to dollars for the service
        amount_dollars = float(amount) / 100

        # Call Stripe service to process payment
        result = process_stripe_payment(amount=amount_dollars, description=description)

        if result["success"]:
            return result["payment_intent_id"]
        else:
            error_msg = result.get("error", "Unknown error")
            print(f"Payment processing failed: {error_msg}")
            raise Exception(f"Payment failed: {error_msg}")

    except Exception as e:
        print(f"Error in process_payment: {str(e)}")
        raise


def consume():
    """Start the RabbitMQ consumer."""
    try:
        # Ensure connection is established
        amqp.connect()

        # Set up consumer
        amqp.channel.basic_consume(
            queue=amqp.queue_name, on_message_callback=callback, auto_ack=True
        )

        print(" [*] Waiting for messages. To exit press CTRL+C")

        # Start consuming
        while not should_stop:
            try:
                # Process any pending events and sleep for 1 second
                amqp.connection.process_data_events()
                amqp.connection.sleep(1)
            except pika.exceptions.AMQPConnectionError:
                print("Connection lost. Attempting to reconnect...")
                amqp.connect()
                amqp.channel.basic_consume(
                    queue=amqp.queue_name, on_message_callback=callback, auto_ack=True
                )

    except KeyboardInterrupt:
        print("Interrupted")
    except Exception as e:
        print(f"Error in consumer: {e}")
    finally:
        if hasattr(amqp, "connection") and amqp.connection and amqp.connection.is_open:
            amqp.close()
        sys.exit(0)


# Initialize Flask app
app = Flask(__name__)

@app.route('/health')
def health_check():
    """Health check endpoint to verify service is running."""
    status = {'status': 'healthy', 'service': 'billings', 'version': '1.0.0'}
    status_code = 200
    
    # Check database connection
    try:
        cnx = mysql.connector.connect(**{
            **DB_CONFIG,
            'connection_timeout': 5,
            'buffered': True
        })
        cursor = cnx.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        if not result or result[0] != 1:
            raise Exception("Unexpected database response")
        status['database'] = 'connected'
    except Exception as e:
        status.update({
            'status': 'unhealthy',
            'database': 'connection failed',
            'error': str(e)
        })
        status_code = 500
    finally:
        if 'cnx' in locals() and cnx.is_connected():
            cnx.close()
    
    status['timestamp'] = datetime.datetime.utcnow().isoformat()
    return jsonify(status), status_code

def run_flask_app():
    """Run the Flask app in a separate thread."""
    port = int(os.environ.get('PORT', '5100'))  # Default to 5100 if not set
    print(f"Starting Flask server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)

if __name__ == "__main__":
    print("Starting billings service...")
    print(f"Environment PORT: {os.environ.get('PORT')}")
    
    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    
    # Start the consumer in the main thread
    print("Starting RabbitMQ consumer...")
    consume()
