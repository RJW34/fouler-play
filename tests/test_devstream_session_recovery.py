import json
import argparse
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import devstream_session


def test_recover_stale_battle_runtime_replaces_idle_singleton(tmp_path, monkeypatch):
    pid_dir = tmp_path / ".pids"
    bot_pid = tmp_path / ".bot.pid"
    session_pid = pid_dir / "devstream_battle_session.pid"
    calls = []

    monkeypatch.setattr(devstream_session, "PID_DIR", pid_dir)
    monkeypatch.setattr(devstream_session, "DRAIN_FILE", pid_dir / "drain.request")
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])
    monkeypatch.setattr(
        devstream_session,
        "pid_alive",
        lambda path: (True, 1234) if path == bot_pid else (False, 36084),
    )
    monkeypatch.setattr(devstream_session, "process_age_seconds", lambda pid: 181.0)

    def fake_terminate(path, *, force=False):
        calls.append((path, force))
        return {"pidFile": str(path), "pid": 1234 if path == bot_pid else 36084, "wasRunning": path == bot_pid}

    monkeypatch.setattr(devstream_session, "terminate_pid_file", fake_terminate)
    monkeypatch.setattr(devstream_session, "read_pid", lambda path: None)

    payload = devstream_session.recover_stale_battle_runtime(execute=True, stale_after_seconds=180)

    assert payload["recovered"] is True
    assert payload["activeBattleCount"] == 0
    assert calls == [(bot_pid, True), (session_pid, True)]
    assert (pid_dir / "drain.request").exists()


def test_doctor_accepts_completed_proof_handoff_without_runtime_ready(monkeypatch):
    health = {
        "healthy": False,
        "readiness": {
            "runtimeReady": False,
            "proofHandoffReady": True,
        },
        "blockers": ["fouler-play battle runner is idle; OBS HTTP alone is not active battle proof"],
    }
    monkeypatch.setattr(devstream_session, "run_json", lambda command: (health, None))
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(devstream_session, "shell_command_for_session", lambda *args, **kwargs: ["python", "run.py"])
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "supervisor_alive", lambda: (True, 4321))

    payload = devstream_session.build_doctor()
    health_check = next(check for check in payload["checks"] if check["name"] == "health_probe")

    assert payload["ready"] is True
    assert health_check["ok"] is True
    assert health_check["acceptedMode"] == "proof-handoff"
    assert "readiness gate" in health_check["runtimeRestoration"]


def test_existing_battle_runner_start_result_reuses_any_live_runner(tmp_path, monkeypatch):
    bot_pid = tmp_path / ".bot.pid"
    session_pid = tmp_path / ".pids" / "devstream_battle_session.pid"
    command = ["python", "run.py", "--bot-mode", "search_ladder"]

    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", session_pid)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])
    monkeypatch.setattr(
        devstream_session,
        "pid_alive",
        lambda path: (True, 29852) if path == bot_pid else (False, 45896),
    )

    payload = devstream_session.existing_battle_runner_start_result(command)

    assert payload is not None
    assert payload["alreadyRunning"] is True
    assert payload["pid"] == 29852
    assert payload["pidFile"] == str(bot_pid)
    assert payload["knownRunners"] == [{"pidFile": str(bot_pid), "pid": 29852}]
    assert payload["adoptedPidFile"] == {
        "pidFile": str(session_pid),
        "pid": 29852,
        "adoptedFrom": str(bot_pid),
    }
    assert payload["command"] == command
    parsed_session_pid = json.loads(session_pid.read_text(encoding="utf-8"))
    assert parsed_session_pid["pid"] == 29852
    assert parsed_session_pid["adoptedExistingProcess"] is True


def test_terminate_battle_runners_covers_all_known_pid_files(tmp_path, monkeypatch):
    bot_pid = tmp_path / ".bot.pid"
    session_pid = tmp_path / ".pids" / "devstream_battle_session.pid"
    calls = []

    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])

    def fake_terminate(path, *, force=False):
        calls.append((path, force))
        return {"pidFile": str(path), "wasRunning": path == bot_pid}

    monkeypatch.setattr(devstream_session, "terminate_pid_file", fake_terminate)

    payload = devstream_session.terminate_battle_runners(force=True)

    assert calls == [(bot_pid, True), (session_pid, True)]
    assert payload == {
        ".bot.pid": {"pidFile": str(bot_pid), "wasRunning": True},
        "devstream_battle_session.pid": {"pidFile": str(session_pid), "wasRunning": False},
    }


