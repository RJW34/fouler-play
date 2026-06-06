import importlib.util
import json
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
