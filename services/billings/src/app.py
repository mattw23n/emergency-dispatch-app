"""Billing service API for managing medical billing and payments."""
import os
import socket
from datetime import datetime

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# --- Database Configuration ---
if os.environ.get("db_conn"):
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        os.environ.get("db_conn") + "/billing"
    )
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "mysql+mysqlconnector://cs302:cs302@localhost:3306/billing"
    )

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_size": 100,
    "pool_recycle": 280,
}

db = SQLAlchemy(app)
CORS(app)

# --- External Service Configuration ---
INSURANCE_API_URL = os.environ.get(
    "INSURANCE_API_URL", "http://localhost:5200/insurance/verify"
)


# --- Models ---
class Billing(db.Model):
    """Billing model for storing billing records."""

    __tablename__ = "billing"

    billing_id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.String(64), nullable=False)
    patient_id = db.Column(db.String(64), nullable=False)
    amount = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="PENDING")
    insurance_verified = db.Column(db.Boolean, default=False)
    payment_reference = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(
        db.DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self):
        """Convert billing record to dictionary."""
        return {
            "billing_id": self.billing_id,
            "incident_id": self.incident_id,
            "patient_id": self.patient_id,
            "amount": self.amount,
            "status": self.status,
            "insurance_verified": self.insurance_verified,
            "payment_reference": self.payment_reference,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# --- Health Check ---
@app.route("/health")
def health_check():
    """Health check endpoint."""
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    return (
        jsonify(
            {
                "message": "Service is healthy.",
                "service": "billings",
                "ip_address": local_ip,
            }
        ),
        200,
    )


# --- Get all billings ---
@app.route("/billings")
def get_all():
    """Get all billing records."""
    billing_list = db.session.scalars(db.select(Billing)).all()
    if billing_list:
        return (
            jsonify(
                {"data": {"billings": [b.to_dict() for b in billing_list]}}
            ),
            200,
        )
    return jsonify({"message": "There are no billings."}), 404


# --- Get a billing by ID ---
@app.route("/billings/<int:billing_id>")
def find_by_id(billing_id):
    """Find billing by ID."""
    billing = db.session.scalar(
        db.select(Billing).filter_by(billing_id=billing_id)
    )
    if billing:
        return jsonify({"data": billing.to_dict()}), 200
    return jsonify({"message": "Billing not found."}), 404


# --- Create new billing ---
@app.route("/billings", methods=["POST"])
def create_billing():
    """Create a new billing record."""
    try:
        data = request.get_json()
        incident_id = data.get("incident_id")
        patient_id = data.get("patient_id")
        amount = data.get("amount")

        billing = Billing(
            incident_id=incident_id,
            patient_id=patient_id,
            amount=amount,
            status="PENDING",
        )

        db.session.add(billing)
        db.session.commit()
        return jsonify({"data": billing.to_dict()}), 201
    except Exception as e:
        return (
            jsonify(
                {
                    "message": "An error occurred while creating billing.",
                    "error": str(e),
                }
            ),
            500,
        )


# --- Update billing status (e.g., insurance verified, paid, failed) ---
@app.route("/billings/<int:billing_id>", methods=["PATCH"])
def update_billing(billing_id):
    """Update billing status."""
    billing = db.session.scalar(
        db.select(Billing)
        .with_for_update(of=Billing)
        .filter_by(billing_id=billing_id)
    )
    if not billing:
        return jsonify({"message": "Billing not found."}), 404

    data = request.get_json()
    if "status" in data:
        billing.status = data["status"]
    if "insurance_verified" in data:
        billing.insurance_verified = data["insurance_verified"]
    if "payment_reference" in data:
        billing.payment_reference = data["payment_reference"]

    try:
        db.session.commit()
        return jsonify({"data": billing.to_dict()}), 200
    except Exception as e:
        return (
            jsonify(
                {
                    "message": "An error occurred updating billing.",
                    "error": str(e),
                }
            ),
            500,
        )


# --- Insurance Verification (Saga Step 1) ---
@app.route("/billings/<int:billing_id>/verify-insurance", methods=["POST"])
def verify_insurance(billing_id):
    """Verify insurance for a billing."""
    billing = db.session.scalar(
        db.select(Billing).filter_by(billing_id=billing_id)
    )
    if not billing:
        return jsonify({"message": "Billing not found."}), 404

    try:
        # Call the Insurance Service
        payload = {
            "patient_id": billing.patient_id,
            "incident_id": billing.incident_id,
            "amount": billing.amount,
        }

        response = requests.post(INSURANCE_API_URL, json=payload, timeout=5)

        if response.status_code == 200 and response.json().get("verified"):
            billing.insurance_verified = True
            billing.status = "VERIFIED"
        else:
            billing.insurance_verified = False
            billing.status = "INSURANCE_FAILED"

        db.session.commit()

        return (
            jsonify(
                {
                    "data": billing.to_dict(),
                    "insurance_response": response.json(),
                }
            ),
            response.status_code,
        )

    except requests.exceptions.ConnectionError:
        return (
            jsonify(
                {
                    "message": "Unable to reach insurance service.",
                    "service_url": INSURANCE_API_URL,
                }
            ),
            503,
        )
    except Exception as e:
        return (
            jsonify(
                {
                    "message": "Insurance verification failed.",
                    "error": str(e),
                }
            ),
            500,
        )


# --- Simulated Payment Processing (Saga Step 2) ---
@app.route("/billings/<int:billing_id>/process-payment", methods=["POST"])
def process_payment(billing_id):
    """Process payment for a billing."""
    billing = db.session.scalar(
        db.select(Billing).filter_by(billing_id=billing_id)
    )
    if not billing:
        return jsonify({"message": "Billing not found."}), 404

    if not billing.insurance_verified:
        return (
            jsonify(
                {
                    "message": (
                        "Insurance not verified. Cannot process payment."
                    )
                }
            ),
            400,
        )

    try:
        from stripe_service import process_stripe_payment

        # Call Stripe to create a payment intent
        result = process_stripe_payment(billing.amount)

        if not result["success"]:
            billing.status = "PAYMENT_FAILED"
            db.session.commit()
            return (
                jsonify(
                    {
                        "message": "Stripe payment failed.",
                        "error": result["error"],
                    }
                ),
                400,
            )

        # Update billing record
        billing.status = "PAID"
        billing.payment_reference = result["payment_intent_id"]
        db.session.commit()

        return (
            jsonify(
                {
                    "message": "Payment successful.",
                    "data": {
                        "billing": billing.to_dict(),
                        "stripe_client_secret": result["client_secret"],
                    },
                }
            ),
            200,
        )

    except Exception as e:
        return (
            jsonify(
                {
                    "message": "Payment processing failed.",
                    "error": str(e),
                }
            ),
            500,
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5100, debug=True)
