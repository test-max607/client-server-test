"""Read-only REST API and shared HTTP/gRPC application lifecycle."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from server.config import Settings
from server.database import Database, Meter, Reading
from server.grpc_service import GrpcService
from server.import_data import import_seed

logger = logging.getLogger(__name__)


class MeterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    serial_number: str
    location: str


class ReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    meter_id: int
    recorded_at: str
    reading_kwh: float


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(settings.database_path)
        grpc_service = None
        try:
            database.initialize()
            added = import_seed(database, settings.seed_path)
            logger.info("Seed imported: %s", added)
            grpc_service = GrpcService(database, settings.grpc_host, settings.grpc_port)
            app.state.database = database
            app.state.grpc_port = grpc_service.port
            yield
        finally:
            if grpc_service is not None:
                grpc_service.close()
            database.close()

    app = FastAPI(title="Metering API", version=settings.app_version, lifespan=lifespan)

    @app.get("/api/v1/meters", response_model=list[MeterResponse])
    def list_meters() -> list[Meter]:
        with app.state.database.sessions() as session:
            return list(session.scalars(select(Meter).order_by(Meter.serial_number)))

    @app.get("/api/v1/meters/{meter_id}/readings", response_model=list[ReadingResponse])
    def list_readings(meter_id: int) -> list[Reading]:
        with app.state.database.sessions() as session:
            # Bind parameters outside SQLite's integer range cannot denote an existing meter.
            if not -(2**63) <= meter_id < 2**63 or session.get(Meter, meter_id) is None:
                raise HTTPException(status_code=404, detail="Meter not found")
            return list(session.scalars(
                select(Reading)
                .where(Reading.meter_id == meter_id)
                .order_by(Reading.recorded_at.desc(), Reading.id.desc())
            ))

    @app.get("/healthz")
    def health() -> dict[str, str]:
        try:
            with app.state.database.sessions() as session:
                session.execute(text("SELECT 1 FROM meters LIMIT 1"))
        except SQLAlchemyError as error:
            logger.exception("Health check cannot reach the database")
            raise HTTPException(status_code=503, detail="Database unavailable") from error
        return {"status": "ok", "database": "ok", "version": settings.app_version}

    return app
