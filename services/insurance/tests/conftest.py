"""Test configuration and fixtures for insurance service."""

import pytest
from sqlalchemy import text


@pytest.fixture
def client():
    """Create test client with fresh database."""
    from src import app

    app.app.config["TESTING"] = True

    # Prepare a clean DB state before each test session
    with app.app.app_context():
        with app.db.engine.begin() as connection:
            # Drop existing insurance table if exists
            connection.execute(
                text("DROP TABLE IF EXISTS `insurance_policies`;")
            )

            # Create fresh insurance_policies table
            connection.execute(
                text(
                    """
                CREATE TABLE `insurance_policies` (
                    `policy_id` int(11) NOT NULL AUTO_INCREMENT,
                    `patient_id` varchar(64) NOT NULL,
                    `provider_name` varchar(128) NOT NULL,
                    `coverage_amount` float NOT NULL,
                    `policy_status` varchar(20) NOT NULL DEFAULT 'ACTIVE',
                    `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
                    `updated_at` datetime DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (`policy_id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8;
            """
                )
            )

            # Seed initial test data
            connection.execute(
                text(
                    """
                INSERT INTO `insurance_policies`
                    (`policy_id`, `patient_id`, `provider_name`,
                     `coverage_amount`, `policy_status`,
                     `created_at`, `updated_at`)
                VALUES
                    (1, 'PAT001', 'AIA Singapore', 3000.00,
                     'ACTIVE', '2025-10-05 10:00:00',
                     '2025-10-05 10:00:00'),
                    (2, 'PAT002', 'Prudential', 1500.00,
                     'INACTIVE', '2025-10-05 10:05:00',
                     '2025-10-05 10:05:00');
            """
                )
            )

    # Return Flask test client
    return app.app.test_client()