def test_start_process_adopts_existing_matching_process_without_spawning(tmp_path, monkeypatch):
    pid_file = tmp_path / ".pids" / "devstream_obs_http.pid"
    command = ["python", "streaming/serve_obs_page.py"]

    monkeypatch.setattr(devstream_session, "PID_DIR", pid_file.parent)
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (False, 32900))
    monkeypatch.setattr(devstream_session, "_find_existing_process", lambda cmd: 42208)
    monkeypatch.setattr(devstream_session, "obs_http_ready", lambda: True)

    def fail_spawn(*args, **kwargs):
        raise AssertionError("should not spawn when matching process already exists")

    monkeypatch.setattr(devstream_session.subprocess, "Popen", fail_spawn)

    payload = devstream_session.start_process(command, pid_file, {})

    assert payload["alreadyRunning"] is True
    assert payload["pid"] == 42208
    assert payload["previousPid"] == 32900
    assert payload["adoptedExistingProcess"] is True
    parsed = json.loads(pid_file.read_text(encoding="utf-8"))
    assert parsed["pid"] == 42208
    assert parsed["previousPid"] == 32900


def test_start_process_refuses_stale_obs_http_adoption(tmp_path, monkeypatch):
    pid_file = tmp_path / ".pids" / "devstream_obs_http.pid"
    command = ["python", "streaming/serve_obs_page.py"]
    spawned = {}
    terminated = []

    monkeypatch.setattr(devstream_session, "PID_DIR", tmp_path / ".pids")
    monkeypatch.setattr(devstream_session, "OBS_PID_FILE", pid_file)
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (False, 32900))
    monkeypatch.setattr(devstream_session, "_find_existing_process", lambda cmd: 42208)
    monkeypatch.setattr(devstream_session, "obs_http_ready", lambda: False)
    monkeypatch.setattr(
        devstream_session,
        "terminate_process_pid",
        lambda pid, **kwargs: terminated.append((pid, kwargs)) or {"pid": pid, "wasRunning": True, "sent": "SIGTERM"},
    )

    class FakeProc:
        pid = 50001

    def fake_spawn(*args, **kwargs):
        spawned["args"] = args
        spawned["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(devstream_session.subprocess, "Popen", fake_spawn)

    payload = devstream_session.start_process(command, pid_file, {})

    assert payload["pid"] == 50001
    assert payload.get("adoptedExistingProcess") is None
    assert payload["staleExistingProcess"]["pid"] == 42208
    assert terminated == [
        (
            42208,
            {
                "force": True,
                "reason": "OBS HTTP process matched command but /health was unavailable before restart",
            },
        )
    ]
    assert spawned


def test_recover_stale_battle_runtime_never_interrupts_active_battles(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 1)

    payload = devstream_session.recover_stale_battle_runtime(execute=True, stale_after_seconds=180)

    assert payload["recovered"] is False
    assert payload["reason"] == "active battles are present; not replacing runner"


def test_drain_command_writes_request_without_terminating_active_battle(tmp_path, monkeypatch, capsys):
    pid_dir = tmp_path / ".pids"
    drain_file = pid_dir / "drain.request"
    args = argparse.Namespace(execute=True, reason="deploy refreshed legal-option trace proof")

    monkeypatch.setattr(devstream_session, "PID_DIR", pid_dir)
    monkeypatch.setattr(devstream_session, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 1)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)

    assert devstream_session.cmd_drain(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "fouler-play-devstream-drain-plan/v1"
    assert payload["activeBattleCount"] == 1
    assert payload["battleRunnerAlive"] is True
    assert payload["written"] is True
    assert "deploy refreshed legal-option trace proof" in drain_file.read_text(encoding="utf-8")


def test_env_loader_strips_unquoted_inline_comments(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LOSS_TRIGGERED_DRAIN=0  # disable early-stop for devstream runs",
                "FOO='quoted # value'",
                'BAR="also # quoted"',
                "URL=https://example.test/path#anchor",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session, "ENV_FILES", [env_file])
    monkeypatch.delenv("LOSS_TRIGGERED_DRAIN", raising=False)
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    monkeypatch.delenv("URL", raising=False)

    env = devstream_session.load_env_files()

    assert env["LOSS_TRIGGERED_DRAIN"] == "0"
    assert env["FOO"] == "quoted # value"
    assert env["BAR"] == "also # quoted"
    assert env["URL"] == "https://example.test/path#anchor"


def test_clear_stale_active_battles_backs_up_and_resets_dead_runner_truth(tmp_path, monkeypatch):
    active = tmp_path / "active_battles.json"
    active.write_text(
        json.dumps({"battles": [{"id": "battle-gen9ou-1"}], "count": 1, "max_slots": 3}),
        encoding="utf-8",
    )
    old = time.time() - 300
    os.utime(active, (old, old))
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STALE_BATTLE_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)

    payload = devstream_session.clear_stale_active_battles(execute=True, stale_after_seconds=180)
    parsed = json.loads(active.read_text(encoding="utf-8"))

    assert payload["cleared"] is True
    assert payload["activeBattleCount"] == 1
    assert parsed["battles"] == []
    assert parsed["count"] == 0
    assert parsed["clearReason"] == "stale active battle truth had no live battle runner"
    assert Path(payload["backupPath"]).exists()


