from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from server.database import Reading
from server.ingestion import InvalidReading, ReadingConflict, UnknownMeter, submit_reading

TIME = "2026-09-18T10:00:00Z"


def test_zero_and_duplicate_imported_record(database):
    assert submit_reading(database, "00000001", "2026-09-11T10:00:00Z", 9.5) == (10, 1)
    reading_id, meter_id = submit_reading(database, "00000002", TIME, 0.0)
    assert reading_id > 10
    assert meter_id == 2
    with database.sessions() as session:
        assert session.get(Reading, reading_id).reading_kwh == 0.0


def test_conflicting_duplicate_keeps_original(database):
    with pytest.raises(ReadingConflict):
        submit_reading(database, "00000001", "2026-09-11T10:00:00Z", 10)
    with database.sessions() as session:
        assert session.get(Reading, 10).reading_kwh == 9.5
        assert session.scalar(select(func.count()).select_from(Reading)) == 1


@pytest.mark.parametrize("serial", ["1", "00000003", "00000001 "])
def test_serial_matching_is_exact(database, serial):
    with pytest.raises(UnknownMeter):
        submit_reading(database, serial, TIME, 1)


@pytest.mark.parametrize(
    "recorded_at",
    [
        "",
        "2026-02-30T10:00:00Z",
        "2026-09-18T25:00:00Z",
        "2026-09-18T10:00:60Z",
        "2026-09-18T10:00:00+00:00",
        "2026-09-18T10:00:00.000Z",
        "2026-9-18T10:00:00Z",
        "2026-09-18 10:00:00Z",
        "2026-09-18T10:00:00z",
        "2026-09-18T10:00:00Z\n",
    ],
)
def test_invalid_timestamp(database, recorded_at):
    with pytest.raises(InvalidReading):
        submit_reading(database, "00000001", recorded_at, 1)


@pytest.mark.parametrize("value", [None, -0.01, float("inf"), float("-inf"), float("nan")])
def test_invalid_reading(database, value):
    with pytest.raises(InvalidReading):
        submit_reading(database, "00000001", TIME, value)


def test_concurrent_identical_readings_are_idempotent(database):
    barrier = Barrier(8)

    def submit(_):
        barrier.wait(timeout=10)
        return submit_reading(database, "00000001", TIME, 10.25)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert len(set(results)) == 1
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Reading)) == 2


def test_concurrent_conflicting_readings_have_one_winner(database):
    barrier = Barrier(8)

    def submit(value):
        barrier.wait(timeout=10)
        try:
            return submit_reading(database, "00000001", TIME, value), value
        except ReadingConflict:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    (reading_id, meter_id), value = winners[0]
    assert meter_id == 1
    with database.sessions() as session:
        assert session.get(Reading, reading_id).reading_kwh == value
        assert session.scalar(select(func.count()).select_from(Reading)) == 2


def test_commit_failure_does_not_report_success_or_persist(database):
    def fail_commit(connection):
        raise RuntimeError("simulated commit failure")

    event.listen(database.engine, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            submit_reading(database, "00000001", TIME, 10)
    finally:
        event.remove(database.engine, "commit", fail_commit)
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Reading)) == 1


def test_database_rejects_foreign_key_and_duplicate_key(database):
    with database.sessions() as session:
        session.add(Reading(meter_id=999, recorded_at=TIME, reading_kwh=1))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(Reading(meter_id=1, recorded_at="2026-09-11T10:00:00Z", reading_kwh=1))
        with pytest.raises(IntegrityError):
            session.commit()
