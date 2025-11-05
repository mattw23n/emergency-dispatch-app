import datetime
import json
import os
import signal
from threading import Thread
import time

import mysql.connector
import pika
from flask import Flask, jsonify

import amqp_setup

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
    global should_stop
    print("Stopping consumer...")
    should_stop = True

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

INSURANCE_API_URL = (os.environ.get("insurance_service_url_internal", "http://insurance:5200").rstrip("/") +
                     "/insurance/verify")

def callback(ch, method, properties, body):
    """Process billing initiation message."""
    cnx = None
    cursor = None
    billing_id = None
    try:
        # Parse message
        body_str = body.decode("utf-8").strip().rstrip(";").strip()
        message_body = json.loads(body_str)
        incident_id = message_body["incident_id"]
        patient_id = message_body["patient_id"]
        amount = message_body.get("amount", 100)

        # Insert billing record
        cnx = _mysql_connect_with_retries()
        cursor = cnx.cursor()
        cursor.execute(
            """
            INSERT INTO billings (incident_id, patient_id, amount)
            VALUES (%s, %s, %s)
            """,
            (incident_id, patient_id, amount),
        )
        cnx.commit()
        billing_id = cursor.lastrowid
        print(f"SUCCESS: Created billings {billing_id} for patient {patient_id}, amount {amount}")

        # --- Insurance verification
        v = verify_insurance(incident_id, patient_id, amount)

        # Support both dict (new) and bool (legacy) return types
        if isinstance(v, dict):
            insurance_verified = bool(v.get("verified"))
            reason = v.get("reason")
            reason_msg = v.get("message")
            http_status = v.get("http_status")
        else:
            insurance_verified = bool(v)
            reason = "OK" if insurance_verified else "SERVICE_ERROR"
            reason_msg = None
            http_status = None

        status_map = {
            "OK": "VERIFIED",
            "NO_POLICY": "INSURANCE_NOT_FOUND",
            "INSUFFICIENT_COVERAGE": "INSURANCE_INSUFFICIENT_COVERAGE",
            "SERVICE_UNAVAILABLE": "INSURANCE_SERVICE_UNAVAILABLE",
            "SERVICE_ERROR": "INSURANCE_VERIFICATION_FAILED",
        }
        billing_status = status_map.get(reason, "INSURANCE_VERIFICATION_FAILED")

        # --- Payment (only if verified)
        payment_reference = None
        if insurance_verified:
            try:
                amount_in_cents = int(float(amount) * 100)
                payment_reference = process_payment(
                    patient_id=patient_id,
                    amount=amount_in_cents,
                    description=f"Billing for incident {incident_id}",
                )
                billing_status = "PAID"
                print(f"SUCCESS: Payment processed for billings {billing_id}, reference: {payment_reference}")

                # Publish payment completion event
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

            except Exception as e:
                error_msg = str(e)
                print(f"FAIL: Payment processing failed: {error_msg}")
                billing_status = (
                    "PAYMENT_DECLINED"
                    if "card was declined" in error_msg.lower()
                    else "PAYMENT_FAILED"
                )

                # Publish payment failure event
                try:
                    status_msg = {
                        "billing_id": billing_id,
                        "incident_id": incident_id,
                        "patient_id": patient_id,
                        "amount": amount,
                        "status": billing_status,
                        "error": error_msg,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                    }
                    amqp.publish_status_update(status_msg, is_success=False)
                except Exception as notify_err:
                    print(f"WARNING: Failed to send failure notification: {notify_err}")
        else:
            # Not verified — publish specific insurance failure reason
            print(
                f"INFO: Insurance verification failed for billings {billing_id}: "
                f"{reason} ({http_status}) - {reason_msg}"
            )
            try:
                status_msg = {
                    "billing_id": billing_id,
                    "incident_id": incident_id,
                    "patient_id": patient_id,
                    "amount": amount,
                    "status": billing_status,  # INSURANCE_NOT_FOUND / INSUFFICIENT_COVERAGE / ...
                    "error": reason_msg,
                    "details": {"reason": reason, "http_status": http_status},
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                }
                amqp.publish_status_update(status_msg, is_success=False)
            except Exception as notify_err:
                print(f"WARNING: Failed to send insurance failure notification: {notify_err}")

        # --- Persist final status to DB
        update_billing_status(billing_id, insurance_verified, payment_reference, billing_status)

    except Exception as e:
        print(f"FAIL: Unexpected error: {str(e)}")
    finally:
        try:
            if cursor:
                cursor.close()
        finally:
            if cnx and cnx.is_connected():
                cnx.close()

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
    except mysql.connector.Error as err:
        print(f"Error updating billings status: {err}")
        raise
    finally:
        if "cnx" in locals() and cnx.is_connected():
            cursor.close()
            cnx.close()

def verify_insurance(incident_id, patient_id, amount=None):
    """
    Call insurance service and return a structured result:
    {
      "verified": bool,
      "reason": "OK|NO_POLICY|INSUFFICIENT_COVERAGE|SERVICE_UNAVAILABLE|SERVICE_ERROR",
      "message": str,
      "http_status": int|None
    }
    """
    import requests

    try:
        # Get amount from DB if not provided
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
            # HTTP is safe here - internal Docker network communication only
            # nosemgrep: python.lang.security.audit.insecure-transport.requests.request-with-http.request-with-http
            r = requests.post(
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
            # Real connectivity/timeout/DNS error
            print(f"Insurance service network error: {e}")
            return {
                "verified": False,
                "reason": "SERVICE_UNAVAILABLE",
                "message": str(e),
                "http_status": None,
            }

        # Try to decode JSON for messages even on non-200
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

        # Any other 4xx/5xx from insurance service
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
    """Process payment using Stripe. Amount is in cents."""
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
    """Health check endpoint to verify service is running."""
    status = {"status": "healthy", "service": "billings", "version": "1.0.0"}
    status_code = 200

    # Check database connection
    try:
        cnx = mysql.connector.connect(
            **{**DB_CONFIG, "connection_timeout": 5}
        )
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
