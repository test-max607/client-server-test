"""Translate ingestion results into the fixed gRPC contract."""

import logging
from concurrent.futures import ThreadPoolExecutor

import grpc
from sqlalchemy.exc import OperationalError

from server.database import Database
from server.ingestion import InvalidReading, ReadingConflict, UnknownMeter, submit_reading
from server.proto import meter_ingest_pb2, meter_ingest_pb2_grpc

logger = logging.getLogger(__name__)


class MeterIngestion(meter_ingest_pb2_grpc.MeterIngestionServicer):
    def __init__(self, database: Database):
        self.database = database

    def SubmitReading(self, request, context):
        try:
            reading_id, meter_id = submit_reading(
                self.database,
                request.serial_number,
                request.recorded_at,
                request.reading_kwh if request.HasField("reading_kwh") else None,
            )
        except InvalidReading as error:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        except UnknownMeter as error:
            context.abort(grpc.StatusCode.NOT_FOUND, str(error))
        except ReadingConflict as error:
            context.abort(grpc.StatusCode.ALREADY_EXISTS, str(error))
        except OperationalError:
            logger.exception("Database unavailable during gRPC ingestion")
            context.abort(grpc.StatusCode.UNAVAILABLE, "Storage is temporarily unavailable; retry later")
        except Exception:
            logger.exception("Cannot persist gRPC reading")
            context.abort(grpc.StatusCode.INTERNAL, "Unable to persist reading")
        return meter_ingest_pb2.SubmitReadingResponse(reading_id=reading_id, meter_id=meter_id)


class GrpcService:
    def __init__(self, database: Database, host: str, port: int):
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="meter-grpc")
        self.server = grpc.server(self.executor, options=(("grpc.so_reuseport", 0),))
        meter_ingest_pb2_grpc.add_MeterIngestionServicer_to_server(MeterIngestion(database), self.server)
        address = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
        try:
            self.port = self.server.add_insecure_port(address)
            if not self.port:
                raise RuntimeError(f"Cannot bind gRPC listener to {address}")
            self.server.start()
        except BaseException:
            self.server.stop(0).wait()
            self.executor.shutdown(wait=True)
            raise
        logger.info("gRPC listening on %s (port %s)", host, self.port)

    def close(self) -> None:
        self.server.stop(grace=5).wait()
        self.executor.shutdown(wait=True)
