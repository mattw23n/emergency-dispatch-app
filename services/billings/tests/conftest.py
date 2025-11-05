edimport os
import pytest
import mysql.connector


@pytest.fixture(scope="session")
def db_connection():
    """Create a DB connection for tests (uses AWS RDS if available).
    Expects DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME in env vars or
    will use defaults suitable for local/CI test compose files.
    """
    conn = mysql.connector.connect(
        host=os.environ.get("DB_HOST", "database-302.c7c2ciii0dcn.ap-southeast-1.rds.amazonaws.com"),
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ.get("DB_USER", "admin"),
        password=os.environ.get("DB_PASSWORD", "c5fV9H4QGxTTfMynLPsT"),
        database=os.environ.get("DB_NAME", "cs302DB"),
        autocommit=True,
    )
    yield conn
    conn.close()


@pytest.fixture
def setup_database(db_connection):
    """Prepare test data for each test and clean up afterwards.
    It inserts bills with patient ids prefixed TEST- for easy cleanup.
    """
    cursor = db_connection.cursor()

    # Ensure table exists (best-effort - test environment should already have schema)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS billings (
            bill_id INT AUTO_INCREMENT PRIMARY KEY,
            incident_id VARCHAR(64),
            patient_id VARCHAR(64),
            amount DECIMAL(10,2),
            status VARCHAR(32) DEFAULT 'PENDING',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB;
        """
    )

    # Clean old test rows
    cursor.execute("DELETE FROM billings WHERE patient_id LIKE 'TEST-%'")

    # Seed two test bills
    seed = [
        ("INC-TEST-001", "TEST-001", 1500.00, "PENDING"),
        ("INC-TEST-002", "TEST-002", 500.00, "COMPLETED"),
    ]
    cursor.executemany(
        "INSERT INTO billings (incident_id, patient_id, amount, status) VALUES (%s, %s, %s, %s)",
        seed,
    )
    db_connection.commit()

    yield

    # Teardown: remove test rows
    cursor.execute("DELETE FROM billings WHERE patient_id LIKE 'TEST-%'")
    db_connection.commit()
    cursor.close()


@pytest.fixture
def service_url():
    """Return base URL for billings service used by tests."""
    return os.environ.get("BILLINGS_SERVICE_URL", "http://localhost:5100")
