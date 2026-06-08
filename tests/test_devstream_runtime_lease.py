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
