/* eslint-env jest */
const request = require("supertest");

// Add Jest globals
/* global jest, test, expect, beforeAll, afterAll, beforeEach, describe */

process.env.NODE_ENV = "test";
process.env.DB_HOST = "localhost";
process.env.DB_PORT = "3306";
process.env.DB_USER = "testuser";
process.env.DB_PASSWORD = "testpass";
process.env.DB_NAME = "test_insurance";

// Create mock connection object
const mockConnection = {
  query: jest.fn(),
  release: jest.fn(),
  ping: jest.fn().mockResolvedValue(undefined),
  execute: jest.fn(),
};

// Create mock pool object
const mockPool = {
  getConnection: jest.fn().mockResolvedValue(mockConnection),
  query: jest.fn(),
  execute: jest.fn(),
  end: jest.fn().mockResolvedValue(),
};

// Mock mysql2/promise 
jest.mock("mysql2/promise", () => ({
  createPool: jest.fn(() => mockPool),
}));

// Import the app
const app = require("../src/app");

// Mock the console to avoid cluttering test output
const originalConsole = { ...console };
beforeAll(() => {
  global.console = {
    ...originalConsole,
    log: jest.fn(),
    error: jest.fn(),
    warn: jest.fn(),
  };
});

afterAll(() => {
  global.console = originalConsole;
  // Clear all mocks
  jest.clearAllMocks();
  
  // Close database connections
  if (mockPool) {
    mockPool.end();
  }
});

