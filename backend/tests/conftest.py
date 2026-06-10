import os
import sys
from pathlib import Path

os.environ["CAPOS_DATABASE_URL"] = "sqlite:///./test_capabilityos.db"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    for f in ("test_capabilityos.db",):
        if os.path.exists(f):
            os.remove(f)
    from app.main import app

    with TestClient(app) as c:
        yield c
    if os.path.exists("test_capabilityos.db"):
        os.remove("test_capabilityos.db")
