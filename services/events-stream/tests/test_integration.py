import json
import time


def test_health_endpoint(service_url, requests_session):
    """Health endpoint should respond and include expected keys."""
    resp = requests_session.get(f"{service_url}/health", timeout=5)
    assert resp.status_code in (200, 503)
    j = resp.json()
    assert isinstance(j, dict)
    assert "status" in j
    assert "rabbitmq_connected" in j


def test_sse_initial_connection(service_url, requests_session):
    """Connect to the SSE /events endpoint and verify the initial connection event is received.

    The event-stream service immediately yields a connection message with type 'connection'.
    """
    url = f"{service_url}/events"

    # Use a short-lived streaming request to capture the initial SSE event
    with requests_session.get(url, stream=True, timeout=15) as resp:
        assert resp.status_code == 200

        # Iterate the streaming response lines until we find the first 'data:' line
        lines = resp.iter_lines(decode_unicode=True)
        start_time = time.time()
        data_line = None

        for line in lines:
            if line is None:
                continue
            # Skip empty lines and comment heartbeats
            text = line.strip()
            if not text:
                continue
            if text.startswith(":"):
                # SSE comment/heartbeat
                continue
            if text.startswith("data:"):
                data_line = text[len("data:"):].strip()
                break
            # Keep a safety timeout to avoid hanging forever
            if time.time() - start_time > 8:
                break

        assert data_line is not None, "Did not receive initial SSE 'data' line"

        payload = json.loads(data_line)
        assert isinstance(payload, dict)
        # The service sends a connection message as the first event
        assert payload.get("type") == "connection"
