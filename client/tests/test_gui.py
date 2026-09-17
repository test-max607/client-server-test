import threading

import pytest
from PyQt6.QtCore import Qt, QTimer

from client.windows import MainWindow, ReadingsWindow

METERS_PATH = "/api/v1/meters"
READINGS_PATH = "/api/v1/meters/1/readings"


def main_window(qtbot, api, meters, **kwargs):
    api.respond(METERS_PATH, meters)
    window = MainWindow(api.base_url, **kwargs)
    qtbot.addWidget(window)
    window.show()
    if kwargs.get("autoload", True):
        qtbot.waitUntil(lambda: not window.loading)
    return window


def readings_window(qtbot, api, meters, readings, **kwargs):
    api.respond(READINGS_PATH, readings)
    window = ReadingsWindow(api.base_url, meters[0], **kwargs)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.loading)
    return window


def visible_rows(window):
    return [
        window.model.rows[window.proxy.mapToSource(window.proxy.index(row, 0)).row()]
        for row in range(window.proxy.rowCount())
    ]


def selected_id(window):
    index = window.table.currentIndex()
    assert index.isValid()
    return window.model.rows[window.proxy.mapToSource(index).row()]["id"]


def test_initial_sort_and_search_keep_leading_zeros_and_ignore_location(qtbot, api, meters):
    window = main_window(qtbot, api, meters)
    assert [row["serial_number"] for row in visible_rows(window)] == ["00002", "00012", "90000"]
    window.search.setText("0012")
    assert [row["id"] for row in visible_rows(window)] == [1]
    window.search.setText("does-not-exist")
    assert window.proxy.rowCount() == 0
    no_results_status = window.status_label.text()
    assert no_results_status
    window.search.clear()
    assert window.proxy.rowCount() == 3
    assert window.status_label.text() != no_results_status


def test_open_sorted_selection_reuses_window_and_removes_closed_window(qtbot, api, meters):
    api.respond("/api/v1/meters/3/readings", [])
    window = main_window(qtbot, api, meters)
    window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    window.table.selectRow(0)
    assert selected_id(window) == 3
    window.open_selected_meter()
    qtbot.waitUntil(lambda: 3 in window.readings_windows)
    child = window.readings_windows[3]
    qtbot.waitUntil(lambda: not child.loading)
    assert "/api/v1/meters/3/readings" in api.requests
    assert child.model.rows == []
    assert child.status_label.text()
    window.open_selected_meter()
    assert window.readings_windows == {3: child}
    child.close()
    qtbot.waitUntil(lambda: 3 not in window.readings_windows)


def test_refresh_preserves_search_sort_and_selected_meter(qtbot, api, meters):
    window = main_window(qtbot, api, meters)
    window.search.setText("000")
    window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    window.table.selectRow(1)
    meter_id = selected_id(window)
    replacement = list(reversed(meters)) + [
        {"id": 4, "serial_number": "00099", "location": "Новый счётчик"}
    ]
    api.respond(METERS_PATH, replacement)
    window.refresh()
    qtbot.waitUntil(lambda: not window.loading)
    assert window.search.text() == "000"
    assert window.proxy.sortOrder() == Qt.SortOrder.DescendingOrder
    assert [row["serial_number"] for row in visible_rows(window)] == [
        "90000",
        "00099",
        "00012",
        "00002",
    ]
    assert selected_id(window) == meter_id


def test_reading_dates_and_values_sort_by_value(qtbot, api, meters, readings):
    window = readings_window(qtbot, api, meters, list(reversed(readings)))
    assert [row["id"] for row in visible_rows(window)] == [5, 4, 3, 2, 1]
    displayed_date = window.proxy.data(window.proxy.index(0, 0))
    assert displayed_date == "2026-09-15 10:00:00 UTC"
    window.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    assert [row["reading_kwh"] for row in visible_rows(window)] == [9.5, 10.0, 99.9, 100.0, 100.25]
    assert window.proxy.data(window.proxy.index(0, 1), Qt.ItemDataRole.UserRole) == 9.5
    window.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    assert [row["id"] for row in visible_rows(window)] == [1, 2, 3, 4, 5]


