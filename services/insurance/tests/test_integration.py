"""Integration tests for insurance service."""

import json

import pytest


def call(client, path, method="GET", body=None):
    """Make HTTP requests using the test client."""
    mimetype = "application/json"
    headers = {"Content-Type": mimetype, "Accept": mimetype}

    if method == "POST":
        response = client.post(
            path, data=json.dumps(body or {}), headers=headers
        )
    elif method == "PATCH":
        response = client.patch(
            path, data=json.dumps(body or {}), headers=headers
        )
    elif method == "DELETE":
        response = client.delete(path, headers=headers)
    else:
        response = client.get(path, headers=headers)

    return {
        "json": json.loads(response.data.decode("utf-8")),
        "code": response.status_code,
    }


# --- TESTS START HERE ---


@pytest.mark.dependency()
def test_health(client):
    """Check if the service is running and healthy."""
    result = call(client, "health")
    assert result["code"] == 200
    assert result["json"]["service"] == "insurance"


@pytest.mark.dependency(depends=["test_health"])
def test_get_all(client):
    """Fetch all insurance policies."""
    result = call(client, "insurance")
    # Either returns list of policies or 404 if none exist
    if result["code"] == 200:
        assert "policies" in result["json"]["data"]
        assert isinstance(result["json"]["data"]["policies"], list)
    else:
        assert result["code"] == 404
        assert "message" in result["json"]


@pytest.mark.dependency(depends=["test_get_all"])
def test_get_one_valid(client):
    """Get one valid policy by ID."""
    result = call(client, "insurance/1")
    if result["code"] == 200:
        data = result["json"]["data"]
        assert "policy_id" in data
        assert "patient_id" in data
        assert "provider_name" in data
        assert "coverage_amount" in data
        assert "policy_status" in data
    else:
        # acceptable if no record 1 exists
        assert result["code"] == 404


@pytest.mark.dependency(depends=["test_get_all"])
def test_get_one_invalid(client):
    """Get non-existing policy."""
    result = call(client, "insurance/99999")
    assert result["code"] == 404
    assert result["json"] == {"message": "Policy not found."}


@pytest.mark.dependency(depends=["test_get_all"])
def test_create_policy_missing_fields(client):
    """Try creating a policy with missing fields."""
    result = call(client, "insurance", "POST", {})
    assert result["code"] == 400
    assert "message" in result["json"]


@pytest.mark.dependency(depends=["test_create_policy_missing_fields"])
def test_create_policy_valid(client):
    """Create a valid insurance policy."""
    body = {
        "patient_id": "PAT-001",
        "provider_name": "Prudential SG",
        "coverage_amount": 5000.00,
    }
    result = call(client, "insurance", "POST", body)
    assert result["code"] == 201
    data = result["json"]["data"]
    assert data["patient_id"] == "PAT-001"
    assert data["provider_name"] == "Prudential SG"
    assert data["policy_status"] == "ACTIVE"


@pytest.mark.dependency(depends=["test_create_policy_valid"])
def test_update_policy_status(client):
    """Update a policy's status to INACTIVE."""
    result = call(
        client, "insurance/1", "PATCH", {"policy_status": "INACTIVE"}
    )
    if result["code"] == 200:
        assert result["json"]["data"]["policy_status"] == "INACTIVE"
    else:
        # acceptable if policy_id=1 doesn't exist
        assert result["code"] in [404]


@pytest.mark.dependency(depends=["test_create_policy_valid"])
def test_verify_insurance_success(client):
    """Verify an insurance policy successfully."""
    # Assuming patient_id=PAT-001 from test_create_policy_valid
    body = {"patient_id": "PAT-001", "amount": 1000.00}
    result = call(client, "insurance/verify", "POST", body)
    assert result["code"] in [200, 400, 404]
    if result["code"] == 200:
        assert result["json"]["verified"] is True
    elif result["code"] == 400:
        assert result["json"]["verified"] is False


@pytest.mark.dependency(depends=["test_verify_insurance_success"])
def test_verify_insurance_no_policy(client):
    """Verify insurance with a patient_id that has no policy."""
    body = {"patient_id": "NON_EXISTENT", "amount": 100.00}
    result = call(client, "insurance/verify", "POST", body)
    assert result["code"] == 404
    assert result["json"]["verified"] is False
    assert result["json"]["message"] == "No active insurance policy found."
