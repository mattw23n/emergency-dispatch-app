"""
Pytest fixtures for the Event Stream service integration tests.

Fixtures provided:
- service_url: base URL for the service (ENV: EVENTS_STREAM_URL, default http://localhost:8090)
- requests_session: a requests.Session instance used by tests
- wait_for_service: autouse fixture that waits for /health to respond before tests run

These tests assume the event-stream service is running (or started by the test runner) and reachable
at the configured URL.
"""
import os
import time

import pytest
import requests


DEFAULT_URL = os.environ.get("EVENTS_STREAM_URL", "http://localhost:8090")


@pytest.fixture(scope="session")
def service_url():
    """Return the base URL for the event-stream service."""
    return DEFAULT_URL


@pytest.fixture(scope="session")
def requests_session():
    """Provide a requests Session for tests and ensure it's cleaned up afterwards."""
    s = requests.Session()
    yield s
    s.close()


@pytest.fixture(scope="session", autouse=True)
def wait_for_service(service_url, requests_session):
    """Wait for the service /health endpoint to respond.

    If the service does not respond within the timeout, the tests will be skipped.
    """
    timeout = int(os.environ.get("EVENTS_STREAM_WAIT", "20"))
    deadline = time.time() + timeout
    last_exc = None

    while time.time() < deadline:
        try:
            resp = requests_session.get(f"{service_url}/health", timeout=5)
            # If we get any HTTP response the service is up enough for tests (200 or 503 are both valid states)
            if resp.status_code in (200, 503):
                return
        except Exception as exc:
            last_exc = exc
        time.sleep(1)

    pytest.skip(f"Event-stream service not reachable at {service_url}/health after {timeout}s. Last error: {last_exc}")
