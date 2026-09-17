"""Run one HTTP worker with its embedded gRPC server."""

import logging

import uvicorn

from server.app import create_app
from server.config import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.http_host, port=settings.http_port, workers=1)


if __name__ == "__main__":
    main()
