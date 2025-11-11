"""Billing Service for the Healthcare System.

This module implements the billing service that handles payment processing,
insurance verification, and billing status updates. It provides both an HTTP API
for health checks and a message consumer for processing billing commands.
"""

import datetime
import json
import os
import signal
import time
from threading import Thread
import requests
import mysql.connector
import pika
from flask import Flask, jsonify

import amqp_setup
import stripe_service

# Create a singleton AMQP helper
amqp = amqp_setup.AMQPSetup()

# Database configuration
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", 3306)),
    "user": os.environ.get("DB_USER", "cs302"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "cs302DB"),
}


# Light retry wrapper to avoid transient startup races
def _mysql_connect_with_retries(retries: int = 10, delay: float = 0.5):
    last_err = None
    for _ in range(retries):
        try:
            return mysql.connector.connect(**DB_CONFIG)
        except mysql.connector.Error as e:
            last_err = e
            time.sleep(delay)
    raise last_err


# Flag to control the consumer loop
should_stop = False


def signal_handler(sig, frame):
    """Handle termination signals to gracefully shut down the consumer.

    This function is called when the process receives a termination signal (e.g., SIGINT).
    It sets the global `should_stop` flag to True, which allows the consumer loop
    to exit cleanly.

    Args:
        sig: The signal number.
        frame: The current stack frame (unused).
    """
    global should_stop
    print("Stopping consumer...")
    should_stop = True


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

INSURANCE_API_URL = (
    os.environ.get("insurance_service_url_internal", "http://insurance:5200").rstrip(
        "/"
    )
    + "/insurance/verify"
)


def rollback_billing(
    billing_id,
    payment_reference,
    insurance_verified,
    incident_id,
    patient_id,
    amount,
    failure_reason,
):
    """
    Rollback all completed billing actions if an error occurs.
    This follows the same pattern as rollback_orders in place_order service.

    Args:
        billing_id: ID of the billing record to rollback
        payment_reference: Stripe payment reference to refund
        insurance_verified: Whether insurance was verified
        incident_id: Incident ID for event notification
        patient_id: Patient ID for event notification
        amount: Amount that was charged
        failure_reason: Reason for the rollback
    """
    rollback_results = []

    # (1) Refund payment if it was processed
    if payment_reference:
        try:
            result = stripe_service.refund_payment(payment_reference)
            if result["success"]:
                print(f"✓ Rollback: Refunded payment {payment_reference}")
                rollback_results.append(("Payment Refund", True))
            else:
                print(
                    f"✗ Rollback: Failed to refund payment {payment_reference}: {result['error']}"
                )
                rollback_results.append(("Payment Refund", False))
        except Exception as e:
            print(f"✗ Rollback: Exception refunding payment {payment_reference}: {e}")
            rollback_results.append(("Payment Refund", False))

    # (2) Mark billing as failed/cancelled in database
    if billing_id:
        try:
            update_billing_status(
                billing_id,
                insurance_verified=False,
                payment_reference=None,
                status="CANCELLED",
            )
            print(f"✓ Rollback: Marked billing {billing_id} as CANCELLED")
            rollback_results.append(("Billing Status", True))
        except Exception as e:
            print(f"✗ Rollback: Failed to update billing {billing_id} status: {e}")
            rollback_results.append(("Billing Status", False))

    # (3) Send failure notification event
    if billing_id and incident_id and patient_id:
        try:
            failure_msg = {
                "billing_id": billing_id,
                "incident_id": incident_id,
                "patient_id": patient_id,
                "amount": amount,
                "status": "CANCELLED",
                "error": failure_reason,
                "timestamp": datetime.datetime.utcnow().isoformat(),
            }
            amqp.publish_status_update(failure_msg, is_success=False)
            print(
                f"✓ Rollback: Published failure notification for billing {billing_id}"
            )
            rollback_results.append(("Failure Notification", True))
        except Exception as e:
            print(f"✗ Rollback: Failed to publish failure notification: {e}")
            rollback_results.append(("Failure Notification", False))

    # Log rollback summary
    print(f"\n{'=' * 60}")
    print(f"ROLLBACK SUMMARY for billing {billing_id}")
    print(f"Reason: {failure_reason}")
    for action, success in rollback_results:
        status = "✓" if success else "✗"
        print(f"  {status} {action}")
    print(f"{'=' * 60}\n")


