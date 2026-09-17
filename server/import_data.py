"""Validate and atomically merge the initial JSON dataset."""

import argparse
import json
import logging
from pathlib import Path

from sqlalchemy import select

from server.config import Settings
from server.database import Database, Meter, Reading
from server.validation import validate_reading, validate_serial, validate_timestamp


class SeedError(ValueError):
    pass


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not -(2**63) <= value < 2**63:
        raise SeedError(f"{label} must be a signed 64-bit integer")
    return value


def _object(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or not fields <= value.keys():
        raise SeedError(f"{label} must be an object with fields: {', '.join(sorted(fields))}")
    return value


def load_seed(path: Path) -> tuple[list[dict], list[dict]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        _object(data, {"meters", "readings"}, "JSON root")
        if not isinstance(data["meters"], list) or not isinstance(data["readings"], list):
            raise SeedError("meters and readings must be arrays")
        meters, readings = [], []
        meter_ids, serials, reading_ids, reading_keys = set(), set(), set(), set()
        for raw in data["meters"]:
            _object(raw, {"id", "serial_number", "location"}, "Meter")
            meter_id = _integer(raw["id"], "Meter id")
            serial_number = validate_serial(raw["serial_number"])
            if not isinstance(raw["location"], str):
                raise SeedError("Meter location must be a string")
            if meter_id in meter_ids or serial_number in serials:
                raise SeedError("Duplicate meter id or serial_number in JSON")
            meter_ids.add(meter_id)
            serials.add(serial_number)
            meters.append({"id": meter_id, "serial_number": serial_number, "location": raw["location"]})
        for raw in data["readings"]:
            _object(raw, {"id", "meter_id", "recorded_at", "reading_kwh"}, "Reading")
            reading_id = _integer(raw["id"], "Reading id")
            meter_id = _integer(raw["meter_id"], "Reading meter_id")
            recorded_at = validate_timestamp(raw["recorded_at"])
            reading_kwh = validate_reading(raw["reading_kwh"])
            if meter_id not in meter_ids:
                raise SeedError("Reading references a meter absent from JSON")
            if reading_id in reading_ids or (meter_id, recorded_at) in reading_keys:
                raise SeedError("Duplicate reading id or meter/time pair in JSON")
            reading_ids.add(reading_id)
            reading_keys.add((meter_id, recorded_at))
            readings.append({
                "id": reading_id,
                "meter_id": meter_id,
                "recorded_at": recorded_at,
                "reading_kwh": reading_kwh,
            })
        return meters, readings
    except (OSError, UnicodeError, ValueError) as error:
        raise SeedError(f"Cannot import {path}: {error}") from error


def _equal(entity: Meter | Reading, values: dict) -> bool:
    return all(getattr(entity, name) == value for name, value in values.items())


def import_seed(database: Database, path: Path) -> dict[str, int]:
    meters, readings = load_seed(path)
    added = {"meters": 0, "readings": 0}
    with database.write() as session:
        for values in meters:
            existing = session.get(Meter, values["id"])
            if existing is not None:
                if not _equal(existing, values):
                    raise SeedError(f"Meter id {values['id']} conflicts with stored data")
                continue
            if session.scalar(select(Meter.id).where(Meter.serial_number == values["serial_number"])) is not None:
                raise SeedError(f"Meter serial_number {values['serial_number']} has a different id")
            session.add(Meter(**values))
            added["meters"] += 1
        session.flush()

        for values in readings:
            existing = session.get(Reading, values["id"])
            if existing is not None:
                if not _equal(existing, values):
                    raise SeedError(f"Reading id {values['id']} conflicts with stored data")
                continue
            existing_key = session.scalar(select(Reading.id).where(
                Reading.meter_id == values["meter_id"],
                Reading.recorded_at == values["recorded_at"],
            ))
            if existing_key is not None:
                raise SeedError("Reading meter/time pair is already stored with a different id")
            session.add(Reading(**values))
            added["readings"] += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="JSON file (defaults to SEED_PATH)")
    parser.add_argument("--database", type=Path, help="SQLite file (defaults to DATABASE_PATH)")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    database = Database(arguments.database or settings.database_path)
    try:
        database.initialize()
        added = import_seed(database, arguments.file or settings.seed_path)
        print(json.dumps(added))
    except Exception:
        logging.exception("Import failed; no seed changes were committed")
        raise SystemExit(1) from None
    finally:
        database.close()


if __name__ == "__main__":
    main()
