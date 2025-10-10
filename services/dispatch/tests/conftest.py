import pytest

from src.app import create_app, db, Hospital


@pytest.fixture
def app():
    # Use in-memory SQLite for tests
    app = create_app(db_uri="sqlite:///:memory:")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sample_hospitals(app):
    """Seed the test database with sample hospitals."""
    with app.app_context():
        # Data is already seeded in create_app, but we can add more if needed
        yield