def callback(ch, method, properties, body):
    """
    Process billing initiation message.
    """
    cnx = None
    cursor = None
    billing_id = None
    payment_reference = None
    insurance_verified = False

    try:
        body_str = body.decode("utf-8").strip().rstrip(";").strip()
        message_body = json.loads(body_str)
        incident_id = message_body["incident_id"]
        patient_id = message_body["patient_id"]
        amount = message_body.get("amount", 100)

        # ==========================================
        # STEP 1: Create Billing Record
        # ==========================================
        try:
            cnx = _mysql_connect_with_retries()
            cursor = cnx.cursor()
            cursor.execute(
                """
                INSERT INTO billings (incident_id, patient_id, amount, status)
                VALUES (%s, %s, %s, 'PENDING')
                """,
                (incident_id, patient_id, amount),
            )
            cnx.commit()
            billing_id = cursor.lastrowid
            print(
                f"SUCCESS: Created billing {billing_id} for patient {patient_id}, amount {amount}"
            )
        except Exception as e:
            print(f"FAIL: Failed to create billing record: {e}")
            # No rollback needed - nothing was created
            return

        # ==========================================
        # STEP 2: Verify Insurance
        # ==========================================
        try:
            v = verify_insurance(incident_id, patient_id, amount)

            if isinstance(v, dict):
                insurance_verified = bool(v.get("verified"))
                reason = v.get("reason")
                reason_msg = v.get("message")

            else:
                insurance_verified = bool(v)
                reason = "OK" if insurance_verified else "SERVICE_ERROR"
                reason_msg = None

            if not insurance_verified:
                print(f"FAIL: Insurance verification failed: {reason} - {reason_msg}")
                rollback_billing(
                    billing_id=billing_id,
                    payment_reference=None,
                    insurance_verified=False,
                    incident_id=incident_id,
                    patient_id=patient_id,
                    amount=amount,
                    failure_reason=f"Insurance verification failed: {reason_msg}",
                )
                # Ensure we don't proceed with payment processing
                return

            insurance_verified = True  # Set to True only if we pass verification

            print(f"SUCCESS: Insurance verified for billing {billing_id}")

        except Exception as e:
            print(f"FAIL: Insurance verification error: {e}")
            rollback_billing(
                billing_id=billing_id,
                payment_reference=None,
                insurance_verified=False,
                incident_id=incident_id,
                patient_id=patient_id,
                amount=amount,
                failure_reason=f"Insurance verification error: {str(e)}",
            )
            return

        # ==========================================
        # STEP 3: Process Payment
        # ==========================================

        try:
            amount_in_cents = int(float(amount) * 100)
            payment_result = process_payment(
                patient_id=patient_id,
                amount=amount_in_cents,
                description=f"Billing for incident {incident_id}",
            )

            if not payment_result["success"]:
                error_msg = payment_result.get("error", "Payment failed")
                print(f"FAIL: Payment processing failed: {error_msg}")
                rollback_billing(
                    billing_id=billing_id,
                    payment_reference=None,
                    insurance_verified=insurance_verified,
                    incident_id=incident_id,
                    patient_id=patient_id,
                    amount=amount,
                    failure_reason=f"Payment failed: {error_msg}",
                )
                return

            payment_reference = payment_result["payment_intent_id"]
            print(
                f"SUCCESS: Payment processed for billing {billing_id}, reference: {payment_reference}"
            )

        except Exception as e:
            error_msg = str(e)
            print(f"FAIL: Payment processing error: {error_msg}")
            rollback_billing(
                billing_id=billing_id,
                payment_reference=None,  # Payment didn't succeed
                insurance_verified=insurance_verified,
                incident_id=incident_id,
                patient_id=patient_id,
                amount=amount,
                failure_reason=f"Payment processing error: {error_msg}",
            )
            return

        # ==========================================
        # STEP 4: Update Billing Status to PAID
        # ==========================================
        try:
            simulate_failure = message_body.get("simulate_failure")

            if simulate_failure == "payment":
                raise Exception("Simulated payment failure")

            update_billing_status(
                billing_id,
                insurance_verified=True,
                payment_reference=payment_reference,
                status="PAID",
            )
            print(f"SUCCESS: Updated billing {billing_id} status to PAID")

        except Exception as e:
            print(f"FAIL: Failed to update billing status: {e}")
            # Payment succeeded but DB update failed
            rollback_billing(
                billing_id=billing_id,
                payment_reference=payment_reference,
                insurance_verified=insurance_verified,
                incident_id=incident_id,
                patient_id=patient_id,
                amount=amount,
                failure_reason=f"Database update failed after payment: {str(e)}",
            )
            return

        # ==========================================
        # STEP 5: Publish Success Event
        # ==========================================
        try:
            status_msg = {
                "billing_id": billing_id,
                "incident_id": incident_id,
                "patient_id": patient_id,
                "amount": amount,
                "status": "COMPLETED",
                "payment_reference": payment_reference,
                "timestamp": datetime.datetime.utcnow().isoformat(),
            }
            amqp.publish_status_update(status_msg, is_success=True)
            print(f"SUCCESS: Published completion event for billing {billing_id}")

        except Exception as e:
            print(f"WARNING: Failed to publish success event: {e}")
            print(
                f"INFO: Billing {billing_id} is PAID but downstream notification failed"
            )

        print(f"SUCCESS: Billing saga completed for billing {billing_id}")

    except Exception as e:
        print(f"FAIL: Unexpected error in billing saga: {e}")
        # Rollback whatever we can
        if billing_id:
            rollback_billing(
                billing_id=billing_id,
                payment_reference=payment_reference,
                insurance_verified=insurance_verified,
                incident_id=message_body.get("incident_id"),
                patient_id=message_body.get("patient_id"),
                amount=message_body.get("amount", 100),
                failure_reason=f"Unexpected error: {str(e)}",
            )
    finally:
        if cursor:
            cursor.close()
        if cnx and cnx.is_connected():
            cnx.close()


