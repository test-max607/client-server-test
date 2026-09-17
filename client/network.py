"""Asynchronous JSON requests with an overall deadline and explicit cancellation."""

import json
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


def normalize_server_url(value: str) -> str:
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Некорректный порт сервера.") from exc
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or port == 0
        or any(character.isspace() for character in value)
    ):
        raise ValueError("Укажите адрес сервера вида http://127.0.0.1:8000.")
    return value.rstrip("/")


class JsonLoader(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, base_url: str, timeout_ms: int = 10_000, parent=None):
        super().__init__(parent)
        self.base_url = normalize_server_url(base_url)
        self.timeout_ms = timeout_ms
        self.manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._generation = 0

    def cancel(self) -> None:
        self._generation += 1
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()

    def get(self, path: str) -> None:
        self.cancel()
        generation = self._generation
        request = QNetworkRequest(QUrl(self.base_url + path))
        request.setRawHeader(b"Accept", b"application/json")
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self.manager.get(request)
        self._reply = reply
        timer = QTimer(reply)
        timer.setSingleShot(True)
        timed_out = False

        def expire():
            nonlocal timed_out
            timed_out = True
            reply.abort()

        def finished():
            timer.stop()
            try:
                if generation != self._generation:
                    return
                self._reply = None
                if timed_out:
                    self.failed.emit(
                        "Превышено время ожидания ответа сервера. Повторите обновление."
                    )
                    return
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if status is not None and not 200 <= status < 300:
                    self.failed.emit(f"Сервер вернул ошибку HTTP {status}. Повторите обновление.")
                    return
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    self.failed.emit("Не удалось связаться с сервером: " + reply.errorString())
                    return
                try:
                    payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
                except (ValueError, RecursionError):
                    self.failed.emit("Сервер вернул некорректный JSON.")
                    return
                self.succeeded.emit(payload)
            finally:
                reply.deleteLater()

        timer.timeout.connect(expire)
        reply.finished.connect(finished)
        timer.start(self.timeout_ms)
