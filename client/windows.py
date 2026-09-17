"""Meter list and independent reading windows; all networking stays asynchronous."""

from PyQt6.QtCore import QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from client.models import MeterTableModel, ReadingTableModel, parse_meters, parse_readings
from client.network import JsonLoader


class DataWindow(QMainWindow):
    def __init__(self, base_url, model, title, subtitle, timeout_ms, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(840, 570)
        self.setMinimumSize(550, 350)
        self.loading = False
        self._closed = False
        self._error = ""
        self._loaded_once = False
        self.model = model
        self.model.setParent(self)
        self.loader = JsonLoader(base_url, timeout_ms, self)
        self.loader.succeeded.connect(self._received)
        self.loader.failed.connect(self._failed)

        central = QWidget(self)
        self.setCentralWidget(central)
        self.layout = QVBoxLayout(central)
        self.layout.setContentsMargins(18, 18, 18, 18)
        self.layout.setSpacing(12)
        heading = QLabel(title)
        heading_font = heading.font()
        heading_font.setPointSize(18)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        self.layout.addWidget(heading)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setWordWrap(True)
            subtitle_label.setTextFormat(Qt.TextFormat.PlainText)
            self.layout.addWidget(subtitle_label)

        self.toolbar = QHBoxLayout()
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.setToolTip("Получить актуальные данные с сервера (F5)")
        self.refresh_button.clicked.connect(self.refresh)
        self.toolbar.addStretch()
        self.toolbar.addWidget(self.refresh_button)
        self.layout.addLayout(self.toolbar)

        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setDynamicSortFilter(True)
        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setShowGrid(False)
        self.layout.addWidget(self.table, 1)

        self.status_label = QLabel("Нажмите «Обновить», чтобы загрузить данные.")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.layout.addWidget(self.status_label)
        self.connection_label = QLabel("Сервер: " + self.loader.base_url)
        self.connection_label.setTextFormat(Qt.TextFormat.PlainText)
        self.connection_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.layout.addWidget(self.connection_label)
        self.refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self.refresh_shortcut.activated.connect(self.refresh)

    def refresh(self):
        if self._closed or self.loading:
            return
        self._before_refresh()
        self.loading = True
        self._error = ""
        self.refresh_button.setEnabled(False)
        self._update_status()
        self.loader.get(self.endpoint)

    def _before_refresh(self):
        pass

    def _received(self, payload):
        if self._closed:
            return
        try:
            rows = self._parse_payload(payload)
        except ValueError as exc:
            self._failed("Ошибка данных: " + str(exc))
            return
        self._apply_rows(rows)
        self.loading = False
        self._loaded_once = True
        self._error = ""
        self.refresh_button.setEnabled(True)
        self._update_status()

    def _apply_rows(self, rows):
        self.model.replace_rows(rows)

    def _failed(self, message):
        if self._closed:
            return
        self.loading = False
        self._error = message
        self.refresh_button.setEnabled(True)
        self._update_status()

    def _update_status(self):
        if self.loading:
            self.status_label.setText("Загрузка данных…")
        elif self._error:
            suffix = " Показаны ранее загруженные данные." if self._loaded_once else ""
            self.status_label.setText("Ошибка: " + self._error + suffix)
        elif self._loaded_once:
            self.status_label.setText(self._result_text())

    def closeEvent(self, event: QCloseEvent):
        self._closed = True
        self.loading = False
        self.loader.cancel()
        super().closeEvent(event)


class MainWindow(DataWindow):
    endpoint = "/api/v1/meters"

    def __init__(self, base_url: str, timeout_ms: int = 10_000, autoload: bool = True):
        super().__init__(
            base_url,
            MeterTableModel(),
            "Счётчики электроэнергии",
            "Выберите счётчик, чтобы посмотреть его показания.",
            timeout_ms,
        )
        self.timeout_ms = timeout_ms
        self.readings_windows: dict[int, ReadingsWindow] = {}
        self._selected_before_refresh: int | None = None
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Поиск по серийному номеру")
        self.search.setAccessibleName("Поиск по серийному номеру")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_changed)
        self.toolbar.insertWidget(0, self.search, 1)
        self.open_button = QPushButton("Показания")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected_meter)
        self.toolbar.addWidget(self.open_button)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.activated.connect(self.open_selected_meter)
        if autoload:
            self.refresh()

    def _filter_changed(self, text):
        self.proxy.setFilterFixedString(text)
        self._selection_changed()
        self._update_status()

    def _selection_changed(self, *_):
        self.open_button.setEnabled(bool(self.table.selectionModel().selectedRows()))

    def selected_meter(self) -> dict | None:
        selection = self.table.selectionModel().selectedRows()
        if not selection:
            return None
        source = self.proxy.mapToSource(selection[0])
        return self.model.rows[source.row()]

    def _before_refresh(self):
        meter = self.selected_meter()
        self._selected_before_refresh = meter["id"] if meter else None

    def _parse_payload(self, payload):
        return parse_meters(payload)

    def _apply_rows(self, rows):
        super()._apply_rows(rows)
        for row_index, row in enumerate(rows):
            if row["id"] == self._selected_before_refresh:
                index = self.proxy.mapFromSource(self.model.index(row_index, 0))
                if index.isValid():
                    self.table.selectRow(index.row())
                break
        self._selection_changed()

    def _result_text(self):
        if not self.model.rows:
            return "Счётчиков пока нет."
        count = self.proxy.rowCount()
        if not count:
            return "По этому серийному номеру ничего не найдено."
        if self.search.text():
            return f"Найдено счётчиков: {count} из {len(self.model.rows)}."
        return f"Счётчиков: {count}."

    def open_selected_meter(self, *_):
        meter = self.selected_meter()
        if meter is None or self._closed:
            return
        window = self.readings_windows.get(meter["id"])
        if window is None:
            window = ReadingsWindow(self.loader.base_url, meter, self.timeout_ms, parent=self)
            self.readings_windows[meter["id"]] = window
            window.closed.connect(lambda meter_id: self.readings_windows.pop(meter_id, None))
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def closeEvent(self, event: QCloseEvent):
        for window in list(self.readings_windows.values()):
            window.close()
        super().closeEvent(event)


class ReadingsWindow(DataWindow):
    # IDs come from SQLite/gRPC int64 and can exceed Qt's 32-bit `int` signal type.
    closed = pyqtSignal(object)

    def __init__(self, base_url: str, meter: dict, timeout_ms: int = 10_000, parent=None):
        self.meter = meter
        self.endpoint = f"/api/v1/meters/{meter['id']}/readings"
        super().__init__(
            base_url,
            ReadingTableModel(),
            "Показания · " + meter["serial_number"],
            meter["location"] + " · Все даты и время указаны в UTC.",
            timeout_ms,
            parent,
        )
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.refresh()

    def _parse_payload(self, payload):
        return parse_readings(payload, self.meter["id"])

    def _result_text(self):
        return f"Показаний: {len(self.model.rows)}." if self.model.rows else "Показаний пока нет."

    def closeEvent(self, event: QCloseEvent):
        if not self._closed:
            self.closed.emit(self.meter["id"])
        super().closeEvent(event)
