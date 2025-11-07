# tests/test_integration.py
import pytest
from fastapi.testclient import TestClient
from src.app import app


# Create a reusable test client for FastAPI
@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def call(client, path, method="GET", body=None):
    """Helper function to call API endpoints with JSON body and headers"""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if method == "POST":
        response = client.post(path, json=body, headers=headers)
    else:
        response = client.get(path, headers=headers)
    return {"json": response.json(), "code": response.status_code}


@pytest.mark.dependency()
def test_health(client):
    """Test that /health endpoint returns 200 OK"""
    result = call(client, "/health")
    assert result["code"] == 200
    assert result["json"] == {"status": "ok"}


# @pytest.mark.dependency(depends=["test_health"])
# def test_publish_notification(client):
#     """Integration test: verify /notify endpoint publishes notification successfully"""
#     body = {
#         "patient_id": "P001",
#         "subject": "Integration Test",
#         "message": "Hello from integration test"
#     }
#     result = call(client, '/notify', method='POST', body=body)
#     assert result['code'] == 200
#     assert "status" in result['json']
