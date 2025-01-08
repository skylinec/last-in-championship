import os
import tempfile
import pytest
from app.main import app

@pytest.fixture
def client():
    db_fd, app.config['DATABASE'] = tempfile.mkstemp()
    app.config['TESTING'] = True

    with app.test_client() as client:
        yield client

    os.close(db_fd)
    os.unlink(app.config['DATABASE'])

@pytest.fixture
def auth_client(client):
    with client.session_transaction() as session:
        session['user'] = 'test_user'
    return client

@pytest.fixture
def sample_data():
    return [
        {
            "id": "1",
            "date": "2024-01-08",
            "name": "Test User",
            "status": "in-office",
            "time": "08:30",
            "timestamp": "2024-01-08T08:30:00"
        }
    ]