def test_readings_manual_refresh_shows_new_record_and_preserves_sort(qtbot, api, meters, readings):
    window = readings_window(qtbot, api, meters, readings)
    window.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    new_reading = {
        "id": 6,
        "meter_id": 1,
        "recorded_at": "2026-09-16T10:00:00Z",
        "reading_kwh": 101.5,
    }
    api.respond(READINGS_PATH, [new_reading, *readings])
    qtbot.mouseClick(window.refresh_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.loading)
    assert window.proxy.sortColumn() == 1
    assert window.proxy.sortOrder() == Qt.SortOrder.AscendingOrder
    assert [row["id"] for row in visible_rows(window)] == [1, 2, 3, 4, 5, 6]
    assert visible_rows(window)[-1] == new_reading


def test_empty_meter_list_has_different_status_from_no_search_matches(qtbot, api, meters):
    window = main_window(qtbot, api, [])
    empty_status = window.status_label.text()
    assert empty_status
    assert window.model.rows == []
    assert window.refresh_button.isEnabled()
    assert not window.open_button.isEnabled()
    api.respond(METERS_PATH, meters)
    window.refresh()
    qtbot.waitUntil(lambda: not window.loading)
    window.search.setText("no-matching-serial")
    assert window.proxy.rowCount() == 0
    assert window.status_label.text()
    assert window.status_label.text() != empty_status


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"detail": "Database unavailable"}, 503),
        (b"this is not JSON", 200),
        ({"meters": []}, 200),
        ([{"id": 4, "serial_number": 1234, "location": "Wrong serial type"}], 200),
    ],
)
def test_bad_response_preserves_previously_loaded_meters(qtbot, api, meters, body, status):
    window = main_window(qtbot, api, meters)
    previous_status = window.status_label.text()
    api.respond(METERS_PATH, body, status=status)
    window.refresh()
    qtbot.waitUntil(lambda: not window.loading)
    assert window.model.rows == meters
    assert window.status_label.text()
    assert window.status_label.text() != previous_status
    assert window.refresh_button.isEnabled()


@pytest.mark.parametrize("invalid_date", ["2026-02-30T00:00:00Z", "2026-09-11T10:00:00+00:00"])
def test_invalid_reading_date_preserves_previous_data(qtbot, api, meters, readings, invalid_date):
    window = readings_window(qtbot, api, meters, readings)
    previous_status = window.status_label.text()
    invalid_reading = {**readings[0], "recorded_at": invalid_date}
    api.respond(READINGS_PATH, [invalid_reading])
    window.refresh()
    qtbot.waitUntil(lambda: not window.loading)
    assert window.model.rows == readings
    assert window.status_label.text() != previous_status


def test_timeout_preserves_data_and_refresh_can_recover(qtbot, api, meters):
    window = main_window(qtbot, api, meters, timeout_ms=400)
    previous_status = window.status_label.text()
    api.respond(METERS_PATH, [], delay=0.8)
    window.refresh()
    qtbot.waitUntil(lambda: not window.loading, timeout=1500)
    assert window.model.rows == meters
    assert window.status_label.text() != previous_status
    api.respond(METERS_PATH, [])
    window.refresh()
    qtbot.waitUntil(lambda: not window.loading)
    assert window.model.rows == []
    assert window.status_label.text()
    assert window.refresh_button.isEnabled()
    # The response to the old, aborted request must not affect the successful refresh.
    recovered_status = window.status_label.text()
    qtbot.wait(850)
    assert window.status_label.text() == recovered_status


def test_slow_response_keeps_event_loop_responsive(qtbot, api, meters):
    window = main_window(qtbot, api, meters, autoload=False)
    release = threading.Event()
    api.respond(METERS_PATH, meters, release=release)
    ticks = []
    window.refresh()
    assert window.loading
    QTimer.singleShot(20, lambda: ticks.append(True))
    qtbot.waitUntil(lambda: bool(ticks), timeout=1500)
    assert window.loading
    assert window.model.rows == []
    release.set()
    qtbot.waitUntil(lambda: not window.loading)
    assert window.model.rows == meters


def test_close_readings_window_during_request_does_not_reopen_or_crash(qtbot, api, meters):
    window = main_window(qtbot, api, meters)
    api.respond("/api/v1/meters/2/readings", [], delay=0.3)
    window.table.selectRow(0)
    window.open_selected_meter()
    child = window.readings_windows[2]
    assert child.loading
    qtbot.waitUntil(lambda: "/api/v1/meters/2/readings" in api.requests)
    child.close()
    qtbot.waitUntil(lambda: not window.readings_windows)
    qtbot.wait(350)
    assert window.readings_windows == {}
    assert window.isVisible()
