import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import devstream_health


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _runner(age: int = 30) -> list[dict]:
    return [{
        "pidFile": ".bot.pid",
        "pid": 1234,
        "alive": True,
        "isBattleRunner": True,
        "ageSeconds": age,
    }]


def test_runtime_processes_does_not_mark_reused_pid_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    pid_file = tmp_path / ".bot.pid"
    pid_file.write_text(
        json.dumps({
            "pid": 1234,
            "command": ["python", "run.py", "--bot-mode", "search_ladder"],
            "startedAt": devstream_health.iso_now(),
        }),
        encoding="utf-8",
    )

    class ReusedPidProcess:
        def cmdline(self):
            return ["python", "not-fouler-runtime.py"]

        def cwd(self):
            return str(tmp_path)

        def create_time(self):
            return time.time()

        def ppid(self):
            return 0

        def status(self):
            return "running"

        def is_running(self):
            return True

    fake_psutil = SimpleNamespace(
        Process=lambda pid: ReusedPidProcess(),
        NoSuchProcess=Exception,
        AccessDenied=Exception,
        STATUS_ZOMBIE="zombie",
    )
    monkeypatch.setattr(devstream_health, "psutil", fake_psutil)

    process = next(
        proc
        for proc in devstream_health.runtime_processes()
        if proc["pidFile"] == ".bot.pid"
    )

    assert process["processRunning"] is True
    assert process["alive"] is False
    assert process["isBattleRunner"] is False
    assert process["stalePidReason"] == "pid belongs to unexpected command, cwd, or older process"


def test_missing_psutil_blocks_runtime_and_marks_pid_files_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "psutil", None)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "obs_surface_task_status", lambda: {"available": False, "reason": "not-windows"})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})

    (tmp_path / ".bot.pid").write_text("42024", encoding="utf-8")
    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})

    payload = devstream_health.build_payload(check_http=False)

    assert payload["readiness"]["runtimeReady"] is False
    assert payload["runtimeOwnership"]["processInspectionReady"] is False
    assert any("psutil is missing" in blocker for blocker in payload["blockers"])
    assert payload["runtimeProcesses"][0]["processInspectionAvailable"] is False
    assert payload["runtimeProcesses"][0]["stalePidReason"] == "psutil is not installed; PID ownership cannot be verified"


def test_optional_stale_stability_report_does_not_gate_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: _runner(age=30))

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 0, "losses": 0})
    stale = tmp_path / "stability_report.json"
    _write_json(stale, {"generated_at": "old"})
    old = time.time() - 90000
    os.utime(stale, (old, old))

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is True
    assert payload["readiness"]["runtimeReady"] is True
    assert payload["readiness"]["analyticsFresh"] is True
    assert payload["readiness"]["proofHandoffReady"] is True
    assert "stale truth file: stability_report.json" in payload["warnings"]
    assert not payload["blockers"]


def test_obs_http_without_battle_runner_is_not_runtime_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is False
    assert payload["running"] is True
    assert payload["readiness"]["runtimeReady"] is False
    assert payload["blockers"] == ["fouler-play battle runner is idle; OBS HTTP alone is not active battle proof"]


def test_health_payload_exposes_offline_eval_useful_work_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "obs_surface_task_status", lambda: {"available": False, "reason": "not-windows"})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])
    monkeypatch.setattr(
        devstream_health,
        "offline_eval_readiness_snapshot",
        lambda: {
            "available": True,
            "ready": True,
            "recursiveImprovementReady": True,
            "blockers": [],
            "failedChecks": [],
            "commands": {"readiness": "python infrastructure/offline_eval_readiness.py --require-ready"},
            "note": "fixture",
        },
    )

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})

    payload = devstream_health.build_payload(check_http=False)

    assert payload["readiness"]["runtimeReady"] is False
    assert payload["readiness"]["offlineEvalReady"] is True
    assert payload["readiness"]["usefulWorkProofReady"] is True
    assert payload["usefulWorkProof"]["status"] == "offline-eval-ready"
    assert payload["usefulWorkProof"]["ready"] is True
    assert payload["offlineEvalReadiness"]["commands"]["readiness"].endswith("--require-ready")


