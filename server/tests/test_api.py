from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import grpc
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from server.app import create_app
from server.import_data import SeedError
from server.proto import meter_ingest_pb2, meter_ingest_pb2_grpc


@pytest.fixture
def service(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        with grpc.insecure_channel(f"127.0.0.1:{app.state.grpc_port}") as channel:
            grpc.channel_ready_future(channel).result(timeout=5)
            stub = meter_ingest_pb2_grpc.MeterIngestionStub(channel)
            yield app, client, stub


def request(**changes):
    fields = {
        "serial_number": "00000001",
        "recorded_at": "2026-09-18T10:00:00Z",
        "reading_kwh": 10.25,
    }
    fields.update(changes)
    return meter_ingest_pb2.SubmitReadingRequest(**fields)


def test_rest_lists_and_empty_unknown_meter(service):
    _, client, _ = service
    response = client.get("/api/v1/meters")
    assert response.status_code == 200
    assert response.json() == [
        {"id": 1, "serial_number": "00000001", "location": "Office"},
        {"id": 2, "serial_number": "00000002", "location": "Warehouse"},
    ]
    readings = client.get("/api/v1/meters/1/readings")
    assert readings.status_code == 200
    assert readings.json() == [
        {"id": 10, "meter_id": 1, "recorded_at": "2026-09-11T10:00:00Z", "reading_kwh": 9.5}
    ]
    assert client.get("/api/v1/meters/2/readings").json() == []
    assert client.get("/api/v1/meters/999/readings").status_code == 404
    health = client.get("/healthz")
    assert health.status_code == 200
    assert "test-version" in health.json().values()


def test_grpc_new_reading_visible_in_rest_and_duplicate_keeps_id(service):
    _, client, stub = service
    result = stub.SubmitReading(request(), timeout=5)
    assert result.reading_id > 10
    assert result.meter_id == 1
    assert stub.SubmitReading(request(), timeout=5) == result
    rows = client.get("/api/v1/meters/1/readings").json()
    assert len(rows) == 2
    assert {
        "id": result.reading_id,
        "meter_id": 1,
        "recorded_at": "2026-09-18T10:00:00Z",
        "reading_kwh": 10.25,
    } in rows


def test_grpc_imported_duplicate_and_zero_presence(service):
    _, client, stub = service
    result = stub.SubmitReading(
        request(recorded_at="2026-09-11T10:00:00Z", reading_kwh=9.5), timeout=5
    )
    assert (result.reading_id, result.meter_id) == (10, 1)
    zero = stub.SubmitReading(request(serial_number="00000002", reading_kwh=0), timeout=5)
    assert zero.meter_id == 2
    assert client.get("/api/v1/meters/2/readings").json()[0]["reading_kwh"] == 0


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"serial_number": ""}, grpc.StatusCode.INVALID_ARGUMENT),
        ({"recorded_at": "2026-02-30T10:00:00Z"}, grpc.StatusCode.INVALID_ARGUMENT),
        ({"reading_kwh": None}, grpc.StatusCode.INVALID_ARGUMENT),
        ({"reading_kwh": -1}, grpc.StatusCode.INVALID_ARGUMENT),
        ({"reading_kwh": float("nan")}, grpc.StatusCode.INVALID_ARGUMENT),
        ({"reading_kwh": float("inf")}, grpc.StatusCode.INVALID_ARGUMENT),
        ({"serial_number": "99999999"}, grpc.StatusCode.NOT_FOUND),
        (
            {"recorded_at": "2026-09-11T10:00:00Z", "reading_kwh": 99},
            grpc.StatusCode.ALREADY_EXISTS,
        ),
    ],
)
def test_grpc_rejections_do_not_change_data(service, changes, status):
    _, client, stub = service
    with pytest.raises(grpc.RpcError) as error:
        stub.SubmitReading(request(**changes), timeout=5)
    assert error.value.code() == status
    assert len(client.get("/api/v1/meters/1/readings").json()) == 1