def test_clear_stale_active_battles_preserves_live_runner_truth(tmp_path, monkeypatch):
    active = tmp_path / "active_battles.json"
    active.write_text(json.dumps({"battles": [{"id": "battle-gen9ou-1"}], "count": 1}), encoding="utf-8")
    old = time.time() - 300
    os.utime(active, (old, old))

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)

    payload = devstream_session.clear_stale_active_battles(execute=True, stale_after_seconds=180)

    assert payload["cleared"] is False
    assert payload["reason"] == "battle runner is alive; preserving active battle truth"
    assert json.loads(active.read_text(encoding="utf-8"))["count"] == 1


def test_forced_clear_active_battles_overrides_live_runner_truth(tmp_path, monkeypatch):
    active = tmp_path / "active_battles.json"
    active.write_text(json.dumps({"battles": [{"id": "battle-gen9ou-1"}], "count": 1}), encoding="utf-8")
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STALE_BATTLE_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)

    payload = devstream_session.clear_stale_active_battles(
        execute=True,
        stale_after_seconds=0,
        force=True,
        clear_reason="forced devstream stop terminated the battle runner; stale active battle truth must not stay public",
    )
    parsed = json.loads(active.read_text(encoding="utf-8"))

    assert payload["cleared"] is True
    assert payload["reason"] == "active battle truth cleared after forced stop"
    assert parsed["battles"] == []
    assert parsed["clearReason"] == "forced devstream stop terminated the battle runner; stale active battle truth must not stay public"


def test_pid_alive_rejects_reused_pid_with_wrong_command(tmp_path, monkeypatch):
    pid_file = tmp_path / "devstream_battle_session.pid"
    pid_file.write_text(
        json.dumps({"pid": 1234, "command": ["python", "run.py"], "startedAt": devstream_session.iso_now()}),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(
        devstream_session,
        "_process_snapshot",
        lambda pid: {
            "running": True,
            "cmdline": ["python", "not_the_bot.py"],
            "cwd": str(devstream_session.ROOT),
            "createTime": time.time(),
        },
    )

    alive, pid = devstream_session.pid_alive(pid_file)

    assert pid == 1234
    assert alive is False


def test_pid_alive_rejects_process_created_before_pid_file_start(tmp_path, monkeypatch):
    pid_file = tmp_path / "devstream_battle_session.pid"
    pid_file.write_text(
        json.dumps({"pid": 1234, "command": ["python", "run.py"], "startedAt": devstream_session.iso_now()}),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(
        devstream_session,
        "_process_snapshot",
        lambda pid: {
            "running": True,
            "cmdline": ["python", "run.py", "--bot-mode", "search_ladder"],
            "cwd": str(devstream_session.ROOT),
            "createTime": time.time() - 60,
        },
    )

    alive, pid = devstream_session.pid_alive(pid_file)

    assert pid == 1234
    assert alive is False


def test_pid_alive_accepts_matching_repo_process(tmp_path, monkeypatch):
    pid_file = tmp_path / "devstream_battle_session.pid"
    started = time.time() - 5
    pid_file.write_text(
        json.dumps({"pid": 1234, "command": ["python", "run.py"], "started_at": started}),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(
        devstream_session,
        "_process_snapshot",
        lambda pid: {
            "running": True,
            "cmdline": ["python", "run.py", "--bot-mode", "search_ladder"],
            "cwd": str(devstream_session.ROOT),
            "createTime": started + 1,
        },
    )

    alive, pid = devstream_session.pid_alive(pid_file)

    assert pid == 1234
    assert alive is True


def test_pid_alive_uses_process_snapshot_when_signal_zero_fails(tmp_path, monkeypatch):
    pid_file = tmp_path / ".bot.pid"
    pid_file.write_text("29852", encoding="utf-8")

    monkeypatch.setattr(devstream_session, "BOT_LOCK_PID_FILE", pid_file)
    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError("signal 0 unsupported")))
    monkeypatch.setattr(
        devstream_session,
        "_process_snapshot",
        lambda pid: {
            "running": True,
            "cmdline": ["python", "run.py", "--bot-mode", "search_ladder"],
            "cwd": str(devstream_session.ROOT),
            "createTime": time.time(),
        },
    )

    alive, pid = devstream_session.pid_alive(pid_file)

    assert pid == 29852
    assert alive is True


def test_continuous_start_spawns_supervisor_not_direct_battle_runner(monkeypatch, capsys, tmp_path):
    calls = []
    supervisor_calls = []

    monkeypatch.setattr(devstream_session, "PID_DIR", tmp_path / ".pids")
    monkeypatch.setattr(devstream_session, "SUPERVISOR_STOP_FILE", tmp_path / ".pids" / "supervisor.stop")
    monkeypatch.setattr(devstream_session, "OBS_PID_FILE", tmp_path / ".pids" / "obs.pid")
    monkeypatch.setattr(devstream_session, "SUPERVISOR_PID_FILE", tmp_path / ".pids" / "supervisor.pid")
    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", tmp_path / ".pids" / "battle.pid")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(devstream_session, "run_json", lambda command: ({"healthy": True}, None))
    monkeypatch.setattr(devstream_session.time, "sleep", lambda seconds: None)

    def fake_start(command, pid_file, env):
        calls.append((command, pid_file))
        return {"pid": 100 + len(calls), "pidFile": str(pid_file), "command": command}

    monkeypatch.setattr(devstream_session, "start_process", fake_start)
    monkeypatch.setattr(
        devstream_session,
        "start_supervisor_runtime",
        lambda args, command, env: supervisor_calls.append(command) or {"ok": True, "taskStatus": {"taskPresent": True}},
    )

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        max_runtime_minutes=180,
        queue_timeout_seconds=180,
        turn_timeout_seconds=90,
        supervisor_sleep_seconds=15,
        replace_stale_runner=True,
        continuous=True,
        execute=True,
    )

    assert devstream_session.cmd_start(args) == 0

    assert supervisor_calls
    assert not any(pid_file == devstream_session.BATTLE_PID_FILE for _, pid_file in calls)
    payload = json.loads(capsys.readouterr().out)
    assert payload["started"]["battleSession"]["reason"] == "persistent supervisor owns bounded battle session starts"


