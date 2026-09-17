"""Validation at the JSON and protobuf boundaries."""

import math
import re
from datetime import datetime

TIMESTAMP_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def validate_timestamp(value: object) -> str:
    if not isinstance(value, str) or TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("recorded_at must use YYYY-MM-DDTHH:mm:ssZ UTC format")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError("recorded_at must be a valid calendar date and time") from error
    return value


def validate_reading(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("reading_kwh is required and must be a number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError("reading_kwh must be a finite nonnegative number") from error
    if not math.isfinite(result) or result < 0:
        raise ValueError("reading_kwh must be a finite nonnegative number")
    return result


def validate_serial(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("serial_number must be a nonempty string")
    return value
