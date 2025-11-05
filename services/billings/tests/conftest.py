import pytest
import sys
import importlib
import os

# ensure the src folder is in sys.path
sys.path.insert(0, os.path.abspath("src"))

from src.app import db      # if you need app.py
import amqp_setup   # your amqp_setup.py

@pytest.fixture
def em_module():
    # ensure env is set BEFORE import
    if "amqp_setup" in sys.modules:
        del sys.modules["amqp_setup"]
    import amqp_setup  # noqa

    return importlib.reload(sys.modules["amqp_setup"])


@pytest.fixture
def em(em_module):
    return em_module.AMQPSetup()


@pytest.fixture
def client():
    from src import app

    app.app.config["TESTING"] = True

    with app.app.app_context():
        with app.db.engine.begin() as connection:
            from sqlalchemy import text

            connection.execute(text("DROP TABLE IF EXISTS `billings`;"))

            connection.execute(
                text("""
            CREATE TABLE `billings` (
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8;
            """)
            )

    return app.app.test_client()