def test_supervisor_cycle_refreshes_proof_then_starts_when_idle(monkeypatch):
    commands = []

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")

    def fake_run(command, *, timeout):
        commands.append(command)
        return {"command": command, "returnCode": 0}

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
    )

    payload = devstream_session.run_supervisor_cycle(args, 1)

    assert payload["state"] == "idle-restoring-runtime"
    assert commands[0][:4] == ["python", "pipeline.py", "autoresearch", "-n"]
    assert commands[1] == ["python", "scripts/devstream_cycle_report.py", "--write"]
    assert commands[2][:3] == ["python", "scripts/devstream_session.py", "start"]
    assert "--continuous" not in commands[2]


def test_supervisor_cycle_clears_stale_active_truth_when_runner_is_dead(monkeypatch):
    commands = []
    counts = [1, 0, 0]

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: counts.pop(0) if counts else 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")

    def fake_clear(**kwargs):
        return {
            "cleared": True,
            "execute": kwargs["execute"],
            "staleAfterSeconds": kwargs["stale_after_seconds"],
            "clearReason": kwargs["clear_reason"],
        }

    def fake_run(command, *, timeout):
        commands.append(command)
        return {"command": command, "returnCode": 0}

    monkeypatch.setattr(devstream_session, "clear_stale_active_battles", fake_clear)
    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
    )

    payload = devstream_session.run_supervisor_cycle(args, 2)

    assert payload["state"] == "idle-restoring-runtime"
    assert payload["staleActiveBattleClear"]["cleared"] is True
    assert payload["staleActiveBattleClear"]["staleAfterSeconds"] == 180
    assert payload["activeBattleCountAfterClear"] == 0
    assert commands[2][:3] == ["python", "scripts/devstream_session.py", "start"]
    assert commands[2][commands[2].index("--max-concurrent-battles") + 1] == "3"


def test_supervisor_cycle_waits_when_battle_runner_alive(monkeypatch):
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)

    payload = devstream_session.run_supervisor_cycle(argparse.Namespace(), 7)

    assert payload["state"] == "battle-cycle-in-flight"
    assert payload["cycleIndex"] == 7
    assert payload["actions"] == []


def test_supervisor_process_identity_requires_supervise_subcommand():
    tokens = devstream_session._command_expected_tokens(
        ["python", "scripts/devstream_session.py", "supervise", "--run-count", "25"]
    )

    assert "devstream_session.py" in tokens
    assert "supervise" in tokens
