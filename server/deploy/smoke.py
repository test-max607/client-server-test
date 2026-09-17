"""Check REST, imported data and gRPC without modifying the database."""

import argparse
import json
import urllib.request

import grpc

from server.proto import meter_ingest_pb2, meter_ingest_pb2_grpc


def check(http_url: str, grpc_target: str, expected_version: str) -> None:
    base_url = http_url.rstrip("/")
    with urllib.request.urlopen(f"{base_url}/healthz", timeout=10) as response:
        health = json.load(response)
    if health.get("status") != "ok" or health.get("version") != expected_version:
        raise RuntimeError(f"Unexpected server health/version: {health!r}")
    with urllib.request.urlopen(f"{base_url}/api/v1/meters", timeout=10) as response:
        meters = json.load(response)
    if not isinstance(meters, list) or len(meters) != 100:
        raise RuntimeError("Expected the 100 imported meters")
    with grpc.insecure_channel(grpc_target) as channel:
        stub = meter_ingest_pb2_grpc.MeterIngestionStub(channel)
        try:
            stub.SubmitReading(meter_ingest_pb2.SubmitReadingRequest(), timeout=10)
        except grpc.RpcError as error:
            if error.code() != grpc.StatusCode.INVALID_ARGUMENT:
                raise RuntimeError(f"Unexpected gRPC status: {error.code()}") from error
        else:
            raise RuntimeError("gRPC accepted an invalid reading")
    print(f"REST and gRPC are ready; version={expected_version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-url", default="http://127.0.0.1:8000")
    parser.add_argument("--grpc-target", default="127.0.0.1:50051")
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    check(args.http_url, args.grpc_target, args.expected_version)


if __name__ == "__main__":
    main()
