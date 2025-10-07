import pytest
from sqlalchemy import text


@pytest.fixture
def client():
    from src import app  # assuming your Flask app is at src/app.py

    app.app.config["TESTING"] = True

    with app.app.app_context():
        with app.db.engine.begin() as connection:
            # Drop existing billing table (if any)
            connection.execute(text("DROP TABLE IF EXISTS `billing`;"))

            # Create fresh billing table
            connection.execute(
                text(
                    """
                CREATE TABLE `billing` (
                    `billing_id` int(11) NOT NULL AUTO_INCREMENT,
                    `incident_id` varchar(64) NOT NULL,
                    `patient_id` varchar(64) NOT NULL,
                    `amount` float DEFAULT NULL,
                    `status` varchar(20) NOT NULL DEFAULT 'PENDING',
                    `insurance_verified` tinyint(1) DEFAULT 0,
                    `payment_reference` varchar(128) DEFAULT NULL,
                    `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
                    `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (`billing_id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8;
            """
                )
            )

            # Seed some initial test data
            connection.execute(
                text(
                    """
                INSERT INTO `billing` (`billing_id`, `incident_id`, `patient_id`, `amount`, `status`, `insurance_verified`, `payment_reference`, `created_at`, `updated_at`)
                VALUES
                (1, 'INC12345', 'PAT001', 250.00, 'PENDING', 0, NULL, '2025-10-05 10:00:00', '2025-10-05 10:00:00'),
                (2, 'INC54321', 'PAT002', 500.00, 'VERIFIED', 1, NULL, '2025-10-05 10:05:00', '2025-10-05 10:05:00');
            """
                )
            )

    return app.app.test_client()
