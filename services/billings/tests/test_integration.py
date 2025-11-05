import json
import pytest
import mysql.connector
import requests

# Test configuration
TEST_DB_CONFIG = {
    "host": "mysql",
    "port": 3306,
    "user": "cs302",
    "password": "",
    "database": "billings",
}


# Helper function to execute raw SQL queries
def execute_sql(query, params=None, fetch=False):
    cnx = mysql.connector.connect(**TEST_DB_CONFIG)
    cursor = cnx.cursor(dictionary=True)
    cursor.execute(query, params or ())

    if fetch:
        result = cursor.fetchall()
    else:
        cnx.commit()
        result = None

    cursor.close()
    cnx.close()
    return result


# Fixture to set up test database
@pytest.fixture(scope="module")
def setup_database():
    # Create test database and tables
    cnx = mysql.connector.connect(
        **{
            "host": TEST_DB_CONFIG["host"],
            "port": TEST_DB_CONFIG["port"],
            "user": TEST_DB_CONFIG["user"],
            "password": TEST_DB_CONFIG["password"],
        }
    )
    cursor = cnx.cursor()

    try:
        cursor.execute(f"DROP DATABASE IF EXISTS {TEST_DB_CONFIG['database']}")
        cursor.execute(f"CREATE DATABASE {TEST_DB_CONFIG['database']}")
        cursor.execute(f"USE {TEST_DB_CONFIG['database']}")

        # Create billings table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS billings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            incident_id VARCHAR(255) NOT NULL,
            patient_id VARCHAR(255) NOT NULL,
            amount DECIMAL(10, 2) NOT NULL,
            status VARCHAR(50) NOT NULL,
            insurance_verified BOOLEAN DEFAULT FALSE,
            payment_reference VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_incident_patient (incident_id, patient_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cnx.commit()
    finally:
        cursor.close()
        cnx.close()

    yield  # Test runs here

    # Clean up after tests
    cnx = mysql.connector.connect(
        **{
            "host": TEST_DB_CONFIG["host"],
            "port": TEST_DB_CONFIG["port"],
            "user": TEST_DB_CONFIG["user"],
            "password": TEST_DB_CONFIG["password"],
        }
    )
    cursor = cnx.cursor()
    cursor.execute(f"DROP DATABASE IF EXISTS {TEST_DB_CONFIG['database']}")
    cursor.close()
    cnx.close()


# Helper to create a test billing record
def create_test_billing(incident_id, patient_id, amount=100.00, status="pending"):
    query = """
    INSERT INTO billings (incident_id, patient_id, amount, status)
    VALUES (%s, %s, %s, %s)
    """
    execute_sql(query, (incident_id, patient_id, amount, status))

    # Get the inserted ID
    result = execute_sql(
        "SELECT id FROM billings WHERE incident_id = %s AND patient_id = %s ORDER BY created_at DESC LIMIT 1",
        (incident_id, patient_id),
        fetch=True,
    )
    return result[0]["id"]


def test_health_check(client):
    """Test the health check endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "healthy"


def test_create_and_retrieve_billing(client):
    """Test creating and retrieving a billing record through the API"""
    # Create a new billing record
    billing_data = {
        "incident_id": "inc-001",
        "patient_id": "patient-001",
        "amount": 150.50,
        "status": "pending",
    }

    # Test creating a billing record
    response = client.post("/billings", json=billing_data)
    assert response.status_code == 201
    billing = json.loads(response.data)
    assert billing["incident_id"] == billing_data["incident_id"]
    assert billing["patient_id"] == billing_data["patient_id"]
    assert float(billing["amount"]) == billing_data["amount"]

    # Test retrieving the billing record
    response = client.get(f"/billings/{billing['id']}")
    assert response.status_code == 200
    retrieved_billing = json.loads(response.data)
    assert retrieved_billing["id"] == billing["id"]


@pytest.mark.integration
def test_billing_lifecycle():
    """Test the complete billing lifecycle"""
    from src.app import update_billing_status, verify_insurance

    # Create a test billing record
    billing_id = create_test_billing("inc-002", "patient-002", 200.00)

    # Test insurance verification (assuming test insurance service is running)
    try:
        result = verify_insurance("inc-002", "patient-002")
        if result["verified"]:
            # Update billing status with insurance verification
            update_billing_status(
                id=billing_id,
                insurance_verified=True,
                payment_reference=None,
                status="verified",
            )

            # Verify the update
            updated = execute_sql(
                "SELECT * FROM billings WHERE id = %s", (billing_id,), fetch=True
            )
            assert updated[0]["status"] == "verified"
            assert updated[0]["insurance_verified"] == 1

    except requests.exceptions.RequestException as e:
        pytest.skip(f"Skipping integration test - Insurance service not available: {e}")


@pytest.mark.integration
def test_process_payment():
    """Test payment processing with Stripe"""
    from src.app import process_payment

    try:
        # This will actually try to process a test payment
        # Note: In a real test environment, you'd use Stripe test mode
        payment_intent = process_payment(1000, "Test payment")  # $10.00 in cents
        assert payment_intent is not None
    except Exception as e:
        pytest.skip(f"Skipping payment test - Stripe service not available: {e}")
