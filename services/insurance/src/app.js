require("dotenv").config();
const express = require("express");
const cors = require("cors");
const mysql = require("mysql2/promise");
const os = require("os");

const app = express();
const port = 5200;
console.log(`Starting insurance service on port ${port}`);

// Middleware
app.use(cors());
app.use(express.json());

// Database configuration
const getDbConfig = () => {
  const requiredVars = [
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_PASSWORD",
    "DB_NAME",
  ];
  const missingVars = requiredVars.filter((varName) => !process.env[varName]);

  if (missingVars.length > 0) {
    throw new Error(
      `Missing required environment variables: ${missingVars.join(", ")}`
    );
  }

  return {
    host: process.env.DB_HOST,
    port: parseInt(process.env.DB_PORT, 10),
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    database: process.env.DB_NAME,
    waitForConnections: true,
    connectionLimit: 10,
    queueLimit: 0,
  };
};

// Create a connection pool
const pool = mysql.createPool(getDbConfig());

// Test connection from billing service
app.get("/test-connection", (req, res) => {
  console.log("Received test connection from:", req.ip);
  res.json({
    success: true,
    message: "Connection successful",
    service: "insurance",
  });
});

// Test database connection
app.get("/test-db", async (req, res) => {
  try {
    const connection = await pool.getConnection();
    const [rows] = await connection.query("SELECT 1 as test");
    connection.release();
    res.json({
      success: true,
      message: "Database connection successful",
      data: rows,
    });
  } catch (error) {
    console.error("Database connection error:", error);
    res
      .status(500)
      .json({
        success: false,
        message: "Database connection failed",
        error: error.message,
      });
  }
});

// Health check endpoint
app.get("/health", async (req, res) => {
  const hostname = os.hostname();
  const networkInterfaces = os.networkInterfaces();
  const localIp = networkInterfaces.eth0?.[0]?.address || "127.0.0.1";

  let dbStatus = "ok";
  try {
    const connection = await pool.getConnection();
    await connection.ping();
    connection.release();
  } catch (error) {
    dbStatus = `error: ${error.message}`;
  }

  const response = {
    message: "Service is healthy.",
    service: "insurance",
    ip_address: localIp,
    database: dbStatus,
    environment: {
      db_host: process.env.DB_HOST || "not set",
      db_name: process.env.DB_NAME || "not set",
    },
  };

  res.status(dbStatus === "ok" ? 200 : 500).json(response);
});

// Get all policies
app.get("/insurance", async (req, res) => {
  try {
    const [policies] = await pool.query(`
      SELECT policy_id, patient_id, provider_name, 
             coverage_amount, created_at, updated_at
      FROM insurance_policies
    `);

    if (policies.length > 0) {
      return res.status(200).json({ data: { policies } });
    }
    return res
      .status(404)
      .json({ message: "There are no insurance policies." });
  } catch (error) {
    console.error("Error fetching policies:", error);
    return res.status(500).json({ error: error.message });
  }
});

// Get policy by ID
app.get("/insurance/:id", async (req, res) => {
  try {
    const { id } = req.params;
    const [policies] = await pool.query(
      "SELECT * FROM insurance_policies WHERE policy_id = ?",
      [id]
    );

    if (policies.length === 0) {
      return res.status(404).json({ message: "Policy not found" });
    }

    return res.status(200).json({ data: policies[0] });
  } catch (error) {
    console.error("Error fetching policy:", error);
    return res.status(500).json({ error: error.message });
  }
});

// Create new policy
app.post("/insurance", async (req, res) => {
  try {
    const { patient_id, provider_name, coverage_amount } = req.body;

    if (!patient_id || !provider_name || !coverage_amount) {
      return res.status(400).json({
        error:
          "Missing required fields: patient_id, provider_name, coverage_amount",
      });
    }

    const [result] = await pool.query(
      `INSERT INTO insurance_policies 
       (patient_id, provider_name, coverage_amount)
       VALUES (?, ?, ?)`,
      [patient_id, provider_name, coverage_amount]
    );

    const [newPolicy] = await pool.query(
      "SELECT * FROM insurance_policies WHERE policy_id = ?",
      [result.insertId]
    );

    return res.status(201).json({ data: newPolicy[0] });
  } catch (error) {
    console.error("Error creating policy:", error);
    return res.status(500).json({ error: error.message });
  }
});

// Update policy
app.put("/insurance/:id", async (req, res) => {
  try {
    const { id } = req.params;
    const { provider_name, coverage_amount } = req.body;

    const [result] = await pool.query(
      "UPDATE insurance_policies SET provider_name = ?, coverage_amount = ? WHERE policy_id = ?",
      [provider_name, coverage_amount, id]
    );

    if (result.affectedRows === 0) {
      return res.status(404).json({ message: "Policy not found" });
    }

    const [updatedPolicy] = await pool.query(
      "SELECT * FROM insurance_policies WHERE policy_id = ?",
      [id]
    );

    return res.status(200).json({ data: updatedPolicy[0] });
  } catch (error) {
    console.error("Error updating policy:", error);
    return res.status(500).json({ error: error.message });
  }
});

// Verify insurance
app.post("/insurance/verify", async (req, res) => {
  try {
    const { patient_id, incident_id, amount } = req.body;

    if (!patient_id || !incident_id || amount === undefined) {
      return res.status(400).json({
        error: "Missing required fields: patient_id, incident_id, amount",
      });
    }

    const [policies] = await pool.query(
      "SELECT * FROM insurance_policies WHERE patient_id = ?",
      [patient_id]
    );

    if (policies.length === 0) {
      return res.status(404).json({
        verified: false,
        message: "No insurance policy found for this patient",
        details: {
          patient_id,
          incident_id,
          coverage_status: "not_covered",
          covered_amount: 0,
          remaining_coverage: 0,
        },
      });
    }

    const policy = policies[0];
    const coveredAmount = Math.min(amount, policy.coverage_amount);
    const remainingCoverage = Math.max(0, policy.coverage_amount - amount);

    return res.status(200).json({
      verified: true,
      message: "Insurance verification successful",
      details: {
        patient_id,
        incident_id,
        policy_id: policy.policy_id,
        provider_name: policy.provider_name,
        coverage_status:
          coveredAmount >= amount ? "fully_covered" : "partially_covered",
        amount_requested: amount,
        covered_amount: coveredAmount,
        remaining_coverage: remainingCoverage,
      },
    });
  } catch (error) {
    console.error("Error verifying insurance:", error);
    return res.status(500).json({
      verified: false,
      error: error.message,
    });
  }
});

// Error handling middleware
app.use((err, req, res, next) => {
  console.error("Unhandled error:", err);
  res.status(500).json({
    error: "Internal server error",
    details: process.env.NODE_ENV === "development" ? err.message : undefined,
  });
});

// Start server
const server = app.listen(port, "0.0.0.0", () => {
  const host = server.address().address;
  const port = server.address().port;
  console.log(`Insurance service running at http://${host}:${port}`);
  console.log("Environment variables:", {
    DB_HOST: process.env.DB_HOST,
    DB_PORT: process.env.DB_PORT,
    DB_NAME: process.env.DB_NAME,
    NODE_ENV: process.env.NODE_ENV,
  });
});

module.exports = app;
