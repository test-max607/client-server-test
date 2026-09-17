import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from server.database import Database, Meter, Reading
from server.import_data import SeedError, import_seed
from server.ingestion import submit_reading


def counts(database):
    with database.sessions() as session:
        return (
            session.scalar(select(func.count()).select_from(Meter)),
            session.scalar(select(func.count()).select_from(Reading)),
        )


def write_seed(path, data):
    path.write_text(json.dumps(data, allow_nan=True), encoding="utf-8")


def test_provided_seed_counts_and_repeated_import(tmp_path):
    database = Database(tmp_path / "full.sqlite3")
    database.initialize()
    try:
        seed_path = Path(__file__).resolve().parents[1] / "data.json"
        assert import_seed(database, seed_path) == {"meters": 100, "readings": 500}
        assert counts(database) == (100, 500)
        assert import_seed(database, seed_path) == {"meters": 0, "readings": 0}
        assert counts(database) == (100, 500)
        with database.sessions() as session:
            serial = session.scalar(select(Meter.serial_number).where(Meter.id == 1))
        new_id, meter_id = submit_reading(database, serial, "2026-09-18T12:00:00Z", 200.0)
        assert new_id > 500
        assert meter_id == 1
        import_seed(database, seed_path)
        assert counts(database) == (100, 501)
    finally:
        database.close()


def test_reimport_and_restart_preserve_ingested_readings(database, seed_path):
    new_id, _ = submit_reading(database, "00000001", "2026-09-18T10:00:00Z", 10.0)
    assert new_id > 10
    assert import_seed(database, seed_path) == {"meters": 0, "readings": 0}
    db_path = Path(database.engine.url.database)
    database.close()
    reopened = Database(db_path)
    reopened.initialize()
    try:
        assert import_seed(reopened, seed_path) == {"meters": 0, "readings": 0}
        assert counts(reopened) == (2, 2)
        assert submit_reading(reopened, "00000001", "2026-09-18T10:00:00Z", 10.0) == (
            new_id,
            1,
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("conflict", ["meter_id", "serial_number", "reading_id", "reading_key"])
def test_conflict_rolls_back_all_new_seed_rows(database, seed_path, seed_data, conflict):
    data = copy.deepcopy(seed_data)
    data["meters"].insert(0, {"id": 3, "serial_number": "00000003", "location": "New"})
    data["readings"].insert(
        0,
        {"id": 11, "meter_id": 3, "recorded_at": "2026-09-11T11:00:00Z", "reading_kwh": 1},
    )
    if conflict == "meter_id":
        data["meters"][1]["location"] = "Changed location"
    elif conflict == "serial_number":
        data["meters"][1]["id"] = 4
        data["readings"][1]["meter_id"] = 4
    elif conflict == "reading_id":
        data["readings"][1]["recorded_at"] = "2026-09-11T12:00:00Z"
    else:
        data["readings"][1]["id"] = 12
        data["readings"][1]["reading_kwh"] = 99.0
    write_seed(seed_path, data)
    with pytest.raises(SeedError):
        import_seed(database, seed_path)
    assert counts(database) == (2, 1)
    with database.sessions() as session:
        assert session.get(Meter, 3) is None
        assert session.get(Reading, 10).reading_kwh == 9.5


@pytest.mark.parametrize(
    ("collection", "field", "value"),
    [
        ("meters", "id", None),
        ("meters", "id", True),
        ("meters", "serial_number", None),
        ("meters", "serial_number", 1),
        ("meters", "location", None),
        ("readings", "id", None),
        ("readings", "meter_id", 999),
        ("readings", "recorded_at", "2026-02-30T00:00:00Z"),
        ("readings", "recorded_at", "2026-09-11T10:00:00+00:00"),
        ("readings", "reading_kwh", None),
        ("readings", "reading_kwh", -1),
        ("readings", "reading_kwh", float("nan")),
        ("readings", "reading_kwh", float("inf")),
    ],
)
def test_invalid_seed_is_rejected_without_writes(
    database, seed_path, seed_data, collection, field, value
):
    seed_data[collection][0][field] = value
    write_seed(seed_path, seed_data)
    with pytest.raises(SeedError):
        import_seed(database, seed_path)
    assert counts(database) == (2, 1)


@pytest.mark.parametrize("collection", ["meters", "readings"])
def test_duplicate_rows_in_seed_are_rejected(database, seed_path, seed_data, collection):
    seed_data[collection].append(copy.deepcopy(seed_data[collection][0]))
    write_seed(seed_path, seed_data)
    with pytest.raises(SeedError):
        import_seed(database, seed_path)
    assert counts(database) == (2, 1)


@pytest.mark.parametrize("contents", ["{", "null", "[]", '{"meters": []}'])
def test_malformed_seed_is_rejected(database, seed_path, contents):
    seed_path.write_text(contents, encoding="utf-8")
    with pytest.raises(SeedError):
        import_seed(database, seed_path)
    assert counts(database) == (2, 1)
