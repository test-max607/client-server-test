import json
import os
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@dataclass(frozen=True)
class Response:
    body: object
    status: int = 200
    delay: float = 0
    release: threading.Event | None = None


class ApiStub:
    def __init__(self):
        self.routes = {}
        self.requests = []
        self.lock = threading.Lock()
        self.base_url = ""
        self.gates = []

    def respond(self, path, body, *, status=200, delay=0, release=None):
        with self.lock:
            self.routes[path] = Response(body, status, delay, release)
            if release is not None:
                self.gates.append(release)

    def request(self, path):
        with self.lock:
            self.requests.append(path)
            return self.routes.get(path, Response({"detail": "Not found"}, 404))


@pytest.fixture
def api():
    stub = ApiStub()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            response = stub.request(urlsplit(self.path).path)
            if response.release is not None:
                response.release.wait(timeout=5)
            if response.delay:
                time.sleep(response.delay)
            body = response.body
            payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            try:
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Aborted requests are expected in timeout and close-window tests.
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    stub.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield stub
    finally:
        for gate in stub.gates:
            gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def meters():
    return [
        {"id": 1, "serial_number": "00012", "location": "Второй этаж"},
        {"id": 2, "serial_number": "00002", "location": "Вход"},
        {"id": 3, "serial_number": "90000", "location": "Помещение 00012"},
    ]


@pytest.fixture
def readings():
    return [
        {
            "id": index,
            "meter_id": 1,
            "recorded_at": f"2026-09-{index + 10:02d}T10:00:00Z",
            "reading_kwh": value,
        }
        for index, value in enumerate([9.5, 10.0, 99.9, 100.0, 100.25], start=1)
    ]