def compensate_payment(billing_id, payment_reference):
    """Compensate a completed payment: refund Stripe and mark billing VOIDED.

    Returns:
        bool: True if compensation was successful, False otherwise
    """
    try:
        refund_success = True
        if payment_reference:
            result = stripe_service.refund_payment(payment_reference)
            if result["success"]:
                print(
                    f"SUCCESS: Refunded Stripe payment for billing {billing_id}, refund_id: {result.get('refund_id', 'N/A')}"
                )
            else:
                print(
                    f"FAIL: Stripe refund failed for billing {billing_id}: {result.get('error', 'Unknown error')}"
                )
                refund_success = False
        else:
            print(f"INFO: No payment to refund for billing {billing_id}")

        # Mark billing as VOIDED
        update_success = update_billing_status(
            billing_id,
            insurance_verified=False,
            payment_reference=None,
            status="VOIDED",
        )

        if update_success:
            print(f"SUCCESS: Billing {billing_id} marked as VOIDED")
        else:
            print(f"FAIL: Failed to update billing {billing_id} status")

        return refund_success and update_success

    except Exception as e:
        print(f"FAIL: Compensation for billing {billing_id} failed: {str(e)}")
        return False


def update_billing_status(id, insurance_verified, payment_reference, status):
    """Update billing record with verification and payment status, then return. No consumer loops here."""
    try:
        cnx = _mysql_connect_with_retries()
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
        return True
    except mysql.connector.Error as err:
        print(f"Error updating billings status: {err}")
        return False
    finally:
        if "cnx" in locals() and cnx.is_connected():
            cursor.close()
            cnx.close()


