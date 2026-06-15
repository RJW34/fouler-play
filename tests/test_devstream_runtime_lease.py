import json
from datetime import datetime, timezone

from scripts.devstream_runtime_lease import build_runtime_lease_artifact, validate_runtime_lease


def test_runtime_lease_validator_accepts_utf8_bom_json(tmp_path):
    lease = build_runtime_lease_artifact(
        purpose="devstream-supervise",
        machine="JIGGLYPUFF",
        account="examplebot",
        run_count=30,
        max_cycles=500,
        max_concurrent_battles=3,
        replay_behavior="always",
        valid_minutes=60,
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    path = tmp_path / "runtime-lease.json"
    path.write_text(json.dumps(lease), encoding="utf-8-sig")

    result = validate_runtime_lease(
        purpose="devstream-supervise",
        lease_path=path,
        requested_run_count=30,
        requested_max_cycles=500,
        requested_max_concurrent_battles=3,
        requested_account="examplebot",
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=datetime(2026, 6, 15, 0, 1, tzinfo=timezone.utc),
    )

    assert result["ok"] is True
    assert result["blockers"] == []
