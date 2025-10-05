import os
import socket
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)

# --- Database Configuration ---
if os.environ.get('db_conn'):
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('db_conn') + '/insurance'
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://cs302:cs302@localhost:3306/insurance'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_size': 100, 'pool_recycle': 280}

db = SQLAlchemy(app)
CORS(app)


# --- Models ---
class InsurancePolicy(db.Model):
    __tablename__ = 'insurance_policies'

    policy_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.String(64), nullable=False)
    provider_name = db.Column(db.String(128), nullable=False)
    coverage_amount = db.Column(db.Float, nullable=False)
    policy_status = db.Column(db.String(20), nullable=False, default='ACTIVE')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "policy_id": self.policy_id,
            "patient_id": self.patient_id,
            "provider_name": self.provider_name,
            "coverage_amount": self.coverage_amount,
            "policy_status": self.policy_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }


# --- Health Check ---
@app.route("/health")
def health_check():
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    return jsonify({
        "message": "Service is healthy.",
        "service": "insurance",
        "ip_address": local_ip
    }), 200


# --- Get all policies ---
@app.route("/insurance")
def get_all_policies():
    policy_list = db.session.scalars(db.select(InsurancePolicy)).all()
    if policy_list:
        return jsonify({
            "data": {"policies": [p.to_dict() for p in policy_list]}
        }), 200
    return jsonify({"message": "There are no insurance policies."}), 404


# --- Get one policy by ID ---
@app.route("/insurance/<int:policy_id>")
def find_by_id(policy_id):
    policy = db.session.scalar(db.select(InsurancePolicy).filter_by(policy_id=policy_id))
    if policy:
        return jsonify({"data": policy.to_dict()}), 200
    return jsonify({"message": "Policy not found."}), 404


# --- Create a new policy ---
@app.route("/insurance", methods=['POST'])
def create_policy():
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        provider_name = data.get('provider_name')
        coverage_amount = data.get('coverage_amount')

        if not patient_id or not provider_name or not coverage_amount:
            return jsonify({"message": "Missing required fields."}), 400

        policy = InsurancePolicy(
            patient_id=patient_id,
            provider_name=provider_name,
            coverage_amount=coverage_amount,
            policy_status='ACTIVE'
        )

        db.session.add(policy)
        db.session.commit()

        return jsonify({"data": policy.to_dict()}), 201
    except Exception as e:
        return jsonify({
            "message": "An error occurred while creating policy.",
            "error": str(e)
        }), 500


# --- Update policy status ---
@app.route("/insurance/<int:policy_id>", methods=['PATCH'])
def update_policy(policy_id):
    policy = db.session.scalar(
        db.select(InsurancePolicy).with_for_update(of=InsurancePolicy).filter_by(policy_id=policy_id)
    )
    if not policy:
        return jsonify({"message": "Policy not found."}), 404

    data = request.get_json()
    if 'policy_status' in data:
        policy.policy_status = data['policy_status']

    try:
        db.session.commit()
        return jsonify({"data": policy.to_dict()}), 200
    except Exception as e:
        return jsonify({
            "message": "Error updating policy.",
            "error": str(e)
        }), 500


# --- Verify Insurance (Endpoint for Billing service) ---
@app.route("/insurance/verify", methods=['POST'])
def verify_insurance():
    """
    This simulates an external insurance verification process.
    Billing service would call this endpoint with patient_id, incident_id, and amount.
    """
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        amount = data.get('amount')

        # Find active policy for patient
        policy = db.session.scalar(db.select(InsurancePolicy).filter_by(patient_id=patient_id, policy_status='ACTIVE'))

        if not policy:
            return jsonify({
                "verified": False,
                "message": "No active insurance policy found."
            }), 404

        # Simulate verification logic
        if amount <= policy.coverage_amount:
            return jsonify({
                "verified": True,
                "provider_name": policy.provider_name,
                "coverage_amount": policy.coverage_amount,
                "message": "Insurance verified successfully."
            }), 200
        else:
            return jsonify({
                "verified": False,
                "message": "Amount exceeds coverage limit."
            }), 400

    except Exception as e:
        return jsonify({
            "message": "Insurance verification failed.",
            "error": str(e)
        }), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5200, debug=True)
