import json

import pytest

from server.config import Settings
from server.database import Database
from server.import_data import import_seed


@pytest.fixture
def seed_data():
    return {
        "meters": [
            {"id": 1, "serial_number": "00000001", "location": "Office"},
            {"id": 2, "serial_number": "00000002", "location": "Warehouse"},
        ],
        "readings": [
            {
                "id": 10,
                "meter_id": 1,
                "recorded_at": "2026-09-11T10:00:00Z",
                "reading_kwh": 9.5,
            }
        ],
    }


@pytest.fixture
def seed_path(tmp_path, seed_data):
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed_data), encoding="utf-8")
    return path


@pytest.fixture
def database(tmp_path, seed_path):
    db = Database(tmp_path / "metering.sqlite3")
    db.initialize()
    import_seed(db, seed_path)
    yield db
    db.close()


@pytest.fixture
def settings(tmp_path, seed_path):
    return Settings(
        database_path=tmp_path / "service.sqlite3",
        seed_path=seed_path,
        http_host="127.0.0.1",
        http_port=8000,
        grpc_host="127.0.0.1",
        grpc_port=0,
        app_version="test-version",
    )
