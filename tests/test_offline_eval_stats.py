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

runner_spec = importlib.util.spec_from_file_location(
    "offline_eval_runner", ROOT / "infrastructure" / "offline_eval_runner.py"
)
offline_eval_runner = importlib.util.module_from_spec(runner_spec)
runner_spec.loader.exec_module(offline_eval_runner)


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
    assert env["FOULER_OFFLINE_EVAL_LABEL"] == "frozen"
    assert env["FOULER_PROCESS_LOCK_FILE"].replace("/", "\\").endswith(
        "eval_results\\offline\\frozen.bot.pid"
    )
    assert env["FOULER_BATTLE_RESULT_QUEUE"] == "0"
    assert env["FOULER_OFFLINE_EVAL_QUEUE_EVENTS"] == "0"
    assert env["DISCORD_BATTLES_WEBHOOK_URL"] == ""
    assert env["EVENT_QUEUE_FILE"].endswith("frozen-events_queue.json")
    assert env["FOULER_STREAM_EVENTS"] == "0"
    assert env["STREAM_EVENT_URL"] == ""
    assert env["FOULER_OFFLINE_BATTLE_STATS_FILE"].replace("/", "\\").endswith(
        "eval_results\\offline\\frozen-battle_stats.json"
    )
    assert env["FOULER_OFFLINE_ACTIVE_BATTLES_FILE"].replace("/", "\\").endswith(
        "eval_results\\offline\\frozen-active_battles.json"
    )
    assert env["FOULER_OFFLINE_STREAM_STATUS_FILE"].replace("/", "\\").endswith(
        "eval_results\\offline\\frozen-stream_status.json"
    )


def test_build_eval_env_allows_explicit_queue_override():
    env = offline_eval.build_eval_env(
        label="candidate",
        showdown_port=8765,
        search_time_ms=250,
        extra_env={"FOULER_BATTLE_RESULT_QUEUE": "1"},
    )

    assert env["FOULER_BATTLE_RESULT_QUEUE"] == "1"


def test_build_fouler_command_uses_offline_runner_with_run_py_sentinel():
    cmd = offline_eval.build_fouler_command(
        fouler_python=["python"],
        ws_uri="ws://localhost:8765/showdown/websocket",
        fouler_user="foulerEvalBot",
        fmt="gen9ou",
        team="gen9/ou/fat-team-1-stall",
        battles=40,
        search_time_ms=250,
    )

    assert cmd[:3] == ["python", str(offline_eval.OFFLINE_RUNNER_SCRIPT), "run.py"]
    assert "--bot-mode" in cmd
    assert "accept_challenge" in cmd
    assert "--run-count" in cmd
    assert cmd[cmd.index("--run-count") + 1] == "45"


def test_offline_eval_runner_redirects_stats_file_away_from_live_battle_stats(tmp_path):
    fake_run = SimpleNamespace(BATTLE_STATS_FILE=tmp_path / "battle_stats.json")

    stats_path, run_argv = offline_eval_runner.configure_run_module(
        fake_run,
        ["run.py", "--bot-mode", "accept_challenge"],
        root=tmp_path,
        env={"FOULER_OFFLINE_BATTLE_STATS_FILE": "eval_results/offline/frozen-battle_stats.json"},
    )

    assert stats_path == tmp_path / "eval_results" / "offline" / "frozen-battle_stats.json"
    assert fake_run.BATTLE_STATS_FILE == stats_path
    assert stats_path != tmp_path / "battle_stats.json"
    assert stats_path.parent.exists()
    assert run_argv == ["run.py", "--bot-mode", "accept_challenge"]


def test_offline_eval_runner_redirects_state_store_files(tmp_path):
    state_store = SimpleNamespace(
        ACTIVE_BATTLES_PATH=None,
        STREAM_STATUS_PATH=None,
        DAILY_STATS_PATH=None,
        STABILITY_REPORT_PATH=None,
        STATE_STORE_WRITE_FAILURE_PATH=None,
    )

    paths = offline_eval_runner.configure_state_store_module(
        state_store,
        root=tmp_path,
        env={"FOULER_OFFLINE_EVAL_LABEL": "candidate"},
    )

    offline_dir = tmp_path / "eval_results" / "offline"
    assert state_store.ACTIVE_BATTLES_PATH == offline_dir / "candidate-active_battles.json"
    assert state_store.STREAM_STATUS_PATH == offline_dir / "candidate-stream_status.json"
    assert state_store.DAILY_STATS_PATH == offline_dir / "candidate-daily_stats.json"
    assert state_store.STABILITY_REPORT_PATH == offline_dir / "candidate-stability_report.json"
    assert state_store.STATE_STORE_WRITE_FAILURE_PATH == offline_dir / "candidate-state-store-write-failure.json"
    assert paths["activeBattles"].parent.exists()


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


def test_require_showdown_server_fails_closed_when_unreachable(monkeypatch):
    monkeypatch.setattr(offline_eval, "showdown_server_reachable", lambda port: False)

    with pytest.raises(RuntimeError, match="not reachable"):
        offline_eval.require_showdown_server(8765)


def test_require_completed_battle_count_rejects_underfilled_eval():
    with pytest.raises(RuntimeError, match="2/3 battles"):
        offline_eval.require_completed_battle_count(requested=3, actual=2, label="smoke")


def test_managed_showdown_refuses_to_adopt_unknown_existing_listener(monkeypatch):
    monkeypatch.delenv("EVAL_SHOWDOWN_ADOPT_EXISTING", raising=False)
    monkeypatch.setattr(offline_eval, "showdown_server_reachable", lambda port: True)

    with pytest.raises(RuntimeError, match="refused to adopt existing listener"):
        offline_eval.start_managed_showdown_server(8765)


def test_managed_showdown_can_explicitly_adopt_existing_listener(monkeypatch):
    monkeypatch.setenv("EVAL_SHOWDOWN_ADOPT_EXISTING", "1")
    monkeypatch.setattr(offline_eval, "showdown_server_reachable", lambda port: True)

    assert offline_eval.start_managed_showdown_server(8765) is None
