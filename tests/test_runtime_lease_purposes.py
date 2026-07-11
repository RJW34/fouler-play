import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from scripts.devstream_runtime_lease import build_runtime_lease_artifact, expanded_allowed_purposes


def test_jiggly_runtime_start_lease_allows_continuous_supervisor_chain():
    allowed = expanded_allowed_purposes("jigglypuff-runtime-start")

    assert allowed == [
        "jigglypuff-runtime-start",
        "devstream-start-continuous-dry-run",
        "devstream-start-continuous",
        "devstream-supervise",
        "devstream-start-dry-run",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ]


def test_continuous_start_lease_allows_supervisor_and_battle_runner():
    lease = build_runtime_lease_artifact(
        purpose="devstream-start-continuous",
        machine="JIGGLYPUFF",
        account="LEBOTJAMESXD00N",
        run_count=30,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=10,
    )

    assert lease["allowedPurposes"] == [
        "devstream-start-continuous",
        "devstream-start-continuous-dry-run",
        "devstream-supervise",
        "devstream-start-dry-run",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ]
