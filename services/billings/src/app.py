import datetime
import json
import os
import signal
import sys
from os import environ
from urllib.parse import urlparse

import mysql.connector
import pika

import amqp_setup

# Create a singleton instance
amqp = amqp_setup.AMQPSetup()

db_url = urlparse(environ.get("db_conn"))

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
    "INSURANCE_API_URL", "http://localhost:5200/insurance/verify"
)


def callback(ch, method, properties, body):
    """Process billing initiation message."""
    try:
        message_body = json.loads(body)
        incident_id = message_body["incident_id"]
        patient_id = message_body["patient_id"]
        amount = message_body["amount"]

        # Connect to DB and create billing record
        cnx = mysql.connector.connect(
            user=db_url.username,
            password=db_url.password,
            host=db_url.hostname,
            port=db_url.port,
            database="billing",
        )
        cursor = cnx.cursor()

        # Insert new billing
        cursor.execute(
            """
            INSERT INTO billing (incident_id, patient_id, amount)
            VALUES (%s, %s, %s)
        """,
            (incident_id, patient_id, amount),
        )
        cnx.commit()

        # Get the inserted billing_id
        billing_id = cursor.lastrowid
        cnx.close()

        print(
            f"SUCCESS: Created billing {billing_id} for patient {patient_id}, amount {amount}"
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
                    f"SUCCESS: Payment processed for billing {billing_id}, reference: {payment_reference}"
                )

                # Publish payment completion notification and event
                status_msg = json.dumps(
                    {
                        "billing_id": billing_id,
                        "incident_id": incident_id,
                        "patient_id": patient_id,
                        "amount": amount,
                        "status": "COMPLETED",
                        "payment_reference": payment_reference,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                    }
                )
                # Publish a single message that both services can consume
                amqp.publish_notification(
                    message=status_msg, routing_key="billing.status.updated"
                )

            except Exception as e:
                error_msg = str(e)
                print(f"FAIL: Payment processing failed: {error_msg}")
                if "card was declined" in error_msg.lower():
                    status = "PAYMENT_DECLINED"
                else:
                    status = "PAYMENT_FAILED"

                # Publish payment failure notification
                try:
                    status_msg = json.dumps(
                        {
                            "billing_id": billing_id,
                            "incident_id": incident_id,
                            "patient_id": patient_id,
                            "amount": amount,
                            "status": "FAILED",
                            "error": error_msg,
                            "timestamp": datetime.datetime.utcnow().isoformat(),
                        }
                    )
                    # Publish a single message that both services can consume
                    amqp.publish_notification(
                        message=status_msg, routing_key="billing.status.updated"
                    )
                except Exception as notify_err:
                    print(f"WARNING: Failed to send failure notification: {notify_err}")
        else:
            status = "INSURANCE_VERIFICATION_FAILED"
            print(f"INFO: Insurance verification failed for billing {billing_id}")

            # Publish insurance verification failure notification
            try:
                status_msg = json.dumps(
                    {
                        "billing_id": billing_id,
                        "incident_id": incident_id,
                        "patient_id": patient_id,
                        "amount": amount,
                        "status": "INSURANCE_VERIFICATION_FAILED",
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                    }
                )
                # Publish a single message that both services can consume
                amqp.publish_notification(
                    message=status_msg, routing_key="billing.status.updated"
                )
            except Exception as notify_err:
                print(
                    f"WARNING: Failed to send insurance verification failure notification: {notify_err}"
                )

        # 3. Update billing record with verification and payment status
        update_billing_status(billing_id, insurance_verified, payment_reference, status)

    except mysql.connector.Error as err:
        print(f"FAIL: Could not process billing: {err}")
    except Exception as e:
        print(f"FAIL: Unexpected error: {str(e)}")


def update_billing_status(billing_id, insurance_verified, payment_reference, status):
    """Update billing record with verification and payment status."""
    try:
        cnx = mysql.connector.connect(
            user=db_url.username,
            password=db_url.password,
            host=db_url.hostname,
            port=db_url.port,
            database="billing",
        )
        cursor = cnx.cursor()

        cursor.execute(
            """
            UPDATE billing
            SET status = %s,
                insurance_verified = %s,
                payment_reference = %s,
                updated_at = NOW()
            WHERE billing_id = %s
        """,
            (status, insurance_verified, payment_reference, billing_id),
        )

        cnx.commit()
        print(f"Updated billing {billing_id} with status: {status}")

    except mysql.connector.Error as err:
        print(f"Error updating billing status: {err}")
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
        print(f"Verifying insurance for incident {incident_id}, patient {patient_id}")

        # If amount is not provided, fetch it from the billing record
        if amount is None:
            cnx = mysql.connector.connect(
                user=db_url.username,
                password=db_url.password,
                host=db_url.hostname,
                port=db_url.port,
                database="billing",
            )
            cursor = cnx.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT amount FROM billing
                WHERE incident_id = %s AND patient_id = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (incident_id, patient_id),
            )

            result = cursor.fetchone()
            cursor.close()
            cnx.close()

            if not result:
                print(
                    f"No billing record found for incident {incident_id} and patient {patient_id}"
                )
                return False

            amount = float(result["amount"])

        # Prepare the request to insurance service
        headers = {"Content-Type": "application/json"}
        payload = {
            "patient_id": patient_id,
            "incident_id": incident_id,
            "amount": amount,
        }

        print(f"Sending verification request to {INSURANCE_API_URL}")
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


if __name__ == "__main__":
    consume()