def test_health_payload_exposes_offline_eval_result_proof_without_live_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "obs_surface_task_status", lambda: {"available": False, "reason": "not-windows"})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])
    monkeypatch.setattr(
        devstream_health,
        "offline_eval_readiness_snapshot",
        lambda: {
            "available": True,
            "ready": False,
            "recursiveImprovementReady": False,
            "blockers": ["frozen baseline proof: run the frozen baseline command"],
            "failedChecks": [{"name": "frozen baseline proof", "ok": False}],
            "commands": {"readiness": "python infrastructure/offline_eval_readiness.py --require-ready"},
            "resultProof": {
                "ready": True,
                "smokePassed": True,
                "accepted": False,
                "promotionEligible": False,
                "status": "smoke-passed",
                "verdict": "passed",
                "candidateBattles": 200,
                "requiredBattles": 200,
                "missingPaths": [],
                "reasons": ["weak-baseline transport and gross-regression smoke passed"],
            },
            "note": "fixture",
        },
    )

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})

    payload = devstream_health.build_payload(check_http=False)

    result_proof = payload["usefulWorkProof"]["offlineEvalResultProof"]
    assert payload["readiness"]["runtimeReady"] is False
    assert payload["readiness"]["usefulWorkProofReady"] is True
    assert payload["usefulWorkProof"]["status"] == "offline-eval-smoke-passed"
    assert payload["usefulWorkProof"]["offlineEvalResultProofReady"] is True
    assert result_proof["status"] == "smoke-passed"
    assert result_proof["candidateBattles"] == 200


def test_health_payload_blocks_useful_work_when_runtime_and_offline_proof_are_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "obs_surface_task_status", lambda: {"available": False, "reason": "not-windows"})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])
    monkeypatch.setattr(
        devstream_health,
        "offline_eval_readiness_snapshot",
        lambda: {
            "available": True,
            "ready": False,
            "recursiveImprovementReady": False,
            "blockers": ["frozen baseline proof: run the frozen baseline command"],
            "failedChecks": [{"name": "frozen baseline proof", "ok": False}],
            "commands": {},
            "note": "fixture",
        },
    )

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})

    payload = devstream_health.build_payload(check_http=False)

    assert payload["readiness"]["usefulWorkProofReady"] is False
    assert payload["usefulWorkProof"]["status"] == "blocked"
    assert "live battle runtime is not ready" in payload["usefulWorkProof"]["blockers"]
    assert any("frozen baseline proof" in item for item in payload["usefulWorkProof"]["blockers"])


def test_windows_obs_surface_task_status_classifies_stderr(tmp_path, monkeypatch):
    script = tmp_path / "scripts" / "install_obs_server_service.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("# task", encoding="utf-8")
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health.os, "name", "nt")
    commands = []

    def fake_run_command(command, timeout=4):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "taskPresent": True,
                    "taskState": "Running",
                    "processCount": 1,
                    "port8777Listening": True,
                    "stderrTail": [
                        "[OBS-WS] GetInputList failed: Authentication failed.",
                        "Existing Fouler OBS server process found; refusing duplicate start",
                    ],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(devstream_health, "run_command", fake_run_command)

    status = devstream_health.obs_surface_task_status(check_http=False)

    assert status["available"] is True
    assert status["port8777Listening"] is True
    assert status["stderrTailClass"]["classes"] == ["duplicate_guard_false_positive", "obs_ws_auth_failed"]
    assert commands[0][-1] == "-SkipHttpProbe"


