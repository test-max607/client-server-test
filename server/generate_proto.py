"""Regenerate checked-in protobuf bindings, or verify them with --check."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO = "server/proto/meter_ingest.proto"
OUTPUTS = ("server/proto/meter_ingest_pb2.py", "server/proto/meter_ingest_pb2_grpc.py")


def generate(output: Path) -> None:
    subprocess.run([
        sys.executable, "-m", "grpc_tools.protoc",
        f"-I{ROOT}", f"--python_out={output}", f"--grpc_python_out={output}", PROTO,
    ], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        generate(ROOT)
        return
    with tempfile.TemporaryDirectory(prefix="meter-proto-") as directory:
        output = Path(directory)
        generate(output)
        changed = [name for name in OUTPUTS if not (ROOT / name).exists()
                   or (ROOT / name).read_bytes() != (output / name).read_bytes()]
    if changed:
        parser.exit(1, f"Generated files differ: {', '.join(changed)}\nRun python -m server.generate_proto\n")
    print("Protobuf bindings match the checked-in contract")


if __name__ == "__main__":
    main()
