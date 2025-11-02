"""Insurance service API for managing insurance policies."""

import os
import socket
import mysql.connector
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Database configuration
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 3306)),
    'user': os.environ.get('DB_USER', 'admin'),
    'password': os.environ.get('DB_PASSWORD', 'cs302'),
    'database': os.environ.get('DB_NAME', 'cs302DB')
}

def get_db_connection():
    """Create and return a new database connection."""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection
    except mysql.connector.Error as err:
        print(f"Error connecting to database: {err}")
        raise


# --- Health Check ---
@app.route("/health")
def health_check():
    """Health check endpoint."""
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    # Check database connection
    db_status = "ok"
    try:
        conn = get_db_connection()
        conn.ping(reconnect=True, attempts=3, delay=5)
        conn.close()
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return (
        jsonify(
            {
                "message": "Service is healthy.",
                "service": "insurance",
                "ip_address": local_ip,
                "database": db_status,
                "environment": {
                    "db_host": os.environ.get('DB_HOST', 'not set'),
                    "db_name": os.environ.get('DB_NAME', 'not set')
                }
            }
        ),
        200 if db_status == "ok" else 500,
    )


# --- Get all policies ---
@app.route("/insurance")
def get_all_policies():
    """Get all insurance policies."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT policy_id, patient_id, provider_name, 
                   coverage_amount, created_at, updated_at
            FROM insurance_policies
        """
        cursor.execute(query)
        policies = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        if policies:
            return jsonify({"data": {"policies": policies}}), 200
        return jsonify({"message": "There are no insurance policies."}), 404
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Get one policy by ID ---
@app.route("/insurance/<int:policy_id>")
def find_by_id(policy_id):
    """Find policy by ID."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT policy_id, patient_id, provider_name, 
                   coverage_amount, created_at, updated_at
            FROM insurance_policies
            WHERE policy_id = %s
        """
        cursor.execute(query, (policy_id,))
        policy = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if policy:
            return jsonify({"data": policy}), 200
        return jsonify({"message": "Policy not found."}), 404
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if policy:
        return jsonify({"data": policy.to_dict()}), 200
    return jsonify({"message": "Policy not found."}), 404


# --- Create a new policy ---
@app.route("/insurance", methods=["POST"])
def create_policy():
    """Create a new insurance policy."""
    try:
        data = request.get_json()
        required_fields = ["patient_id", "provider_name", "coverage_amount"]
        if not all(field in data for field in required_fields):
            return (
                jsonify(
                    {
                        "error": "Missing required fields. Required: patient_id, provider_name, coverage_amount"
                    }
                ),
                400,
            )

        conn = get_db_connection()
        cursor = conn.cursor()
        
        query = """
            INSERT INTO insurance_policies 
                (patient_id, provider_name, coverage_amount, created_at, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW())
        """
        values = (
            data["patient_id"],
            data["provider_name"],
            float(data["coverage_amount"])
        )
        
        cursor.execute(query, values)
        policy_id = cursor.lastrowid
        
        # Fetch the created policy
        cursor.execute(
            """
            SELECT policy_id, patient_id, provider_name, 
                   coverage_amount, created_at, updated_at
            FROM insurance_policies 
            WHERE policy_id = %s
            """,
            (policy_id,)
        )
        new_policy = cursor.fetchone()
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({"data": new_policy}), 201

    except ValueError as e:
        return jsonify({"error": f"Invalid data format: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to create policy: {str(e)}"}), 500


# --- Update policy ---
@app.route("/insurance/<int:policy_id>", methods=["PATCH"])
def update_policy(policy_id):
    """Update policy information."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No update data provided"}), 400
            
        # Only allow specific fields to be updated
        allowed_fields = ["provider_name", "coverage_amount"]
        update_fields = {k: v for k, v in data.items() if k in allowed_fields}
        
        if not update_fields:
            return jsonify({"error": "No valid fields to update"}), 400
            
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Build the update query dynamically
        set_clause = ", ".join([f"{field} = %s" for field in update_fields.keys()])
        values = list(update_fields.values())
        values.append(policy_id)
        
        query = f"""
            UPDATE insurance_policies 
            SET {set_clause}, updated_at = NOW()
            WHERE policy_id = %s
        """
        
        cursor.execute(query, values)
        
        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({"message": "Policy not found."}), 404
            
        # Fetch the updated policy
        cursor.execute(
            """
            SELECT policy_id, patient_id, provider_name, 
                   coverage_amount, created_at, updated_at
            FROM insurance_policies 
            WHERE policy_id = %s
            """,
            (policy_id,)
        )
        updated_policy = cursor.fetchone()
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({"data": updated_policy}), 200

    except Exception as e:
        return jsonify({"error": f"Failed to update policy: {str(e)}"}), 500


# --- Verify Insurance (Endpoint for Billing service) ---
@app.route("/insurance/verify", methods=["POST"])
def verify_insurance():
    """Verify insurance coverage for a patient.

    Billing service calls this endpoint with patient_id,
    incident_id, and amount.
    
    Returns:
        JSON response with verification status and details
    """
    try:
        data = request.get_json()
        required_fields = ["patient_id", "incident_id", "amount"]
        
        if not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        patient_id = data["patient_id"]
        amount = float(data["amount"])
        incident_id = data["incident_id"]

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Find active policy for patient
        query = """
            SELECT policy_id, patient_id, provider_name, 
                   coverage_amount, created_at, updated_at
            FROM insurance_policies 
            WHERE patient_id = %s 
            LIMIT 1
        """
        cursor.execute(query, (patient_id,))
        policy = cursor.fetchone()
        
        if not policy:
            cursor.close()
            conn.close()
            return (
                jsonify(
                    {
                        "verified": False,
                        "message": "No active policy found for patient",
                        "patient_id": patient_id,
                        "incident_id": incident_id,
                    }
                ),
                404,
            )

        # Check if coverage is sufficient
        if policy["coverage_amount"] < amount:
            cursor.close()
            conn.close()
            return (
                jsonify(
                    {
                        "verified": False,
                        "message": "Insufficient coverage",
                        "patient_id": patient_id,
                        "incident_id": incident_id,
                        "coverage_amount": policy["coverage_amount"],
                        "billed_amount": amount,
                    }
                ),
                402,
            )

        cursor.close()
        conn.close()
        
        # If we get here, verification is successful
        return (
            jsonify(
                {
                    "verified": True,
                    "message": "Insurance verification successful",
                    "patient_id": patient_id,
                    "incident_id": incident_id,
                    "policy_id": policy["policy_id"],
                    "provider_name": policy["provider_name"],
                    "coverage_amount": float(policy["coverage_amount"]),
                    "billed_amount": amount,
                }
            ),
            200,
        )

    except ValueError as e:
        return jsonify({"error": f"Invalid data format: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Verification failed: {str(e)}"}), 500


if __name__ == "__main__":
    # Create database tables if they don't exist
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create insurance_policies table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS insurance_policies (
                policy_id INT AUTO_INCREMENT PRIMARY KEY,
                patient_id VARCHAR(64) NOT NULL,
                provider_name VARCHAR(128) NOT NULL,
                coverage_amount DECIMAL(10, 2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_patient_id (patient_id),
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("Database tables verified/created successfully")
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
    
    # Start the Flask application
    app.run(host="0.0.0.0", port=5200, debug=True)
