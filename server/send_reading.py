"""Send one reading over gRPC and print its persisted identifiers."""

import argparse
import json

import grpc

from server.proto import meter_ingest_pb2, meter_ingest_pb2_grpc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="127.0.0.1:50051", help="gRPC host:port")
    parser.add_argument("--serial-number", required=True)
    parser.add_argument("--recorded-at", required=True, help="UTC YYYY-MM-DDTHH:mm:ssZ")
    parser.add_argument("--reading-kwh", required=True, type=float)
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    request = meter_ingest_pb2.SubmitReadingRequest(
        serial_number=args.serial_number,
        recorded_at=args.recorded_at,
        reading_kwh=args.reading_kwh,
    )
    try:
        with grpc.insecure_channel(args.target) as channel:
            response = meter_ingest_pb2_grpc.MeterIngestionStub(channel).SubmitReading(
                request, timeout=args.timeout,
            )
    except grpc.RpcError as error:
        parser.exit(1, f"{error.code().name}: {error.details()}\n")
    print(json.dumps({"reading_id": response.reading_id, "meter_id": response.meter_id}))


if __name__ == "__main__":
    main()
