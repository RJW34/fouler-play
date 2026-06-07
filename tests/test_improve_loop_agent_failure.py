import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "improve_loop", ROOT / "infrastructure" / "improve_loop.py"
)
improve_loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(improve_loop)


def test_classify_outcome_distinguishes_agent_subprocess_failure():
    result = {
        "gate_skipped": False,
        "committed": False,
        "accepted": False,
        "verdict_line": "",
        "agent_returncode": 1,
    }

    assert improve_loop._classify_outcome(result) == "agent_failed"


def test_loop_status_surfaces_agent_failed_reason(tmp_path, monkeypatch):
    ledger = tmp_path / "improve_ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "issue": "Improve move choice",
                "outcome": "agent_failed",
                "agent_returncode": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(improve_loop, "LEDGER_PATH", ledger)
    monkeypatch.setattr(improve_loop, "BATTLE_STATS_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(
        improve_loop,
        "_ladder_snapshot",
        lambda: {"current_elo": 1200, "peak_elo": 1230, "target": 1700, "recent_slope_per_game": -1.0, "progress_fraction_1000_to_target": 0.28},
    )

    status = improve_loop.loop_status()

    assert status["last_outcome"] == "agent_failed"
    assert status["last_agent_returncode"] == 1
    assert "agent-failed" in status["headline"]


def test_improve_loop_auto_improve_requires_cli_flag_or_env_sentinel(monkeypatch):
    monkeypatch.delenv(improve_loop.AUTO_IMPROVE_SENTINEL, raising=False)

    assert not improve_loop.auto_improve_enabled(False)
    assert improve_loop.auto_improve_enabled(True)

    monkeypatch.setenv(improve_loop.AUTO_IMPROVE_SENTINEL, "yes")

    assert improve_loop.auto_improve_enabled(False)


def test_offline_no_live_readiness_reports_sentinel_and_measured_gate(monkeypatch):
    status = {
        "headline": "learn-loop idle",
        "measured_gate_ever": False,
        "battle_stream_stale": False,
        "battle_stream_age_minutes": None,
    }
    monkeypatch.delenv(improve_loop.AUTO_IMPROVE_SENTINEL, raising=False)

    blocked = improve_loop.offline_no_live_readiness(status)

    assert blocked["readyForOfflineIteration"] is False
    assert blocked["readyForRecursiveAutoImprove"] is False
    assert improve_loop.AUTO_IMPROVE_SENTINEL in blocked["blockers"][0]
    assert "public ladder" in blocked["exclusions"]

    monkeypatch.setenv(improve_loop.AUTO_IMPROVE_SENTINEL, "1")

    gated = improve_loop.offline_no_live_readiness(status)

    assert gated["readyForOfflineIteration"] is True
    assert gated["readyForRecursiveAutoImprove"] is False
    assert gated["measuredGateEver"] is False

    status["measured_gate_ever"] = True

    ready = improve_loop.offline_no_live_readiness(status)

    assert ready["readyForRecursiveAutoImprove"] is True


def test_main_blocks_recursive_iterations_without_measured_gate_readiness(monkeypatch):
    monkeypatch.setenv(improve_loop.AUTO_IMPROVE_SENTINEL, "1")
    monkeypatch.setattr(sys, "argv", ["improve_loop.py", "--iterations", "2"])
    monkeypatch.setattr(
        improve_loop,
        "loop_status",
        lambda: {
            "measured_gate_ever": False,
            "battle_stream_stale": False,
            "battle_stream_age_minutes": None,
            "headline": "learn-loop awaiting first measured gate",
        },
    )
    monkeypatch.setattr(
        improve_loop,
        "acquire_runtime_lease",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("lease should not be acquired")),
    )

    assert improve_loop.main() == 2


def test_main_allows_recursive_iterations_when_readiness_is_measured(monkeypatch):
    calls = []

    class FakeLease:
        def release(self):
            calls.append("released")

    monkeypatch.setenv(improve_loop.AUTO_IMPROVE_SENTINEL, "1")
    monkeypatch.setattr(sys, "argv", ["improve_loop.py", "--iterations", "2"])
    monkeypatch.setattr(improve_loop, "current_branch", lambda: "fix/test")
    monkeypatch.setattr(improve_loop, "acquire_runtime_lease", lambda **_kwargs: FakeLease())
    monkeypatch.setattr(
        improve_loop,
        "loop_status",
        lambda: {
            "measured_gate_ever": True,
            "battle_stream_stale": False,
            "battle_stream_age_minutes": None,
            "headline": "learn-loop measured",
        },
    )
    monkeypatch.setattr(improve_loop, "one_iteration", lambda **_kwargs: calls.append("iteration"))

    assert improve_loop.main() == 0
    assert calls == ["iteration", "iteration", "released"]


def test_main_blocks_nonpositive_iteration_count(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["improve_loop.py", "--iterations", "0", "--dry-run"])
    monkeypatch.setattr(
        improve_loop,
        "acquire_runtime_lease",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("lease should not be acquired")),
    )

    assert improve_loop.main() == 2


def test_main_blocks_mutating_loop_before_runtime_lease_without_sentinel(monkeypatch):
    monkeypatch.delenv(improve_loop.AUTO_IMPROVE_SENTINEL, raising=False)
    monkeypatch.setattr(sys, "argv", ["improve_loop.py", "--iterations", "1"])
    monkeypatch.setattr(
        improve_loop,
        "acquire_runtime_lease",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("lease should not be acquired")),
    )

    assert improve_loop.main() == 2


def test_main_releases_runtime_lease_after_iteration(monkeypatch, tmp_path):
    from infrastructure import runtime_lease

    lease_path = tmp_path / "fouler-runtime-lane.lease.json"
    monkeypatch.delenv(improve_loop.AUTO_IMPROVE_SENTINEL, raising=False)
    monkeypatch.delenv(runtime_lease.LEASE_TOKEN_ENV, raising=False)
    monkeypatch.delenv(runtime_lease.LEASE_NAME_ENV, raising=False)
    monkeypatch.setattr(improve_loop, "current_branch", lambda: "fix/test")
    monkeypatch.setattr(
        improve_loop,
        "acquire_runtime_lease",
        lambda **kwargs: runtime_lease.acquire_runtime_lease(**kwargs, lease_dir=tmp_path),
    )
    monkeypatch.setattr(improve_loop, "one_iteration", lambda **_kwargs: {"outcome": "dry_run"})
    monkeypatch.setattr(sys, "argv", ["improve_loop.py", "--dry-run", "--iterations", "1"])

    assert improve_loop.main() == 0
    assert not lease_path.exists()
    assert runtime_lease.LEASE_TOKEN_ENV not in os.environ
