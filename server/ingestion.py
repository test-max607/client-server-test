"""Persist an individual reading, including concurrent retry handling."""

from sqlalchemy import select

from server.database import Database, Meter, Reading
from server.validation import validate_reading, validate_serial, validate_timestamp


class InvalidReading(ValueError):
    pass


class UnknownMeter(LookupError):
    pass


class ReadingConflict(ValueError):
    pass


def submit_reading(
    database: Database,
    serial_number: str,
    recorded_at: str,
    reading_kwh: float | None,
) -> tuple[int, int]:
    try:
        serial_number = validate_serial(serial_number)
        recorded_at = validate_timestamp(recorded_at)
        reading_kwh = validate_reading(reading_kwh)
    except ValueError as error:
        raise InvalidReading(str(error)) from error

    with database.write() as session:
        meter = session.scalar(select(Meter).where(Meter.serial_number == serial_number))
        if meter is None:
            raise UnknownMeter("No meter with this serial number")
        reading = session.scalar(
            select(Reading).where(
                Reading.meter_id == meter.id,
                Reading.recorded_at == recorded_at,
            )
        )
        if reading is not None:
            if reading.reading_kwh != reading_kwh:
                raise ReadingConflict("A different reading already exists for this meter and time")
        else:
            reading = Reading(
                meter_id=meter.id,
                recorded_at=recorded_at,
                reading_kwh=reading_kwh,
            )
            session.add(reading)
            session.flush()
        result = (reading.id, meter.id)

    # The context manager has committed before a caller can acknowledge success.
    return result
