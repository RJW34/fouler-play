"""Unit tests for the offline-eval acceptance statistics (the real gate's math)."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "offline_eval", ROOT / "infrastructure" / "offline_eval.py"
)
offline_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline_eval)


def test_wilson_lower_bound_basic():
    # 100% over a tiny sample is NOT confidently > 0.5
    assert offline_eval.wilson_lower_bound(2, 2) < 0.5
    # 100% over a large sample IS confidently > 0.5
    assert offline_eval.wilson_lower_bound(200, 200) > 0.5
    # coin flip centers below 0.5 on the lower bound
    assert offline_eval.wilson_lower_bound(100, 200) < 0.5
    # empty sample -> 0
    assert offline_eval.wilson_lower_bound(0, 0) == 0.0


def test_wilson_monotonic_in_n():
    # Same win-rate, larger n -> tighter (higher) lower bound
    lo_small = offline_eval.wilson_lower_bound(15, 20)
    lo_big = offline_eval.wilson_lower_bound(150, 200)
    assert lo_big > lo_small


def test_two_proportion_z_significant():
    # 80% vs 50% over 200 each should be significant (p < 0.05)
    z, p = offline_eval.two_proportion_z(160, 200, 100, 200)
    assert z > 0
    assert p < 0.05


def test_two_proportion_z_not_significant():
    # 52% vs 50% over 40 each: not significant
    z, p = offline_eval.two_proportion_z(21, 40, 20, 40)
    assert p > 0.05


def test_two_proportion_z_empty():
    z, p = offline_eval.two_proportion_z(0, 0, 0, 0)
    assert p == 1.0


def test_resolve_fouler_python_skips_eval_venv_without_runtime_imports(monkeypatch):
    monkeypatch.delenv("FOULER_RUNTIME_PYTHON", raising=False)
    monkeypatch.setattr(
        offline_eval,
        "_runtime_python_candidates",
        lambda: [["eval-python"], ["runtime-python"]],
    )

    def fake_probe(command):
        if command == ["runtime-python"]:
            return True, {"command": "runtime-python"}
        return False, {"command": "eval-python", "stderr": "No module named aiohttp"}

    monkeypatch.setattr(offline_eval, "_probe_fouler_python", fake_probe)

    assert offline_eval.resolve_fouler_python() == ["runtime-python"]


def test_build_eval_env_disables_discord_battle_result_queue(monkeypatch):
    monkeypatch.setenv("FOULER_BATTLE_RESULT_QUEUE", "1")

    env = offline_eval.build_eval_env(
        label="frozen",
        showdown_port=8765,
        search_time_ms=250,
        extra_env=None,
    )

    assert env["FOULER_OFFLINE_EVAL"] == "1"
    assert env["FOULER_BATTLE_RESULT_QUEUE"] == "0"
    assert env["FOULER_OFFLINE_EVAL_QUEUE_EVENTS"] == "0"
    assert env["DISCORD_BATTLES_WEBHOOK_URL"] == ""
    assert env["EVENT_QUEUE_FILE"].endswith("frozen-events_queue.json")


def test_build_eval_env_allows_explicit_queue_override():
    env = offline_eval.build_eval_env(
        label="candidate",
        showdown_port=8765,
        search_time_ms=250,
        extra_env={"FOULER_BATTLE_RESULT_QUEUE": "1"},
    )

    assert env["FOULER_BATTLE_RESULT_QUEUE"] == "1"


def test_process_owner_payload_records_git_and_child_processes(monkeypatch):
    monkeypatch.setattr(
        offline_eval,
        "current_git_metadata",
        lambda: {
            "head": "e4315275d6e12b126ecf0fadd433fbad5134b0b0",
            "shortHead": "e4315275",
            "dirty": False,
        },
    )
    fake_proc = SimpleNamespace(pid=1234, poll=lambda: None, args=["py", "-3", "run.py"])

    payload = offline_eval._build_process_owner_payload(
        label="frozen",
        stage="fouler-started",
        command=["python", "infrastructure/offline_eval.py", "--label", "frozen"],
        fouler_proc=fake_proc,
        fouler_cmd=["py", "-3", "run.py"],
    )

    assert payload["schemaVersion"] == "fouler-play-offline-eval-process-owner/v1"
    assert payload["git"]["shortHead"] == "e4315275"
    assert payload["processes"]["offlineEval"]["command"].endswith("--label frozen")
    assert payload["processes"]["fouler"]["pid"] == 1234
    assert payload["processes"]["fouler"]["running"] is True
