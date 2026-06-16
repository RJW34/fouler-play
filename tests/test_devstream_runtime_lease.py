import sys
from datetime import datetime, timezone

from scripts import devstream_runtime_lease as lease


NOW = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)


def write_lease(path, **overrides):
    payload = {
        "schemaVersion": "fouler-play-runtime-lease/v1",
        "projectId": "fouler-play",
        "leaseId": "lease-test",
        "status": "active",
        "machine": "JIGGLYPUFF",
        "account": "bot",
        "maxRunCount": 10,
        "maxCycles": 2,
        "maxConcurrentBattles": 3,
        "replayBehavior": "save",
        "proofWindow": {
            "startsAt": "2026-06-08T00:00:00+00:00",
            "expiresAt": "2026-06-09T00:00:00+00:00",
        },
    }
    payload.update(overrides)
    path.write_text(lease.json.dumps(payload), encoding="utf-8")
    return path


def test_missing_runtime_lease_fails_closed(tmp_path):
    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=tmp_path / "missing.json",
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "missing" in payload["blockers"][0]


def test_valid_runtime_lease_allows_bounded_live_start(tmp_path):
    path = write_lease(tmp_path / "runtime-lease.json")

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=2,
        requested_max_concurrent_battles=3,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is True
    assert payload["lease"]["maxRunCount"] == 10


def test_runtime_lease_blocks_expired_proof_window(tmp_path):
    path = write_lease(
        tmp_path / "runtime-lease.json",
        proofWindow={
            "startsAt": "2026-06-07T00:00:00+00:00",
            "expiresAt": "2026-06-08T00:00:00+00:00",
        },
    )

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "expired" in " ".join(payload["blockers"])


def test_runtime_lease_blocks_run_count_over_scope(tmp_path):
    path = write_lease(tmp_path / "runtime-lease.json", maxRunCount=1)

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=2,
        requested_max_concurrent_battles=1,
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "exceeds lease maxRunCount" in " ".join(payload["blockers"])


def test_jiggly_runtime_start_lease_delegates_to_supervisor_and_child_start(tmp_path):
    path = tmp_path / "runtime-lease.json"
    artifact = lease.build_runtime_lease_artifact(
        purpose="jigglypuff-runtime-start",
        machine="JIGGLYPUFF",
        account="bot",
        run_count=30,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=30,
        now=NOW,
    )
    lease.atomic_write_json(path, artifact)

    supervise = lease.validate_runtime_lease(
        purpose="devstream-supervise",
        lease_path=path,
        requested_run_count=30,
        requested_max_cycles=1,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )
    child_start = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=30,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )
    runner = lease.validate_runtime_lease(
        purpose="run-py-battle-runner",
        lease_path=path,
        requested_run_count=30,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert artifact["allowedPurposes"] == [
        "jigglypuff-runtime-start",
        "devstream-supervise",
        "devstream-start",
        "run-py-battle-runner",
    ]
    assert supervise["ok"] is True
    assert child_start["ok"] is True
    assert runner["ok"] is True


def test_supervise_lease_delegates_to_child_start(tmp_path):
    path = tmp_path / "runtime-lease.json"
    artifact = lease.build_runtime_lease_artifact(
        purpose="devstream-supervise",
        machine="JIGGLYPUFF",
        account="bot",
        run_count=10,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=30,
        now=NOW,
    )
    lease.atomic_write_json(path, artifact)

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=10,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert artifact["allowedPurposes"] == ["devstream-supervise", "devstream-start", "run-py-battle-runner"]
    assert payload["ok"] is True


def test_devstream_start_lease_delegates_to_run_py_battle_runner(tmp_path):
    path = tmp_path / "runtime-lease.json"
    artifact = lease.build_runtime_lease_artifact(
        purpose="devstream-start",
        machine="JIGGLYPUFF",
        account="bot",
        run_count=10,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=30,
        now=NOW,
    )
    lease.atomic_write_json(path, artifact)

    payload = lease.validate_runtime_lease(
        purpose="run-py-battle-runner",
        lease_path=path,
        requested_run_count=10,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert artifact["allowedPurposes"] == ["devstream-start", "run-py-battle-runner"]
    assert payload["ok"] is True


def test_supervise_requires_explicit_max_cycles(tmp_path):
    path = write_lease(tmp_path / "runtime-lease.json")

    payload = lease.validate_runtime_lease(
        purpose="devstream-supervise",
        lease_path=path,
        requested_run_count=1,
        requested_max_cycles=0,
        requested_max_concurrent_battles=1,
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "requested max cycles" in " ".join(payload["blockers"])


def test_generated_dry_run_runtime_lease_round_trips_without_execute_authority(tmp_path):
    path = tmp_path / "runtime-lease.json"
    artifact = lease.build_runtime_lease_artifact(
        purpose="devstream-start-dry-run",
        machine="JIGGLYPUFF",
        account="bot",
        run_count=1,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="never",
        valid_minutes=30,
        now=NOW,
    )
    lease.atomic_write_json(path, artifact)

    dry_run = lease.validate_runtime_lease(
        purpose="devstream-start-dry-run",
        lease_path=path,
        requested_run_count=1,
        requested_max_cycles=1,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )
    execute = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert dry_run["ok"] is True
    assert dry_run["lease"]["maxRunCount"] == 1
    assert execute["ok"] is False
    assert "does not allow purpose devstream-start" in " ".join(execute["blockers"])


def test_cli_write_mode_writes_and_validates_finite_lease(tmp_path, monkeypatch, capsys):
    path = tmp_path / "runtime-lease.json"
    monkeypatch.setattr(lease, "utc_now", lambda: NOW)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devstream_runtime_lease.py",
            "--write",
            "--runtime-lease",
            str(path),
            "--purpose",
            "devstream-start-dry-run",
            "--machine",
            "JIGGLYPUFF",
            "--account",
            "bot",
            "--run-count",
            "1",
            "--max-cycles",
            "1",
            "--max-concurrent-battles",
            "1",
            "--replay-behavior",
            "never",
            "--valid-minutes",
            "30",
            "--require-run-count",
            "--require-max-cycles",
            "--require-max-concurrent-battles",
            "--require-replay-behavior",
        ],
    )

    assert lease.main() == 0

    payload = lease.json.loads(capsys.readouterr().out)
    written = lease.json.loads(path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["writtenLease"]["noRuntimeActions"] is True
    assert payload["validation"]["ok"] is True
    assert written["allowedPurposes"] == ["devstream-start-dry-run"]
