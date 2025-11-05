CREATE DATABASE IF NOT EXISTS cs302DB;
USE cs302DB;

-- Minimal billings table required by your code
DROP TABLE IF EXISTS billings;
CREATE TABLE billings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  incident_id VARCHAR(128) NOT NULL,
  patient_id  VARCHAR(128) NOT NULL,
  amount DECIMAL(10,2) NOT NULL DEFAULT 100.00,
  status VARCHAR(64) DEFAULT NULL,
  insurance_verified TINYINT(1) DEFAULT NULL,
  payment_reference VARCHAR(128) DEFAULT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX (incident_id),
  INDEX (patient_id)
);

-- Ensure app user exists (compose already creates it; grant anyway)
CREATE USER IF NOT EXISTS 'cs302'@'%' IDENTIFIED BY 'cs302pw';
GRANT ALL PRIVILEGES ON cs302DB.* TO 'cs302'@'%';
FLUSH PRIVILEGES;