def test_windows_health_children_do_not_share_the_service_console(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(devstream_health.subprocess, "run", fake_run)
    monkeypatch.setattr(devstream_health, "_child_creation_flags", lambda: 0x08000000)

    result = devstream_health.run_command(["probe.exe"], timeout=7)

    assert result.returncode == 0
    assert calls[0][1]["stdin"] is devstream_health.subprocess.DEVNULL
    assert calls[0][1]["creationflags"] == 0x08000000
    assert calls[0][1]["timeout"] == 7


def test_git_status_avoids_optional_locks_and_untracked_scan(monkeypatch):
    calls = []

    def fake_run_command(command, *, timeout=4):
        calls.append((command, timeout))
        stdout = "abc123\n" if "rev-parse" in command else " M tracked.py\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(devstream_health, "run_command", fake_run_command)

    status = devstream_health.git_status()

    assert calls == [
        (["git", "rev-parse", "--short", "HEAD"], 3),
        (["git", "--no-optional-locks", "status", "--short", "--untracked-files=no"], 3),
    ]
    assert status == {"commit": "abc123", "dirty": True, "dirtyScope": "tracked"}


def test_http_handler_witness_skips_recursive_obs_health_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    probed_ports = []
    task_probe_modes = []

    def fake_port_open(port, host="127.0.0.1"):
        probed_ports.append(port)
        return False

    def fake_obs_surface_task_status(*, check_http=True):
        task_probe_modes.append(check_http)
        return {
            "available": True,
            "servicePresent": True,
            "serviceState": "Running",
            "managedPid": 1234,
            "processCount": 1,
            "port8777Listening": False,
            "healthEndpointOk": None,
            "healthProbeSkipped": True,
            "lifecycleHealthy": False,
            "stderrTailClass": {"classes": ["clean"], "summary": "clean"},
        }

    monkeypatch.setattr(devstream_health, "port_open", fake_port_open)
    monkeypatch.setattr(devstream_health, "obs_surface_task_status", fake_obs_surface_task_status)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})

    payload = devstream_health.build_payload(check_http=False, http_handler_witness=True)

    assert task_probe_modes == [False]
    assert devstream_health.HTTP_PORT not in probed_ports
    assert payload["ports"]["obsHttp"]["open"] is True
    assert payload["ports"]["obsHttp"]["rawSocketOpen"] is True
    assert payload["obsSurface"]["port8777Listening"] is True
    assert payload["obsSurface"]["healthEndpointOk"] is True
    assert payload["obsSurface"]["lifecycleHealthy"] is True
    assert payload["obsSurface"]["healthWitness"] == "current-http-handler"


def test_obs_task_port_reports_surface_running_without_claiming_battle_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])
    monkeypatch.setattr(
        devstream_health,
        "obs_surface_task_status",
        lambda: {
            "available": True,
            "taskPresent": True,
            "taskState": "Running",
            "processCount": 1,
            "port8777Listening": True,
            "stderrTailClass": {"classes": ["clean"], "summary": "clean"},
        },
    )

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["running"] is True
    assert payload["readiness"]["streamReady"] is True
    assert payload["readiness"]["runtimeReady"] is False
    assert payload["ports"]["obsHttp"]["open"] is True
    assert payload["ports"]["obsHttp"]["rawSocketOpen"] is False
    assert payload["ports"]["obsHttp"]["taskReportsListening"] is True
    assert payload["obsSurface"]["taskState"] == "Running"
    assert payload["blockers"] == ["fouler-play battle runner is idle; OBS HTTP alone is not active battle proof"]


def test_duplicate_battle_runners_block_runtime_until_hermes_drain_or_adopt(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(
        devstream_health,
        "runtime_processes",
        lambda: [
            {"pidFile": ".bot.pid", "pid": 1111, "alive": True, "isBattleRunner": True, "ageSeconds": 30},
            {
                "pidFile": ".pids/devstream_battle_session.pid",
                "pid": 2222,
                "alive": True,
                "isBattleRunner": True,
                "ageSeconds": 20,
            },
        ],
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {
                "/slot/1/state": {"battle_id": "battle-gen9ou-1"},
                "/slot/2/state": {"battle_id": "battle-gen9ou-2"},
                "/slot/3/state": {"battle_id": "battle-gen9ou-3"},
            }.get(path, {}),
        },
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_showdown_battle_title",
        lambda battle_id: {"url": devstream_health.showdown_battle_url(battle_id), "ok": True, "title": "A vs. B - Showdown!"},
    )

    _write_json(
        tmp_path / "active_battles.json",
        {
            "battles": [
                {"id": "battle-gen9ou-1", "slot": 1},
                {"id": "battle-gen9ou-2", "slot": 2},
                {"id": "battle-gen9ou-3", "slot": 3},
            ],
            "count": 3,
        },
    )
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-30", "wins": 0, "losses": 0})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is False
    assert payload["status"] == "blocked"
    assert payload["activeBattleCount"] == 3
    assert payload["readiness"]["runtimeReady"] is False
    assert payload["runtimeOwnership"]["duplicateBattleRunners"] is True
    assert payload["runtimeOwnership"]["battleRunnerCount"] == 2
    assert "drain/adopt" in payload["runtimeOwnership"]["requiredHermesAction"]
    assert any("duplicate fouler-play battle runners" in blocker for blocker in payload["blockers"])
    assert "drain/adopt" in payload["devstreamReporting"]["nextHermesAction"]


