import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from main import app


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: needs a real local Docker daemon")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
