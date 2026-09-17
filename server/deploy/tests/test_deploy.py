"""Exercise the real deployment shell script without Docker or network access."""

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Deployment runs on Linux")

CURRENT = "a" * 40
PREVIOUS = "b" * 40
ADVANCED = "c" * 40
SCRIPT = Path(__file__).resolve().parents[1] / "deploy.sh"


@pytest.fixture
def deployment(tmp_path):
    """Keep actual filesystem/locking/checksum operations and fake only external services."""
    app = tmp_path / "application"
    release = app / "releases" / CURRENT
    release.mkdir(parents=True)
    data = app / "data"
    data.mkdir()
    (data / "metering.sqlite3").write_bytes(b"existing readings must survive")
    shutil.copyfile(SCRIPT, release / "deploy.sh")
    (release / "compose.yaml").write_text("services:\n  server:\n    image: ${APP_IMAGE}\n")
    (release / "smoke.py").write_text("# The real probe is invoked inside the container.\n")
    (release / "image.tar.gz").write_bytes(gzip.compress(b"fake docker image archive"))
    files = ("deploy.sh", "compose.yaml", "smoke.py", "image.tar.gz")
    (release / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}\n"
            for name in files
        )
    )

    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_command = textwrap.dedent(
        """\
        import json
        import os
        import sys
        from pathlib import Path

        command = Path(sys.argv[0]).name
        args = sys.argv[1:]
        event = {"command": command, "args": args}
        if command == "docker" and "--env-file" in args:
            values = Path(args[args.index("--env-file") + 1]).read_text().splitlines()
            event["version"] = next(
                (line.split("=", 1)[1] for line in values if line.startswith("APP_VERSION=")),
                "",
            )
        log = Path(os.environ["FAKE_EVENTS"])
        with log.open("a") as stream:
            stream.write(json.dumps(event) + "\\n")

        if command == "git":
            counter = Path(os.environ["FAKE_GIT_COUNTER"])
            index = int(counter.read_text()) if counter.exists() else 0
            counter.write_text(str(index + 1))
            versions = os.environ["FAKE_REMOTE_REVISIONS"].split(",")
            print(versions[min(index, len(versions) - 1)] + "\\trefs/heads/main")
        elif args[:2] == ["image", "inspect"]:
            print(os.environ["FAKE_LOADED_VERSION"])
        elif args[:2] == ["image", "ls"]:
            print("\\n".join("client-server-test:" + char * 40 for char in "abc"))
        elif args and args[0] == "compose":
            if "exec" in args:
                expected = args[args.index("--expected-version") + 1]
                if expected == os.environ.get("FAKE_FAIL_PROBE_VERSION"):
                    print("simulated smoke failure", file=sys.stderr)
                    sys.exit(19)
            if "up" in args and event.get("version") == os.environ.get("FAKE_FAIL_UP_VERSION"):
                print("simulated container startup failure", file=sys.stderr)
                sys.exit(23)
        """
    )
    for command in ("docker", "git"):
        executable = binaries / command
        executable.write_text(f"#!{sys.executable}\n{fake_command}")
        executable.chmod(0o755)

    environment = {
        **os.environ,
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        "APP_ROOT": str(app),
        "FAKE_EVENTS": str(tmp_path / "events.jsonl"),
        "FAKE_GIT_COUNTER": str(tmp_path / "git_calls"),
        "FAKE_REMOTE_REVISIONS": CURRENT,
        "FAKE_LOADED_VERSION": CURRENT,
    }
    return app, release, environment