def test_unreadable_discord_queue_blocks_reporting_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: _runner(age=30))

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})
    _write_json(
        tmp_path / "events_queue.json",
        {
            "not": "a list"
        },
    )

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is True
    assert payload["readiness"]["runtimeReady"] is True
    assert payload["readiness"]["discordReportingReady"] is False
    assert payload["readiness"]["proofHandoffReady"] is False
    assert payload["discordQueue"]["status"] == "unreadable"
    assert payload["devstreamReporting"]["backlogClassification"]["status"] == "unreadable"
    assert payload["devstreamReporting"]["proofReadiness"]["status"] == "queue-unreadable"
    assert "repair events_queue.json" in payload["devstreamReporting"]["nextHermesAction"]
    assert not payload["blockers"]
    assert any("Discord event queue could not be read" in blocker for blocker in payload["proofBlockers"])


def test_discord_backlog_does_not_make_live_runtime_unhealthy(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {"battle_id": "battle-gen9ou-1"} if path == "/slot/1/state" else {},
        },
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_showdown_battle_title",
        lambda battle_id: {"url": devstream_health.showdown_battle_url(battle_id), "ok": False, "title": "Showdown!"},
    )
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: _runner(age=30))

    _write_json(
        tmp_path / "active_battles.json",
        {"battles": [{"id": "battle-gen9ou-1", "slot": 1, "opponent": "Opponent"}], "count": 1},
    )
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 1, "losses": 0})
    _write_json(
        tmp_path / "events_queue.json",
        [
            {
                "id": "pending-1",
                "timestamp": time.time() - 1200,
                "event_type": "battle_result",
                "status": "pending",
                "content": "battle finished loss vs Example in 31 turns battle-gen9ou-123",
            }
        ],
    )

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is True
    assert payload["status"] == "running"
    assert payload["readiness"]["runtimeReady"] is True
    assert payload["readiness"]["discordReportingReady"] is True
    assert payload["readiness"]["proofHandoffReady"] is False
    assert payload["blockers"] == []
    assert payload["proofBlockers"] == [
        "active battle cycle has not drained to analysis yet (1 active battle)"
    ]
    assert payload["proofWarnings"] == [
        "Discord delivery remains pending, but queued battle reports are classified as redacted local proof for HERMES rehearsal handoff."
    ]


def test_classified_local_discord_backlog_is_rehearsal_proof_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {"battle_id": "battle-gen9ou-1"} if path == "/slot/1/state" else {},
        },
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_showdown_battle_title",
        lambda battle_id: {"url": devstream_health.showdown_battle_url(battle_id), "ok": False, "title": "Showdown!"},
    )
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: _runner(age=30))

    _write_json(
        tmp_path / "active_battles.json",
        {"battles": [{"id": "battle-gen9ou-1", "slot": 1, "opponent": "Opponent"}], "count": 1},
    )
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 1, "losses": 0})
    _write_json(
        tmp_path / "events_queue.json",
        [
            {
                "id": "pending-1",
                "timestamp": time.time() - 1200,
                "event_type": "battle_result",
                "status": "pending",
                "content": "battle finished loss vs Example in 31 turns battle-gen9ou-123",
                "battle_id": "battle-gen9ou-123",
                "winner": "Example",
                "loser": "fouler-play",
                "turns": 31,
                "proof": {"battleIds": ["gen9ou-123"], "items": ["battle `123`"]},
                "analysis": {
                    "currentBattleState": "battle loss; vs Example; 31 turns; id 123",
                    "whyItMatters": "loss proof should be visible locally",
                    "nextHermesAction": "review the replay",
                },
                "current_battle_state": "battle loss; vs Example; 31 turns; id 123",
                "why_it_matters": "loss proof should be visible locally",
                "next_hermes_action": "review the replay",
                "proof_readiness": {"status": "proof-ready"},
            }
        ],
    )

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is True
    assert payload["readiness"]["runtimeReady"] is True
    assert payload["readiness"]["discordReportingReady"] is True
    assert payload["readiness"]["proofHandoffReady"] is False
    assert payload["proofBlockers"] == [
        "active battle cycle has not drained to analysis yet (1 active battle)"
    ]
    assert payload["proofWarnings"] == [
        "Discord delivery remains pending, but queued battle reports are classified as redacted local proof for HERMES rehearsal handoff."
    ]
    assert payload["discordQueue"]["proofReadiness"]["readyForLocalProofHandoff"] is True


