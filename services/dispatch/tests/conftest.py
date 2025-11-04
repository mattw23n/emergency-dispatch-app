"""Pytest configuration and fixtures for dispatch service tests."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add the parent directory to the path so we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app import create_app, db, Hospital


@pytest.fixture
def app():
    """Create a Flask app for testing without database dependency."""
    # Use a mock database URI that won't create a file
    # Mock all database operations to avoid actual database connections
    with patch('src.app.DB_URL', None), \
         patch('src.app.db.create_all'), \
         patch('src.app.db.session') as mock_session, \
         patch('src.app.create_engine') as mock_engine:
        
        # Configure mock session
        mock_session.execute = Mock()
        mock_session.commit = Mock()
        mock_session.add_all = Mock()
        
        test_app = create_app(db_uri="sqlite:///:memory:")
        
        yield test_app


@pytest.fixture
def client(app):
    """Create a test client for the Flask app."""
    return app.test_client()


@pytest.fixture
def sample_hospitals(app):
    """Provide mock sample hospitals for testing."""
    with app.app_context():
        # Return mock hospital data
        mock_hospitals = [
            Hospital(id="hosp-1", name="Central Hospital", lat=51.5074, lng=-0.1278, capacity=5),
            Hospital(id="hosp-2", name="Westside Medical", lat=51.5155, lng=-0.1420, capacity=2),
            Hospital(id="hosp-3", name="Riverside Clinic", lat=51.5033, lng=-0.1196, capacity=10),
        ]
        
        # Mock the database query to return these hospitals
        with patch('src.app.db.session') as mock_session:
            mock_result = Mock()
            mock_result.scalars.return_value.all.return_value = mock_hospitals
            mock_session.execute.return_value = mock_result
            yield mock_hospitals
