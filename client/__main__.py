"""Run with: python -m client --server-url http://127.0.0.1:8000."""

import argparse
import sys

from PyQt6.QtWidgets import QApplication

from client.network import normalize_server_url
from client.windows import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="Просмотр показаний счётчиков")
    parser.add_argument("--server-url", default="http://127.0.0.1:8000", help="Адрес HTTP API")
    args = parser.parse_args()
    try:
        base_url = normalize_server_url(args.server_url)
    except ValueError as exc:
        parser.error(str(exc))
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Счётчики электроэнергии")
    window = MainWindow(base_url)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
