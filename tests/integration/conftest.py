import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def make_client(test_settings, mock_transport):
    def factory(claude=None, settings=None):
        app = create_app(settings or test_settings, claude_client=claude, transport=mock_transport)
        return TestClient(app)

    return factory
