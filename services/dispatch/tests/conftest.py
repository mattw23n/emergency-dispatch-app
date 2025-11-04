"""Pytest configuration and fixtures for dispatch service tests."""
import pytest

from src.app import create_app


@pytest.fixture
def app():
    """Create a Flask app for testing with in-memory SQLite database."""
    test_app = create_app(db_uri="sqlite:///:memory:")
    yield test_app


@pytest.fixture
def client(app):
    """Create a test client for the Flask app."""
    return app.test_client()


@pytest.fixture
def sample_hospitals(app):
    """Seed the test database with sample hospitals."""
    with app.app_context():
        # Data is already seeded in create_app
        yield