def run_deploy(deployment, *, rollback=False, **overrides):
    _, release, environment = deployment
    arguments = ["bash", str(release / "deploy.sh"), CURRENT, str(release)]
    if rollback:
        arguments.append("--rollback")
    return subprocess.run(
        arguments,
        env={**environment, **overrides},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def events(deployment):
    path = Path(deployment[2]["FAKE_EVENTS"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def compose_events(deployment, action):
    return [
        event
        for event in events(deployment)
        if event["command"] == "docker"
        and event["args"][0] == "compose"
        and action in event["args"]
    ]


def previous_installation(deployment):
    app = deployment[0]
    contents = {
        "active.env": f"APP_IMAGE=client-server-test:{PREVIOUS}\nAPP_VERSION={PREVIOUS}\n",
        "active.compose.yaml": "services:\n  server:\n    image: previous-configuration\n",
        "current.version": f"{PREVIOUS}\n",
    }
    for name, value in contents.items():
        (app / name).write_text(value)
    return contents


def assert_data_untouched(deployment):
    assert (deployment[0] / "data" / "metering.sqlite3").read_bytes() == (
        b"existing readings must survive"
    )


def test_first_deployment_starts_and_verifies_before_recording_version(deployment):
    result = run_deploy(deployment)

    assert result.returncode == 0, result.stdout + result.stderr
    app, release, _ = deployment
    assert (app / "current.version").read_text().strip() == CURRENT
    assert f"APP_VERSION={CURRENT}\n" in (app / "active.env").read_text()
    assert (app / "active.compose.yaml").read_bytes() == (release / "compose.yaml").read_bytes()
    assert len(compose_events(deployment, "up")) == 1
    assert compose_events(deployment, "exec")[0]["args"][-1] == CURRENT
    assert not (app / "previous.version").exists()
    assert_data_untouched(deployment)


@pytest.mark.parametrize("remote_versions", [ADVANCED, f"{CURRENT},{ADVANCED}"])
def test_stale_release_never_replaces_running_service(deployment, remote_versions):
    original = previous_installation(deployment)

    result = run_deploy(deployment, FAKE_REMOTE_REVISIONS=remote_versions)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipping stale release" in result.stdout
    assert not compose_events(deployment, "up")
    for name, value in original.items():
        assert (deployment[0] / name).read_text() == value
    assert_data_untouched(deployment)


def test_empty_remote_revision_fails_without_touching_running_service(deployment):
    original = previous_installation(deployment)

    result = run_deploy(deployment, FAKE_REMOTE_REVISIONS="")

    assert result.returncode != 0
    assert not any(event["command"] == "docker" for event in events(deployment))
    for name, value in original.items():
        assert (deployment[0] / name).read_text() == value
    assert_data_untouched(deployment)


def test_invalid_checksum_never_invokes_docker(deployment):
    (deployment[1] / "image.tar.gz").write_bytes(b"corrupted archive")

    result = run_deploy(deployment)

    assert result.returncode != 0
    assert not events(deployment)
    assert not (deployment[0] / "current.version").exists()
    assert_data_untouched(deployment)


@pytest.mark.parametrize("failure", ["FAKE_FAIL_PROBE_VERSION", "FAKE_FAIL_UP_VERSION"])
def test_failed_update_restores_previous_configuration_and_keeps_readings(deployment, failure):
    original = previous_installation(deployment)

    result = run_deploy(deployment, **{failure: CURRENT})

    assert result.returncode != 0
    assert f"Restored {PREVIOUS}" in result.stderr
    for name, value in original.items():
        assert (deployment[0] / name).read_text() == value
    assert [event["version"] for event in compose_events(deployment, "up")] == [
        CURRENT,
        PREVIOUS,
    ]
    assert compose_events(deployment, "exec")[-1]["args"][-1] == PREVIOUS
    assert_data_untouched(deployment)


def test_failed_first_deployment_stops_service_without_removing_data(deployment):
    result = run_deploy(deployment, FAKE_FAIL_PROBE_VERSION=CURRENT)

    assert result.returncode != 0
    assert len(compose_events(deployment, "down")) == 1
    for name in ("current.version", "active.env", "active.compose.yaml"):
        assert not (deployment[0] / name).exists()
    assert_data_untouched(deployment)


def test_successful_update_keeps_previous_configuration_for_rollback(deployment):
    original = previous_installation(deployment)

    result = run_deploy(deployment)

    assert result.returncode == 0, result.stdout + result.stderr
    app = deployment[0]
    assert (app / "current.version").read_text().strip() == CURRENT
    assert (app / "previous.version").read_text() == original["current.version"]
    assert (app / "previous.env").read_text() == original["active.env"]
    assert (app / "previous.compose.yaml").read_text() == original["active.compose.yaml"]
    removed = [event["args"][-1] for event in events(deployment) if event["args"][:2] == ["image", "rm"]]
    assert removed == [f"client-server-test:{ADVANCED}"]
    assert_data_untouched(deployment)


def test_incomplete_previous_state_is_not_overwritten(deployment):
    app = deployment[0]
    (app / "current.version").write_text(PREVIOUS)

    result = run_deploy(deployment)

    assert result.returncode != 0
    assert "Incomplete active deployment state" in result.stderr
    assert (app / "current.version").read_text() == PREVIOUS
    assert not (app / "active.env").exists()
    assert not compose_events(deployment, "up")
    assert_data_untouched(deployment)


def test_external_probe_failure_rolls_back_completed_deployment(deployment):
    original = previous_installation(deployment)
    deployed = run_deploy(deployment)
    assert deployed.returncode == 0, deployed.stdout + deployed.stderr

    result = run_deploy(deployment, rollback=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Rolled back externally failed release" in result.stdout
    for name, value in original.items():
        assert (deployment[0] / name).read_text() == value
    assert [event["version"] for event in compose_events(deployment, "up")] == [
        CURRENT,
        PREVIOUS,
    ]
    assert compose_events(deployment, "exec")[-1]["args"][-1] == PREVIOUS
    assert_data_untouched(deployment)


def test_external_rollback_refuses_to_replace_a_newer_deployment(deployment):
    deployed = run_deploy(deployment)
    assert deployed.returncode == 0, deployed.stdout + deployed.stderr
    app = deployment[0]
    advanced_state = {
        "current.version": f"{ADVANCED}\n",
        "active.env": f"APP_IMAGE=client-server-test:{ADVANCED}\nAPP_VERSION={ADVANCED}\n",
        "active.compose.yaml": "services:\n  server:\n    image: advanced-configuration\n",
    }
    for name, value in advanced_state.items():
        (app / name).write_text(value)
    before = events(deployment)

    result = run_deploy(deployment, rollback=True)

    assert result.returncode != 0
    assert "refusing to roll back another release" in result.stderr
    assert events(deployment) == before
    for name, value in advanced_state.items():
        assert (app / name).read_text() == value
    assert_data_untouched(deployment)


def test_external_rollback_stops_first_deployment_and_keeps_data(deployment):
    deployed = run_deploy(deployment)
    assert deployed.returncode == 0, deployed.stdout + deployed.stderr

    result = run_deploy(deployment, rollback=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(compose_events(deployment, "down")) == 1
    for name in ("current.version", "active.env", "active.compose.yaml"):
        assert not (deployment[0] / name).exists()
    assert_data_untouched(deployment)
