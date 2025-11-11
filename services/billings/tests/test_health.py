"""Health check tests for the billings service.

This module contains tests that verify the health check endpoint and its responses
under various conditions, including database connectivity issues.
"""

import app as billings_app


# Minimal dummies so /health runs without a real DB
class _DummyCursor:
    def execute(self, *_a, **_k):
        pass

    def fetchone(self):
        return (1,)

    def close(self):
        pass


class _DummyConn:
    def __init__(self):
        self._open = True

    def cursor(self, *_, **__):
        return _DummyCursor()

    def is_connected(self):
        return self._open

    def close(self):
        self._open = False


def test_health_ok(monkeypatch):
    """Test the health check endpoint when all services are healthy.

    Verifies that the health check returns a 200 status code and indicates
    that both the service and database are healthy.

    Args:
        monkeypatch: Pytest fixture for patching modules and functions.
    """
    import mysql
    import mysql.connector

    monkeypatch.setattr(mysql.connector, "connect", lambda **kw: _DummyConn())

    client = billings_app.app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["service"] == "billings"


def test_health_db_failure(monkeypatch):
    """Test the health check endpoint when database connection fails.

    Verifies that the health check properly reports database connectivity issues
    by returning a 503 status code and indicating the database is not connected.

    Args:
        monkeypatch: Pytest fixture for patching modules and functions.
    """
    import mysql
    import mysql.connector

    class _Boom(mysql.connector.Error):
        pass

    monkeypatch.setattr(
        mysql.connector, "connect", lambda **kw: (_ for _ in ()).throw(_Boom("no db"))
    )

    client = billings_app.app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 500
    data = resp.get_json()
    assert data["status"] == "unhealthy"
    assert data["database"] == "connection failed"
    assert "no db" in data["error"]
