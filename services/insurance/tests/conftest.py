"""Test configuration and fixtures for insurance service."""

import os
import pytest
import mysql.connector


@pytest.fixture(scope="session")
def db_connection():
    """Create database connection for tests."""
    connection = mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=int(os.environ.get('DB_PORT', 3306)),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASSWORD', 'root'),
        database=os.environ.get('DB_NAME', 'cs302DB')
    )
    yield connection
    connection.close()


@pytest.fixture
def setup_database(db_connection):
    """Setup fresh test data before each test."""
    cursor = db_connection.cursor()
    
    # Clean existing test data
    cursor.execute("DELETE FROM insurance_policies WHERE patient_id LIKE 'TEST%'")
    db_connection.commit()
    
    # Insert test data
    test_policies = [
        ('TEST001', 'AIA Singapore', 3000.00),
        ('TEST002', 'Prudential', 1500.00),
    ]
    
    cursor.executemany(
        "INSERT INTO insurance_policies (patient_id, provider_name, coverage_amount) VALUES (%s, %s, %s)",
        test_policies
    )
    db_connection.commit()
    
    yield
    
    # Cleanup after test
    cursor.execute("DELETE FROM insurance_policies WHERE patient_id LIKE 'TEST%'")
    db_connection.commit()
    cursor.close()


@pytest.fixture
def service_url():
    """Return the base URL for the insurance service."""
    return os.environ.get('INSURANCE_SERVICE_URL', 'http://localhost:5200')
