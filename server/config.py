"""Environment configuration shared by the server and import command."""

import os
from dataclasses import dataclass, field
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    database_path: Path = field(default_factory=lambda: SERVER_ROOT / "runtime" / "metering.sqlite3")
    seed_path: Path = field(default_factory=lambda: SERVER_ROOT / "data.json")
    http_host: str = "0.0.0.0"
    http_port: int = 8000
    grpc_host: str = "0.0.0.0"
    grpc_port: int = 50051
    app_version: str = "dev"

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        settings = cls(
            database_path=Path(os.getenv("DATABASE_PATH", str(defaults.database_path))),
            seed_path=Path(os.getenv("SEED_PATH", str(defaults.seed_path))),
            http_host=os.getenv("HTTP_HOST", defaults.http_host),
            http_port=int(os.getenv("HTTP_PORT", defaults.http_port)),
            grpc_host=os.getenv("GRPC_HOST", defaults.grpc_host),
            grpc_port=int(os.getenv("GRPC_PORT", defaults.grpc_port)),
            app_version=os.getenv("APP_VERSION", defaults.app_version),
        )
        if not 1 <= settings.http_port <= 65535 or not 1 <= settings.grpc_port <= 65535:
            raise ValueError("HTTP_PORT and GRPC_PORT must be between 1 and 65535")
        return settings
