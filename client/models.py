"""Table models and validation of the public HTTP response contracts."""

import math
import re
from datetime import datetime, timezone

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_meters(payload: object) -> list[dict]:
    if not isinstance(payload, list):
        raise ValueError("Ожидался список счётчиков.")
    identifiers, serials = set(), set()
    for row in payload:
        if (
            not isinstance(row, dict)
            or type(row.get("id")) is not int
            or not isinstance(row.get("serial_number"), str)
            or not row["serial_number"]
            or not isinstance(row.get("location"), str)
        ):
            raise ValueError("Некорректные данные счётчика.")
        if row["id"] in identifiers or row["serial_number"] in serials:
            raise ValueError("Сервер вернул повторяющиеся счётчики.")
        identifiers.add(row["id"])
        serials.add(row["serial_number"])
    return payload


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ValueError("Некорректная дата показания; ожидалось время UTC.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("Некорректная календарная дата показания.") from exc


def parse_readings(payload: object, meter_id: int) -> list[dict]:
    if not isinstance(payload, list):
        raise ValueError("Ожидался список показаний.")
    identifiers, timestamps = set(), set()
    for row in payload:
        if (
            not isinstance(row, dict)
            or type(row.get("id")) is not int
            or type(row.get("meter_id")) is not int
            or row["meter_id"] != meter_id
            or type(row.get("reading_kwh")) not in (int, float)
        ):
            raise ValueError("Некорректные данные показания.")
        try:
            valid_number = math.isfinite(row["reading_kwh"]) and row["reading_kwh"] >= 0
        except OverflowError:
            valid_number = False
        if not valid_number:
            raise ValueError("Показание должно быть конечным неотрицательным числом.")
        parse_timestamp(row.get("recorded_at"))
        if row["id"] in identifiers or row["recorded_at"] in timestamps:
            raise ValueError("Сервер вернул повторяющиеся показания.")
        identifiers.add(row["id"])
        timestamps.add(row["recorded_at"])
    return payload


class TableModel(QAbstractTableModel):
    """Display text is independent of the values used to sort each column."""

    def __init__(self, columns: tuple[tuple[str, str, str], ...], parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []

    def replace_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        key, _, kind = self.columns[index.column()]
        value = self.rows[index.row()][key]
        if role == Qt.ItemDataRole.UserRole:
            if kind == "date":
                return (parse_timestamp(value) - _EPOCH).total_seconds()
            if kind == "number":
                return float(value)
            return value
        if role == Qt.ItemDataRole.DisplayRole:
            if kind == "date":
                return value.replace("T", " ").removesuffix("Z") + " UTC"
            return str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole and kind == "number":
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][1]
        return super().headerData(section, orientation, role)


class MeterTableModel(TableModel):
    def __init__(self, parent=None):
        super().__init__(
            (("serial_number", "Серийный номер", "text"), ("location", "Место установки", "text")),
            parent,
        )


class ReadingTableModel(TableModel):
    def __init__(self, parent=None):
        super().__init__(
            (
                ("recorded_at", "Дата и время", "date"),
                ("reading_kwh", "Показание, кВт·ч", "number"),
            ),
            parent,
        )