def test_concurrent_grpc_retries_return_one_record(service):
    _, client, stub = service
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: stub.SubmitReading(request(), timeout=10), range(16)))
    assert len({result.reading_id for result in results}) == 1
    assert len(client.get("/api/v1/meters/1/readings").json()) == 2


def test_concurrent_grpc_conflicting_values_have_one_winner(service):
    _, client, stub = service
    barrier = Barrier(8)

    def submit(value):
        barrier.wait(timeout=10)
        try:
            result = stub.SubmitReading(request(reading_kwh=value), timeout=10)
            return result.reading_id, value
        except grpc.RpcError as error:
            assert error.code() == grpc.StatusCode.ALREADY_EXISTS
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    reading_id, value = winners[0]
    rows = client.get("/api/v1/meters/1/readings").json()
    assert len(rows) == 2
    persisted = next(row for row in rows if row["id"] == reading_id)
    assert persisted["reading_kwh"] == value


@pytest.mark.parametrize("operational", [False, True])
def test_grpc_commit_failure_returns_error_and_rolls_back(service, operational):
    app, client, stub = service

    def fail_commit(connection):
        if operational:
            raise OperationalError("COMMIT", {}, RuntimeError("database unavailable"))
        raise RuntimeError("simulated commit failure")

    event.listen(app.state.database.engine, "commit", fail_commit)
    try:
        with pytest.raises(grpc.RpcError) as error:
            stub.SubmitReading(request(), timeout=5)
        expected = grpc.StatusCode.UNAVAILABLE if operational else grpc.StatusCode.INTERNAL
        assert error.value.code() == expected
    finally:
        event.remove(app.state.database.engine, "commit", fail_commit)
    assert len(client.get("/api/v1/meters/1/readings").json()) == 1


def test_restart_preserves_grpc_data_and_closes_server(settings):
    app = create_app(settings)
    with TestClient(app):
        channel = grpc.insecure_channel(f"127.0.0.1:{app.state.grpc_port}")
        stub = meter_ingest_pb2_grpc.MeterIngestionStub(channel)
        result = stub.SubmitReading(request(), timeout=5)
    try:
        with pytest.raises(grpc.RpcError) as error:
            stub.SubmitReading(request(), timeout=1)
        assert error.value.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)
    finally:
        channel.close()
    restarted = create_app(settings)
    with TestClient(restarted) as client:
        rows = client.get("/api/v1/meters/1/readings").json()
        assert len(rows) == 2
        assert any(row["id"] == result.reading_id for row in rows)


def test_invalid_seed_prevents_startup(settings):
    settings.seed_path.write_text("not json", encoding="utf-8")
    with pytest.raises(SeedError):
        with TestClient(create_app(settings)):
            pytest.fail("Application started despite invalid seed")


def test_occupied_grpc_port_prevents_startup_and_shutdown_releases_port(settings):
    first = create_app(settings)
    with TestClient(first) as client:
        fixed_settings = replace(settings, grpc_port=first.state.grpc_port)
        with pytest.raises(RuntimeError):
            with TestClient(create_app(fixed_settings)):
                pytest.fail("Second application started on an occupied gRPC port")
        assert client.get("/healthz").status_code == 200
        with grpc.insecure_channel(f"127.0.0.1:{fixed_settings.grpc_port}") as channel:
            stub = meter_ingest_pb2_grpc.MeterIngestionStub(channel)
            result = stub.SubmitReading(request(), timeout=5)

    restarted = create_app(fixed_settings)
    with TestClient(restarted) as client:
        assert restarted.state.grpc_port == fixed_settings.grpc_port
        with grpc.insecure_channel(f"127.0.0.1:{fixed_settings.grpc_port}") as channel:
            stub = meter_ingest_pb2_grpc.MeterIngestionStub(channel)
            assert stub.SubmitReading(request(), timeout=5) == result
        assert len(client.get("/api/v1/meters/1/readings").json()) == 2