def test_completed_cycle_proof_allows_handoff_while_runtime_start_is_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 1, "losses": 0})
    _write_json(tmp_path / "battle_stats.json", {"battles": []})
    _write_json(
        tmp_path / "events_queue.json",
        [
            {
                "id": "pending-1",
                "timestamp": time.time() - 1200,
                "event_type": "battle_result",
                "status": "pending",
                "battle_id": "battle-gen9ou-123",
                "winner": "Example",
                "loser": "fouler-play",
                "proof": {"battleIds": ["gen9ou-123"], "items": ["battle `123`"]},
                "analysis": {
                    "currentBattleState": "battle loss; vs Example; 31 turns; id 123",
                    "whyItMatters": "loss proof should be visible locally",
                    "nextHermesAction": "review the replay",
                },
                "current_battle_state": "battle loss; vs Example; 31 turns; id 123",
                "why_it_matters": "loss proof should be visible locally",
                "next_hermes_action": "review the replay",
                "proof_readiness": {"status": "proof-ready"},
            }
        ],
    )
    _write_json(
        tmp_path / "devstream" / "truth" / "proof-status.json",
        {
            "generatedAt": devstream_health.iso_now(),
            "status": "local-discord-proof-classified",
            "readyForProofHandoff": True,
            "secretValuesPrinted": False,
            "blockers": [],
            "activeBattleTelemetry": {"battleCount": 0},
            "completedCycleProof": {
                "isCurrent": True,
                "latestBattleId": "battle-gen9ou-123",
                "performanceImprovementVerified": True,
                "performanceTrendStatus": "improving",
            },
        },
    )

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is False
    assert payload["readiness"]["runtimeReady"] is False
    assert payload["readiness"]["proofHandoffReady"] is True
    assert payload["completedCycleProof"]["readyForProofHandoff"] is True
    assert payload["proofBlockers"] == []
    assert payload["blockers"] == ["fouler-play battle runner is idle; OBS HTTP alone is not active battle proof"]


def test_completed_cycle_proof_without_improvement_signal_is_not_handoff_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0, "max_slots": 3})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})
    _write_json(
        tmp_path / "devstream" / "truth" / "proof-status.json",
        {
            "generatedAt": devstream_health.iso_now(),
            "status": "proof-ready",
            "readyForProofHandoff": True,
            "secretValuesPrinted": False,
            "blockers": [],
            "activeBattleTelemetry": {"battleCount": 0},
            "completedCycleProof": {
                "isCurrent": True,
                "latestBattleId": "battle-gen9ou-123",
                "performanceTrendStatus": "flat",
            },
        },
    )

    payload = devstream_health.build_payload(check_http=True)

    assert payload["completedCycleProof"]["readyForProofHandoff"] is False
    assert payload["completedCycleProof"]["improvementSignalOk"] is False
    assert payload["readiness"]["proofHandoffReady"] is False


def test_devstream_health_requires_three_public_battle_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "RUNTIME_LEASE_PATH", tmp_path / "devstream" / "truth" / "runtime-lease.json")
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: _runner(age=30))

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0, "max_slots": 2})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["battleSurfaceReadiness"]["expected"] == 3
    assert payload["battleSurfaceReadiness"]["declaredMaxSlots"] == 2
    assert payload["readiness"]["streamReady"] is False
    assert payload["readiness"]["runtimeReady"] is False
    assert any("expects 3 concurrent battle surfaces" in blocker for blocker in payload["blockers"])


def test_expected_battle_surfaces_follow_devstream_session_default():
    assert devstream_health.EXPECTED_DEVSTREAM_BATTLE_SURFACES == devstream_health.DEFAULT_DEVSTREAM_BATTLE_SURFACES
    assert "/slot/3/state" in devstream_health.ENDPOINTS


def test_discord_queue_health_exposes_pending_and_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    _write_json(
        tmp_path / "events_queue.json",
        [
            {"id": "p1", "timestamp": time.time() - 50, "event_type": "battle_result", "status": "pending"},
            {"id": "f1", "timestamp": time.time() - 60, "event_type": "battle_result", "status": "failed", "last_error": "webhook_http_error"},
        ],
    )

    health = devstream_health.discord_queue_health()

    assert health["status"] == "delivery-failed"
    assert health["ready"] is False
    assert health["pendingBacklog"] == 1
    assert health["pendingBattleResults"] == 1
    assert health["deliveryFailures"] == 1
    assert health["webhookFailures"] == 1
    assert health["backlogClassification"]["status"] == "delivery-failed"
    assert health["proofReadiness"]["status"] == "delivery-failed"
    assert "repair webhook" in health["nextHermesAction"]


