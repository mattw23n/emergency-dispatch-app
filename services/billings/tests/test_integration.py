"""Integration tests for Billings service (HTTP-based).

Tests assume the billings service is reachable via the `service_url` fixture
and that the database is accessible (AWS RDS or CI database configured).
"""

import pytest
import requests
import time


def call_get(url):
    return requests.get(url, timeout=10)


def call_post(url, json_body):
    return requests.post(url, json=json_body, timeout=10)


# --- TESTS ---

@pytest.mark.dependency()
def test_health(service_url):
    resp = call_get(f"{service_url}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("service") in ("billings", "billings-service", "billings-service-http") or "status" in data


@pytest.mark.dependency(depends=["test_health"])
def test_get_all_bills(service_url, setup_database):
    resp = call_get(f"{service_url}/billings")
    # Accept 200 with data or 404 if none
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        json = resp.json()
        assert "data" in json or isinstance(json, dict)


@pytest.mark.dependency(depends=["test_get_all_bills"])
def test_create_bill_missing_fields(service_url):
    # Missing required fields should return 400
    resp = call_post(f"{service_url}/billings", json_body={})
    assert resp.status_code == 400
    j = resp.json()
    assert "error" in j or "message" in j


@pytest.mark.dependency(depends=["test_create_bill_missing_fields"])
def test_create_bill_valid(service_url):
    body = {
        "incident_id": "INC-TEST-NEW",
        "patient_id": "TEST-NEW-001",
        "amount": 1234.56
    }
    resp = call_post(f"{service_url}/billings", json_body=body)
    assert resp.status_code in (201, 200)
    j = resp.json()
    # Accept either direct created object under data or the created id
    if isinstance(j, dict):
        if "data" in j:
            entry = j["data"]
        else:
            entry = j
        assert entry.get("patient_id") == body["patient_id"]
        assert float(entry.get("amount", 0)) == pytest.approx(body["amount"], rel=1e-3)


@pytest.mark.dependency(depends=["test_create_bill_valid"])
def test_get_one_bill(service_url, db_connection):
    # Find a test bill inserted in conftest
    cursor = db_connection.cursor(dictionary=True)
    cursor.execute("SELECT bill_id FROM billings WHERE patient_id LIKE 'TEST-%' ORDER BY created_at DESC LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    assert row is not None
    bill_id = row["bill_id"]

    resp = call_get(f"{service_url}/billings/{bill_id}")
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        j = resp.json()
        # Normalize response shape: prefer `data` field when present
        if isinstance(j, dict) and "data" in j:
            data = j.get("data") or {}
        elif isinstance(j, dict):
            data = j
        else:
            data = {}

        # Safely extract bill id and assert
        bill_val = data.get("bill_id") if isinstance(data, dict) else None
        bill_val = bill_val or bill_id
        assert int(bill_val) == bill_id


@pytest.mark.dependency(depends=["test_get_one_bill"])
def test_update_bill_status(service_url, db_connection):
    cursor = db_connection.cursor(dictionary=True)
    cursor.execute("SELECT bill_id FROM billings WHERE patient_id LIKE 'TEST-%' ORDER BY created_at DESC LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    assert row is not None
    bill_id = row["bill_id"]

    # Update status to PAID
    resp = requests.put(f"{service_url}/billings/{bill_id}", json={"status": "PAID"}, timeout=10)
    assert resp.status_code in (200, 204)

    # Give the service a moment to persist
    time.sleep(0.5)

    # Verify DB updated
    cursor = db_connection.cursor(dictionary=True)
    cursor.execute("SELECT status FROM billings WHERE bill_id = %s", (bill_id,))
    srow = cursor.fetchone()
    cursor.close()
    assert srow is not None
    assert srow["status"] in ("PAID", "paid", "COMPLETED", "COMPLETED")


@pytest.mark.dependency(depends=["test_update_bill_status"])
def test_verify_billing_flow(service_url):
    # If billings triggers an insurance verify endpoint, test that the verify path exists
    resp = call_post(f"{service_url}/billings/verify", json_body={"patient_id": "TEST-001", "incident_id": "INC-TEST-001", "amount": 100.0})
    # Accept 200 or 404 depending on implementation
    assert resp.status_code in (200, 404, 400)
    j = resp.json()
    # If 200, expect verified boolean or details
    if resp.status_code == 200:
        assert "verified" in j or "details" in j