def verify_insurance(incident_id, patient_id, amount=None):
    """Verify insurance coverage for a patient's incident.

    Makes an HTTP request to the insurance service to verify coverage for the
    specified patient and incident. Handles various error cases and returns
    a structured response.

    Args:
        incident_id: Unique identifier for the medical incident.
        patient_id: Unique identifier for the patient.
        amount: Optional amount in cents to verify coverage for.

    Returns:
        dict: A dictionary containing:
            - verified (bool): Whether insurance verification was successful
            - reason (str): One of: OK, NO_POLICY, INSUFFICIENT_COVERAGE,
                          SERVICE_UNAVAILABLE, or SERVICE_ERROR
            - message (str): Human-readable status message
            - http_status (int|None): HTTP status code from the insurance service
    """

    try:
        if amount is None:
            cnx = _mysql_connect_with_retries()
            cursor = cnx.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT amount FROM billings
                WHERE incident_id = %s AND patient_id = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (incident_id, patient_id),
            )
            row = cursor.fetchone()
            cursor.close()
            cnx.close()
            if not row:
                return {
                    "verified": False,
                    "reason": "SERVICE_ERROR",
                    "message": f"No billing record found for incident {incident_id} / patient {patient_id}",
                    "http_status": None,
                }
            amount = float(row["amount"])

        url = INSURANCE_API_URL

        try:
            r = requests.post(
                # HTTP is safe here - internal Docker network communication only
                # nosemgrep: python.lang.security.audit.insecure-transport.requests.request-with-http.request-with-http
                url,
                json={
                    "patient_id": patient_id,
                    "incident_id": incident_id,
                    "amount": amount,
                },
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
        except requests.exceptions.RequestException as e:
            print(f"Insurance service network error: {e}")
            return {
                "verified": False,
                "reason": "SERVICE_UNAVAILABLE",
                "message": str(e),
                "http_status": None,
            }

        try:
            payload = r.json()
        except ValueError:
            payload = {}

        if r.status_code == 200 and payload.get("verified") is True:
            print(f"Insurance verification successful: {payload.get('message', '')}")
            return {
                "verified": True,
                "reason": "OK",
                "message": payload.get("message", ""),
                "http_status": 200,
            }

        if r.status_code == 404:
            msg = payload.get("message", "No active policy found for patient")
            print(f"Insurance policy not found for patient {patient_id}: {msg}")
            return {
                "verified": False,
                "reason": "NO_POLICY",
                "message": msg,
                "http_status": 404,
            }

        if r.status_code == 402:
            msg = payload.get("message", "Insufficient coverage")
            print(f"Insurance insufficient coverage for patient {patient_id}: {msg}")
            return {
                "verified": False,
                "reason": "INSUFFICIENT_COVERAGE",
                "message": msg,
                "http_status": 402,
            }

        msg = payload.get("error") or payload.get("message") or r.text
        print(f"Insurance service error ({r.status_code}): {msg}")
        return {
            "verified": False,
            "reason": "SERVICE_ERROR",
            "message": msg,
            "http_status": r.status_code,
        }

    except Exception as e:
        print(f"Unexpected error during insurance verification: {str(e)}")
        return {
            "verified": False,
            "reason": "SERVICE_ERROR",
            "message": str(e),
            "http_status": None,
        }


def process_payment(patient_id, amount, description):
    """Process payment using Stripe. Amount is in cents.

    Returns:
        dict: {
            'success': bool,  # Whether the payment was successful
            'payment_intent_id': str or None,  # The payment intent ID if successful
            'error': str or None  # Error message if payment failed
        }
    """
    try:
        # Convert amount from cents to dollars for the service
        amount_dollars = float(amount) / 100

        # Call Stripe service to process payment
        result = stripe_service.process_stripe_payment(
            amount=amount_dollars, description=description
        )

        if result["success"]:
            return {
                "success": True,
                "payment_intent_id": result["payment_intent_id"],
                "error": None,
            }
        else:
            error_msg = result.get("error", "Unknown error")
            print(f"Payment processing failed: {error_msg}")
            return {"success": False, "payment_intent_id": None, "error": error_msg}

    except Exception as e:
        error_msg = str(e)
        print(f"Error in process_payment: {error_msg}")
        return {"success": False, "payment_intent_id": None, "error": error_msg}


def consume():
    """Start the RabbitMQ consumer."""
    consumer_tag = None
    try:
        amqp.connect()

        # Capture the consumer tag so we can cancel cleanly during teardown
        consumer_tag = amqp.channel.basic_consume(
            queue=amqp.queue_name, on_message_callback=callback, auto_ack=True
        )

        print(" [*] Waiting for messages. To exit press CTRL+C")

        while not should_stop:
            try:
                amqp.connection.process_data_events()
                amqp.connection.sleep(1)
            except pika.exceptions.AMQPConnectionError:
                if should_stop:
                    # We're stopping anyway; don't reconnect during teardown.
                    break
                print("Connection lost. Attempting to reconnect...")
                amqp.connect()
                consumer_tag = amqp.channel.basic_consume(
                    queue=amqp.queue_name, on_message_callback=callback, auto_ack=True
                )

    except KeyboardInterrupt:
        print("Interrupted")
    except Exception as e:
        print(f"Error in consumer: {e}")
    finally:
        try:
            if consumer_tag and amqp.channel and amqp.channel.is_open:
                amqp.channel.basic_cancel(consumer_tag=consumer_tag)
        except Exception:
            pass

        if hasattr(amqp, "connection") and amqp.connection and amqp.connection.is_open:
            amqp.close()


# Initialize Flask app
app = Flask(__name__)


@app.route("/health")
def health_check():
    """Health check endpoint to verify service is running.

    Returns:
        tuple: A JSON response with service status and a status code.
    """
    status = {"status": "healthy", "service": "billings", "version": "1.0.0"}
    status_code = 200

    # Check database connection
    try:
        cnx = mysql.connector.connect(**{**DB_CONFIG, "connection_timeout": 5})
        cursor = cnx.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        if not result or result[0] != 1:
            raise Exception("Unexpected database response")
        status["database"] = "connected"
    except Exception as e:
        status.update(
            {"status": "unhealthy", "database": "connection failed", "error": str(e)}
        )
        status_code = 500
    finally:
        if "cnx" in locals() and cnx.is_connected():
            cnx.close()

    status["timestamp"] = datetime.datetime.utcnow().isoformat()
    return jsonify(status), status_code


def run_flask_app():
    """Run the Flask app in a separate thread."""
    port = int(os.environ.get("PORT", "5100"))
    print(f"Starting Flask server on port {port}")
    # nosemgrep: python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    print("Starting billings service...")
    print(f"Environment PORT: {os.environ.get('PORT')}")
    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    # Start the consumer in the main thread
    print("Starting RabbitMQ consumer...")
    consume()