def test_idle_battle_runner_without_active_battle_proof_blocks_after_queue_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(
        devstream_health,
        "runtime_processes",
        lambda: _runner(age=devstream_health.IDLE_RUNNER_STALE_SECONDS + 1),
    )

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    stream = tmp_path / "stream_status.json"
    _write_json(stream, {"status": "Searching", "runtime_blocked": False})
    old = time.time() - 25000
    os.utime(stream, (old, old))

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is False
    assert payload["readiness"]["runtimeReady"] is False
    assert any("no active battle proof" in blocker for blocker in payload["blockers"])


def test_long_running_search_with_fresh_truth_is_runtime_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(
        devstream_health,
        "runtime_processes",
        lambda: _runner(age=devstream_health.IDLE_RUNNER_STALE_SECONDS + 1),
    )

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 1, "losses": 0})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is True
    assert payload["readiness"]["runtimeReady"] is True
    assert not payload["blockers"]


def test_stale_empty_active_battles_truth_without_runner_blocks_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "fetch_endpoint", lambda path: {"url": path, "ok": True, "statusCode": 200, "json": {}})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])

    active = tmp_path / "active_battles.json"
    _write_json(active, {"battles": [], "count": 0})
    _write_json(tmp_path / "stream_status.json", {"status": "Searching", "runtime_blocked": False})
    old = time.time() - 90000
    os.utime(active, (old, old))

    payload = devstream_health.build_payload(check_http=True)

    active_truth = next(item for item in payload["truth"] if item["relativePath"] == "active_battles.json")
    assert active_truth["stale"] is True
    assert active_truth["freshnessNote"] == "empty active battle truth is valid only while fresh runtime ownership exists"
    assert payload["healthy"] is False
    assert payload["readiness"]["runtimeReady"] is False
    assert any("active_battles.json is stale and no battle runner is alive" in blocker for blocker in payload["blockers"])
    assert payload["runtimeTruthDisposition"]["artifacts"]["activeBattles"]["state"] == "blocked"
    assert "stale truth file: active_battles.json" in payload["warnings"]


def test_archived_stale_active_battles_truth_is_not_live_runtime_blocker(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "obs_surface_task_status", lambda: {"available": False, "reason": "not-windows"})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])

    active = tmp_path / "active_battles.json"
    stream = tmp_path / "stream_status.json"
    _write_json(
        active,
        {
            "battles": [],
            "count": 0,
            "max_slots": 3,
            "clearedBy": "HERMES devstream_session start",
            "clearReason": "stale empty active battle truth had no live battle runner",
        },
    )
    _write_json(stream, {"status": "Searching", "runtime_blocked": False})
    old = time.time() - 90000
    os.utime(active, (old, old))
    os.utime(stream, (old, old))

    payload = devstream_health.build_payload(check_http=False)

    active_truth = next(item for item in payload["truth"] if item["relativePath"] == "active_battles.json")
    assert active_truth["stale"] is True
    assert active_truth["blocksAnalyticsFresh"] is False
    assert active_truth["disposition"]["state"] == "archived"
    assert payload["runtimeTruthDisposition"]["artifacts"]["activeBattles"]["state"] == "archived"
    assert payload["runtimeTruthDisposition"]["artifacts"]["streamStatus"]["state"] == "blocked"
    assert any("stream_status.json is stale" in blocker for blocker in payload["blockers"])
    assert not any("active_battles.json is stale" in blocker for blocker in payload["blockers"])
    assert "finite proof-window runtime lease" in payload["blockers"][0]


def test_autoresearch_json_freshness_uses_generated_at_not_touched_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})

    _write_json(tmp_path / "active_battles.json", {"battles": [], "count": 0})
    source = tmp_path / "replay_analysis" / "autoresearch_latest.json"
    _write_json(source, {"generated_at": "2026-05-20T00:00:00+00:00"})

    payload = devstream_health.build_payload(check_http=True)

    autoresearch = next(item for item in payload["truth"] if item["relativePath"] == "replay_analysis/autoresearch_latest.json")
    assert autoresearch["freshnessSource"] == "generated_at"
    assert autoresearch["stale"] is True
    assert "stale truth file: replay_analysis/autoresearch_latest.json" in payload["warnings"]


