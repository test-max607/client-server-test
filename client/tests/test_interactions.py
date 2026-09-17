"""Exercise user gestures through Qt's event loop, rather than calling handlers."""

import threading

import pytest
from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer

from client.windows import MainWindow, ReadingsWindow

METERS_PATH = "/api/v1/meters"


def show_window(qtbot, window):
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(lambda: not window.loading)
    return window


def click_header(qtbot, window, column):
    header = window.table.horizontalHeader()
    position = header.rect().center()
    position.setX(header.sectionViewportPosition(column) + header.sectionSize(column) // 2)
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=position)


def ordered_rows(window):
    return [
        window.model.rows[window.proxy.mapToSource(window.proxy.index(row, 0)).row()]
        for row in range(window.proxy.rowCount())
    ]


def click_row(qtbot, window, row):
    index = window.proxy.index(row, 0)
    position = window.table.visualRect(index).center()
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=position)
    return position


@pytest.mark.parametrize("column", [0, 1])
@pytest.mark.parametrize("kind", ["meters", "readings"])
def test_header_clicks_sort_every_column_in_both_directions(
    qtbot, api, meters, readings, kind, column
):
    if kind == "meters":
        api.respond(METERS_PATH, list(reversed(meters)))
        window = show_window(qtbot, MainWindow(api.base_url))
        field = ("serial_number", "location")[column]
    else:
        api.respond("/api/v1/meters/1/readings", list(reversed(readings)))
        window = show_window(qtbot, ReadingsWindow(api.base_url, meters[0]))
        field = ("recorded_at", "reading_kwh")[column]

    seen_orders = set()
    for _ in range(2):
        click_header(qtbot, window, column)
        assert window.proxy.sortColumn() == column
        order = window.proxy.sortOrder()
        seen_orders.add(order)
        actual = [row[field] for row in ordered_rows(window)]
        assert actual == sorted(actual, reverse=order == Qt.SortOrder.DescendingOrder)
    assert seen_orders == {Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder}


@pytest.mark.parametrize("gesture", ["button", "enter", "double_click"])
def test_opening_gestures_use_selected_row_after_sorting_and_reuse_window(
    qtbot, api, meters, gesture
):
    api.respond(METERS_PATH, meters)
    for meter in meters:
        api.respond(f"/api/v1/meters/{meter['id']}/readings", [])
    window = show_window(qtbot, MainWindow(api.base_url))
    click_header(qtbot, window, 1)
    click_header(qtbot, window, 1)
    expected = ordered_rows(window)[0]
    # Location order differs from the source data order, exposing mapping mistakes.
    assert expected["id"] != meters[0]["id"]

    def open_with_gesture():
        window.activateWindow()
        qtbot.waitUntil(window.isActiveWindow)
        position = click_row(qtbot, window, 0)
        if gesture == "button":
            assert window.open_button.isEnabled()
            qtbot.mouseClick(window.open_button, Qt.MouseButton.LeftButton)
        elif gesture == "enter":
            window.table.setFocus()
            qtbot.keyClick(window.table, Qt.Key.Key_Return)
        else:
            qtbot.mouseDClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=position)

    open_with_gesture()
    qtbot.waitUntil(lambda: expected["id"] in window.readings_windows)
    child = window.readings_windows[expected["id"]]
    qtbot.waitUntil(lambda: not child.loading)
    assert child.isVisible()
    assert expected["serial_number"] in child.windowTitle()
    assert api.requests[-1] == f"/api/v1/meters/{expected['id']}/readings"
    assert len(window.readings_windows) == 1

    before_requests = len(api.requests)
    open_with_gesture()
    assert window.readings_windows == {expected["id"]: child}
    assert len(api.requests) == before_requests


@pytest.mark.parametrize("kind", ["meters", "readings"])
def test_f5_refreshes_active_window_and_ignores_repeat_while_loading(
    qtbot, api, meters, readings, kind
):
    if kind == "meters":
        path = METERS_PATH
        initial = meters
        replacement = list(reversed(meters))
        api.respond(path, initial)
        window = show_window(qtbot, MainWindow(api.base_url))
        focus = window.search
    else:
        path = "/api/v1/meters/1/readings"
        initial = readings
        replacement = readings[:-1]
        api.respond(path, initial)
        window = show_window(qtbot, ReadingsWindow(api.base_url, meters[0]))
        focus = window.table

    gate = threading.Event()
    api.respond(path, replacement, release=gate)
    before_requests = api.requests.count(path)
    focus.setFocus()
    qtbot.keyClick(focus, Qt.Key.Key_F5)
    qtbot.waitUntil(lambda: api.requests.count(path) == before_requests + 1)
    assert window.loading
    assert not window.refresh_button.isEnabled()
    assert window.model.rows == initial
    qtbot.keyClick(focus, Qt.Key.Key_F5)
    # Processing a timer proves that key events were handled while the request waits.
    ticks = []
    QTimer.singleShot(20, lambda: ticks.append(True))
    qtbot.waitUntil(lambda: bool(ticks))
    assert api.requests.count(path) == before_requests + 1
    gate.set()
    qtbot.waitUntil(lambda: not window.loading)
    assert window.model.rows == replacement
    assert window.refresh_button.isEnabled()


@pytest.mark.parametrize("body,status", [({"detail": "Unavailable"}, 503), (b"not JSON", 200)])
def test_first_load_error_can_recover_through_refresh_button(qtbot, api, meters, body, status):
    api.respond(METERS_PATH, body, status=status)
    window = show_window(qtbot, MainWindow(api.base_url))
    assert window.model.rows == []
    assert "Ошибка" in window.status_label.text()
    assert "ранее загруженные" not in window.status_label.text()
    assert window.refresh_button.isEnabled()
    assert not window.open_button.isEnabled()

    api.respond(METERS_PATH, meters)
    qtbot.mouseClick(window.refresh_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.loading)
    assert window.model.rows == meters
    assert "Ошибка" not in window.status_label.text()
    click_row(qtbot, window, 0)
    assert window.open_button.isEnabled()


def test_closing_main_window_cancels_parent_and_multiple_child_requests(qtbot, api, meters):
    api.respond(METERS_PATH, meters)
    window = show_window(qtbot, MainWindow(api.base_url))
    gate = threading.Event()
    children = []
    for row in (0, 1):
        meter_id = ordered_rows(window)[row]["id"]
        path = f"/api/v1/meters/{meter_id}/readings"
        api.respond(path, [], release=gate)
        window.activateWindow()
        qtbot.waitUntil(window.isActiveWindow)
        click_row(qtbot, window, row)
        qtbot.mouseClick(window.open_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: path in api.requests)
        children.append(window.readings_windows[meter_id])
    assert all(child.loading for child in children)

    api.respond(METERS_PATH, [], release=gate)
    qtbot.mouseClick(window.refresh_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: api.requests.count(METERS_PATH) == 2)
    assert window.loading
    window.close()
    qtbot.waitUntil(lambda: sip.isdeleted(window))
    assert all(sip.isdeleted(child) for child in children)
    gate.set()
    ticks = []
    QTimer.singleShot(30, lambda: ticks.append(True))
    qtbot.waitUntil(lambda: bool(ticks))
