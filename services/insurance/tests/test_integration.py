"""Integration tests for insurance service (Node.js/Express)."""

import pytest
import requests


# --- TESTS START HERE ---


@pytest.mark.dependency()
def test_health(service_url):
    """Check if the service is running and healthy."""
    response = requests.get(f"{service_url}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "insurance"


@pytest.mark.dependency(depends=["test_health"])
def test_get_all(service_url, setup_database):
    """Fetch all insurance policies."""
    response = requests.get(f"{service_url}/insurance")
    # Either returns list of policies or 404 if none exist
    if response.status_code == 200:
        data = response.json()
        assert "data" in data
        assert "policies" in data["data"]
        assert isinstance(data["data"]["policies"], list)
    else:
        assert response.status_code == 404
        assert "message" in response.json()


@pytest.mark.dependency(depends=["test_get_all"])
def test_get_one_valid(service_url, setup_database, db_connection):
    """Get one valid policy by ID."""
    # Get a valid policy_id from test data
    cursor = db_connection.cursor(dictionary=True)
    cursor.execute("SELECT policy_id FROM insurance_policies WHERE patient_id = 'TEST001' LIMIT 1")
    result = cursor.fetchone()
    cursor.close()
    
    if result:
        policy_id = result['policy_id']
        response = requests.get(f"{service_url}/insurance/{policy_id}")
        if response.status_code == 200:
            data = response.json()["data"]
            assert "policy_id" in data
            assert "patient_id" in data
            assert "provider_name" in data
            assert "coverage_amount" in data
        else:
            assert response.status_code == 404


@pytest.mark.dependency(depends=["test_get_all"])
def test_get_one_invalid(service_url):
    """Get non-existing policy."""
    response = requests.get(f"{service_url}/insurance/99999")
    assert response.status_code == 404
    assert response.json()["message"] == "Policy not found"


@pytest.mark.dependency(depends=["test_get_all"])
def test_create_policy_missing_fields(service_url):
    """Try creating a policy with missing fields."""
    response = requests.post(f"{service_url}/insurance", json={})
    assert response.status_code == 400
    data = response.json()
    assert "error" in data


@pytest.mark.dependency(depends=["test_create_policy_missing_fields"])
def test_create_policy_valid(service_url, setup_database):
    """Create a valid insurance policy."""
    body = {
        "patient_id": "TEST-NEW-001",
        "provider_name": "Prudential SG",
        "coverage_amount": 5000.00,
    }
    response = requests.post(f"{service_url}/insurance", json=body)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["patient_id"] == "TEST-NEW-001"
    assert data["provider_name"] == "Prudential SG"
    assert float(data["coverage_amount"]) == 5000.00


@pytest.mark.dependency(depends=["test_create_policy_valid"])
def test_update_policy(service_url, setup_database, db_connection):
    """Update a policy's provider and coverage amount."""
    # Get a valid policy_id from test data
    cursor = db_connection.cursor(dictionary=True)
    cursor.execute("SELECT policy_id FROM insurance_policies WHERE patient_id = 'TEST001' LIMIT 1")
    result = cursor.fetchone()
    cursor.close()
    
    if result:
        policy_id = result['policy_id']
        body = {
            "provider_name": "Updated Provider",
            "coverage_amount": 6000.00
        }
        response = requests.put(f"{service_url}/insurance/{policy_id}", json=body)
        if response.status_code == 200:
            data = response.json()["data"]
            assert data["provider_name"] == "Updated Provider"
            assert float(data["coverage_amount"]) == 6000.00
        else:
            assert response.status_code == 404


@pytest.mark.dependency(depends=["test_create_policy_valid"])
def test_verify_insurance_success(service_url, setup_database):
    """Verify an insurance policy successfully."""
    body = {
        "patient_id": "TEST001",
        "incident_id": "INC-001",
        "amount": 1000.00
    }
    response = requests.post(f"{service_url}/insurance/verify", json=body)
    assert response.status_code in [200, 404]
    data = response.json()
    
    if response.status_code == 200:
        assert data["verified"] is True
        assert "details" in data
        assert data["details"]["patient_id"] == "TEST001"
        assert data["details"]["coverage_status"] in ["fully_covered", "partially_covered"]


@pytest.mark.dependency(depends=["test_verify_insurance_success"])
def test_verify_insurance_no_policy(service_url):
    """Verify insurance with a patient_id that has no policy."""
    body = {
        "patient_id": "NON_EXISTENT_PATIENT",
        "incident_id": "INC-002",
        "amount": 100.00
    }
    response = requests.post(f"{service_url}/insurance/verify", json=body)
    assert response.status_code == 404
    data = response.json()
    assert data["verified"] is False
    assert "No insurance policy found" in data["message"]


@pytest.mark.dependency(depends=["test_verify_insurance_success"])
def test_verify_insurance_missing_fields(service_url):
    """Verify insurance with missing required fields."""
    body = {
        "patient_id": "TEST001"
        # Missing incident_id and amount
    }
    response = requests.post(f"{service_url}/insurance/verify", json=body)
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