def test_active_slot_readiness_uses_local_state_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: _runner(age=30))
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {"battle_id": "battle-gen9ou-1"} if path == "/slot/1/state" else {},
        },
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_showdown_battle_title",
        lambda battle_id: {
            "url": devstream_health.showdown_battle_url(battle_id),
            "ok": False,
            "statusCode": 200,
            "title": "Showdown!",
        },
    )

    _write_json(
        tmp_path / "active_battles.json",
        {"battles": [{"id": "battle-gen9ou-1", "slot": 1, "opponent": "Opponent"}], "count": 1},
    )
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 0, "losses": 0})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is True
    assert payload["readiness"]["streamReady"] is True
    assert payload["slotReadiness"]["checks"][0]["localStateOk"] is True
    assert payload["slotReadiness"]["checks"][0]["showdownPage"]["title"] == "Showdown!"
    assert not payload["blockers"]


def test_stale_active_battle_truth_without_runner_blocks_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [])
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {"battle_id": "battle-gen9ou-1"} if path == "/slot/1/state" else {},
        },
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_showdown_battle_title",
        lambda battle_id: {"url": devstream_health.showdown_battle_url(battle_id), "ok": False, "title": "Showdown!"},
    )

    active = tmp_path / "active_battles.json"
    _write_json(active, {"battles": [{"id": "battle-gen9ou-1", "slot": 1}], "count": 1})
    old = time.time() - 3600
    os.utime(active, (old, old))
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 0, "losses": 0})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is False
    assert payload["readiness"]["runtimeReady"] is False
    assert any("active battle truth is stale and no battle runner is alive" in blocker for blocker in payload["blockers"])


def test_terminal_active_battle_is_classified_as_ghost_not_live_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(devstream_health, "runtime_processes", lambda: [{"alive": True, "pid": 8124, "isBattleRunner": True, "ageSeconds": 1}])
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {"battle_id": None} if path == "/slot/1/state" else {},
        },
    )

    _write_json(
        tmp_path / "active_battles.json",
        {"battles": [{"id": "battle-gen9ou-2618904557", "slot": 1}], "count": 1},
    )
    _write_json(
        tmp_path / "battle_stats.json",
        {"battles": [{"battle_id": "battle-gen9ou-2618904557", "result": "loss"}]},
    )
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 0, "losses": 1})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["activeBattleCount"] == 0
    assert payload["rawActiveBattleCount"] == 1
    assert payload["ghostActiveBattles"]["battleIds"] == ["battle-gen9ou-2618904557"]
    assert any("not counting ghost battle telemetry as live proof" in warning for warning in payload["warnings"])
    assert payload["readiness"]["runtimeReady"] is True


def test_active_slot_blocks_when_local_state_does_not_match_active_battle(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_health, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_health, "port_open", lambda port, host="127.0.0.1": port == devstream_health.HTTP_PORT)
    monkeypatch.setattr(devstream_health, "systemctl_state", lambda unit: {"activeState": "unknown", "enabledState": "unknown", "active": False})
    monkeypatch.setattr(devstream_health, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_health, "git_status", lambda: {"commit": "test", "dirty": False})
    monkeypatch.setattr(
        devstream_health,
        "fetch_endpoint",
        lambda path: {
            "url": path,
            "ok": True,
            "statusCode": 200,
            "json": {"battle_id": None} if path == "/slot/1/state" else {},
        },
    )
    monkeypatch.setattr(
        devstream_health,
        "fetch_showdown_battle_title",
        lambda battle_id: {"url": devstream_health.showdown_battle_url(battle_id), "ok": False, "title": "Showdown!"},
    )

    _write_json(
        tmp_path / "active_battles.json",
        {"battles": [{"id": "battle-gen9ou-1", "slot": 1, "opponent": "Opponent"}], "count": 1},
    )
    _write_json(tmp_path / "stream_status.json", {"status": "Active", "runtime_blocked": False})
    _write_json(tmp_path / "daily_stats.json", {"date": "2026-05-20", "wins": 0, "losses": 0})

    payload = devstream_health.build_payload(check_http=True)

    assert payload["healthy"] is False
    assert payload["readiness"]["streamReady"] is False
    assert any("slot 1 is not battle-ready" in blocker for blocker in payload["blockers"])