describe("Insurance Service API Tests", () => {
  let server;
  let testPort;

  beforeAll(async () => {
    testPort = 0;
    
    return new Promise((resolve) => {
      server = app.listen(testPort, () => {
        testPort = server.address().port;
        console.log(`Test server listening on port ${testPort}`);
        resolve();
      });
    });
  });

  afterAll((done) => {
    if (server) {
      server.close(done);
    } else {
      done();
    }
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe("Health Check Endpoints", () => {
    test("should return 200 for health check", async () => {
      mockConnection.ping.mockResolvedValueOnce(undefined);

      const response = await request(app).get("/health");
      expect(response.status).toBe(200);
      expect(response.body).toHaveProperty("message");
      expect(response.body.service).toBe("insurance");
    });

    test("should return 200 for test connection", async () => {
      const response = await request(app).get("/test-connection");
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.service).toBe("insurance");
    });

    test("should return 200 for test database connection", async () => {
      mockConnection.query.mockResolvedValueOnce([[{ test: 1 }]]);

      const response = await request(app).get("/test-db");
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.message).toBe("Database connection successful");
    });
  });

  describe("Policy Management", () => {
    const testPolicy = {
      policy_id: 1,
      patient_id: 1,
      provider_name: "Test Insurance",
      coverage_amount: 10000.0,
      created_at: "2025-01-01T00:00:00.000Z",
      updated_at: "2025-01-01T00:00:00.000Z",
    };

    test("should return 404 for empty policies list", async () => {
      // Mock empty result set - app uses pool.query directly
      mockPool.query.mockResolvedValueOnce([[]]);

      const response = await request(app).get("/insurance");
      expect(response.status).toBe(404);
      expect(response.body.message).toBe("There are no insurance policies.");
    });

    test("should return all policies", async () => {
      // Mock successful query with test data - app uses pool.query
      mockPool.query.mockResolvedValueOnce([[testPolicy]]);

      const response = await request(app).get("/insurance");
      expect(response.status).toBe(200);
      expect(response.body.data).toHaveProperty("policies");
      expect(Array.isArray(response.body.data.policies)).toBe(true);
      expect(response.body.data.policies[0]).toMatchObject(testPolicy);
    });

    test("should return policy by ID", async () => {
      // Mock successful query for specific policy - app uses pool.query
      mockPool.query.mockResolvedValueOnce([[testPolicy]]);

      const response = await request(app).get("/insurance/1");
      expect(response.status).toBe(200);
      expect(response.body.data).toMatchObject(testPolicy);
    });

    test("should return 404 for non-existent policy", async () => {
      // Mock empty result for non-existent policy
      mockPool.query.mockResolvedValueOnce([[]]);

      const response = await request(app).get("/insurance/999");
      expect(response.status).toBe(404);
      expect(response.body.message).toContain("not found");
    });

    test("should create a new policy", async () => {
      const newPolicy = {
        patient_id: 2,
        provider_name: "New Insurance",
        coverage_amount: 5000.0,
      };

      mockPool.query
        .mockResolvedValueOnce([{ insertId: 2 }])
        .mockResolvedValueOnce([
          [
            {
              policy_id: 2,
              ...newPolicy,
              created_at: "2025-01-01T00:00:00.000Z",
              updated_at: "2025-01-01T00:00:00.000Z",
            },
          ],
        ]);

      const response = await request(app).post("/insurance").send(newPolicy);

      expect(response.status).toBe(201);
      expect(response.body.data.patient_id).toBe(newPolicy.patient_id);
      expect(response.body.data.provider_name).toBe(newPolicy.provider_name);
      expect(response.body.data.coverage_amount).toBe(
        newPolicy.coverage_amount
      );
    });

    test("should update an existing policy", async () => {
      const updateData = {
        provider_name: "Updated Insurance",
        coverage_amount: 15000.0,
      };

      mockPool.query
        .mockResolvedValueOnce([{ affectedRows: 1 }])
        .mockResolvedValueOnce([
          [
            {
              ...testPolicy,
              ...updateData,
            },
          ],
        ]);

      const response = await request(app).put("/insurance/1").send(updateData);

      expect(response.status).toBe(200);
      expect(response.body.data.provider_name).toBe(updateData.provider_name);
      expect(response.body.data.coverage_amount).toBe(
        updateData.coverage_amount
      );
    });
  });

  describe("Insurance Verification", () => {
    const testPolicy = {
      policy_id: 1,
      patient_id: "123",
      provider_name: "Test Insurance",
      coverage_amount: 10000.0,
    };

    test("should verify insurance with sufficient coverage", async () => {
      mockPool.query.mockResolvedValueOnce([[testPolicy]]);

      const verificationRequest = {
        patient_id: "123",
        incident_id: "inc-123",
        amount: 5000.0,
      };

      const response = await request(app)
        .post("/insurance/verify")
        .send(verificationRequest);

      expect(response.status).toBe(200);
      expect(response.body.verified).toBe(true);
      expect(response.body.details.coverage_status).toBe("fully_covered");
      expect(response.body.details.covered_amount).toBe(5000.0);
      expect(response.body.details.remaining_coverage).toBe(5000.0);
    });

    test("should handle non-existent patient", async () => {
      mockPool.query.mockResolvedValueOnce([[]]);

      const verificationRequest = {
        patient_id: "nonexistent",
        incident_id: "inc-123",
        amount: 5000.0,
      };

      const response = await request(app)
        .post("/insurance/verify")
        .send(verificationRequest);

      expect(response.status).toBe(404);
      expect(response.body.verified).toBe(false);
      expect(response.body.message).toContain("No insurance policy found");
    });

    test("should handle partial coverage", async () => {
      mockPool.query.mockResolvedValueOnce([[testPolicy]]);

      const verificationRequest = {
        patient_id: "123",
        incident_id: "inc-123",
        amount: 15000.0,
      };

      const response = await request(app)
        .post("/insurance/verify")
        .send(verificationRequest);

      expect(response.status).toBe(200);
      expect(response.body.verified).toBe(true);
      expect(response.body.details.coverage_status).toBe("partially_covered");
      expect(response.body.details.covered_amount).toBe(10000.0);
      expect(response.body.details.remaining_coverage).toBe(0);
    });
  });

  describe("Error Handling", () => {
    test("should return 404 for non-existent policy", async () => {
      mockPool.query.mockResolvedValueOnce([[]]);

      const response = await request(app).get("/insurance/999");
      expect(response.status).toBe(404);
      expect(response.body.message).toContain("not found");
    });

    test("should handle database errors", async () => {
      mockPool.query.mockRejectedValueOnce(new Error("Database error"));

      const response = await request(app).get("/insurance");
      expect(response.status).toBe(500);
      expect(response.body.error).toBeDefined();
    });
  });
});
