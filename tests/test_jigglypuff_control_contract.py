import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_REMOTE_SCRIPT = (
    "D:\\Releases\\fouler-play\\"
    + "a" * 40
    + "\\scripts\\fouler_jigglypuff_runtime.ps1"
)


def load_module():
    path = ROOT / "scripts" / "jigglypuff_devstream_control.py"
    spec = importlib.util.spec_from_file_location("jigglypuff_devstream_control", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_control_never_mirrors_jigglypuff_live_battle_state(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "TRUTH_DIR", tmp_path / "devstream" / "truth")
    monkeypatch.setattr(
        module,
        "fetch_live_state",
        lambda: {
            "battles": [{"id": "battle-gen9ou-1", "slot": 1}],
            "count": 1,
            "max_slots": 3,
            "updated": "2026-05-03T00:00:00Z",
        },
    )

    mirrored = module.mirror_status({"ok": True, "status": "running"}, action="status", raw={})

    assert not (tmp_path / "active_battles.json").exists()
    assert not (tmp_path / "devstream" / "truth" / "jigglypuff-runtime.json").exists()
    assert mirrored["mirrorSkipped"] is True
    assert mirrored["liveStateMirror"]["activeBattlesMirrored"] is False
    assert mirrored["liveStateMirror"]["skipped"] is True
    assert mirrored["liveStateMirror"]["observedAt"]
    assert "write_mirror was ignored" in " ".join(mirrored["warnings"])


def test_status_read_only_skips_mirror_writes(monkeypatch, tmp_path):
    module = load_module()
    captured = {}
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "TRUTH_DIR", tmp_path / "devstream" / "truth")
    def fake_remote_command(action, **kwargs):
        captured["action"] = action
        captured["kwargs"] = kwargs
        return {
            "ok": True,
            "returnCode": 0,
            "remoteStatusWriteSkipped": bool(kwargs.get("no_remote_write")),
            "json": {"ok": True, "status": "ready-idle"},
        }

    monkeypatch.setattr(module, "remote_command", fake_remote_command)
    monkeypatch.setattr(
        module,
        "mirror_live_state",
        lambda: (_ for _ in ()).throw(AssertionError("read-only status should not mirror live state")),
    )

    code, payload = module.action_status(SimpleNamespace(timeout=45, mirror=False))

    assert code == 0
    assert payload["status"] == "ready-idle"
    assert payload["mirrorSkipped"] is True
    assert payload["remoteStatusWriteSkipped"] is True
    assert payload["liveStateMirror"]["skipped"] is True
    assert captured["action"] == "status"
    assert captured["kwargs"]["no_remote_write"] is True
    assert not (tmp_path / "active_battles.json").exists()
    assert not (tmp_path / "devstream" / "truth" / "jigglypuff-runtime.json").exists()


def test_control_supports_direct_ip_env_overrides(monkeypatch):
    monkeypatch.setenv("FOULER_JIGGLYPUFF_SSH", "Ryanj@192.168.1.40")
    monkeypatch.setenv("FOULER_JIGGLYPUFF_OBS_HTTP", "http://192.168.1.40:8777/")
    monkeypatch.setenv("FOULER_JIGGLYPUFF_WORKER_HTTP", "http://192.168.1.40:8791/")

    module = load_module()

    assert module.REMOTE == "Ryanj@192.168.1.40"
    assert module.SSH_REMOTE_CANDIDATES == ["Ryanj@192.168.1.40"]
    assert module.OBS_HTTP == "http://192.168.1.40:8777"
    assert module.WORKER_HTTP == "http://192.168.1.40:8791"


def test_control_defaults_to_jigglypuff_direct_ip_with_tailnet_fallback(monkeypatch):
    monkeypatch.delenv("FOULER_JIGGLYPUFF_SSH", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_SSH_FALLBACKS", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_OBS_HTTP", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_WORKER_HTTP", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_OBS_HTTP_FALLBACKS", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_WORKER_HTTP_FALLBACKS", raising=False)

    module = load_module()

    assert module.OBS_HTTP == "http://192.168.1.126:8777"
    assert module.WORKER_HTTP == "http://192.168.1.126:8791"
    assert module.SSH_REMOTE_CANDIDATES == [
        "Ryanj@jigglypuff.tail4859dd.ts.net",
        "Ryanj@JIGGLYPUFF",
        "Ryanj@192.168.1.126",
    ]
    assert module.OBS_HTTP_CANDIDATES == [
        "http://192.168.1.126:8777",
        "http://jigglypuff.tail4859dd.ts.net:8777",
    ]
    assert module.WORKER_HTTP_CANDIDATES == [
        "http://192.168.1.126:8791",
        "http://jigglypuff.tail4859dd.ts.net:8791",
    ]


def test_public_runtime_fetch_falls_back_to_direct_ip_when_tailnet_primary_fails(monkeypatch):
    monkeypatch.setenv("FOULER_JIGGLYPUFF_OBS_HTTP", "http://jigglypuff.tail4859dd.ts.net:8777")
    module = load_module()
    calls = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"battles":[{"id":"battle-gen9ou-live"}],"count":1}'

    def fake_urlopen(url, timeout=4.0):
        calls.append(url)
        if "jigglypuff.tail4859dd.ts.net" in url:
            raise OSError("dns failed")
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    state = module.fetch_live_state()

    assert state["count"] == 1
    assert calls == [
        "http://jigglypuff.tail4859dd.ts.net:8777/state",
        "http://192.168.1.126:8777/state",
    ]


def test_resident_status_worker_timeout_is_capped_for_public_runtime_fallback(monkeypatch):
    module = load_module()
    captured = {}

    def fake_worker_request(path, **kwargs):
        captured["path"] = path
        captured["timeout"] = kwargs["timeout"]
        return {"ok": False, "json": None}

    monkeypatch.setattr(module, "worker_request", fake_worker_request)

    module.resident_command("status", timeout=45)

    assert captured == {
        "path": "/fouler/status",
        "timeout": module.STATUS_WORKER_TIMEOUT_SECONDS,
    }


def test_status_synthesizes_degraded_live_from_public_runtime_when_control_json_is_missing(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "TRUTH_DIR", tmp_path / "devstream" / "truth")
    monkeypatch.setattr(
        module,
        "fetch_live_state",
        lambda: {
            "battles": [
                {"id": "battle-gen9ou-1", "slot": 1},
                {"id": "battle-gen9ou-2", "slot": 2},
                {"id": "battle-gen9ou-3", "slot": 3},
            ],
            "count": 3,
            "max_slots": 3,
            "updated": "2026-05-30T06:00:00Z",
        },
    )
    monkeypatch.setattr(
        module,
        "fetch_live_health",
        lambda: {
            "status": "running",
            "healthy": True,
            "readiness": {"streamReady": True, "runtimeReady": True},
            "activeBattleCount": 3,
            "blockers": [],
        },
    )

    mirrored = module.mirror_status(
        None,
        action="status",
        raw={"returnCode": None, "stderr": "ssh closed", "stdout": ""},
    )

    assert mirrored["status"] == "degraded-live"
    assert mirrored["running"] is False
    assert mirrored["healthy"] is False
    assert mirrored["readiness"]["runtimeReady"] is False
    assert mirrored["activeBattleCount"] == 3
    assert "did not return JSON" not in " ".join(mirrored["blockers"])
    assert "exact-release control proof is unavailable" in mirrored["blockers"][0]
    assert mirrored["liveStateMirror"]["activeBattlesMirrored"] is False
    assert mirrored["liveStateMirror"]["skipped"] is True
    assert not (tmp_path / "active_battles.json").exists()
    assert not (tmp_path / "devstream" / "truth" / "jigglypuff-runtime.json").exists()


def test_remote_command_prefers_resident_worker_when_fouler_endpoint_exists(monkeypatch):
    module = load_module()

    monkeypatch.setattr(
        module,
        "resident_command",
        lambda action, **kwargs: {
            "ok": True,
            "returnCode": 0,
            "workerStatus": 200,
            "json": {"ok": True, "status": "ready-idle"},
        },
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ssh fallback should not run")),
    )

    result = module.remote_command("status")

    assert result["transport"] == "resident-worker-http"
    assert result["json"]["status"] == "ready-idle"


def test_remote_command_falls_back_to_ssh_when_resident_fouler_endpoint_is_missing(monkeypatch):
    monkeypatch.setenv("FOULER_JIGGLYPUFF_SCRIPT", IMMUTABLE_REMOTE_SCRIPT)
    module = load_module()
    captured = {}

    monkeypatch.setattr(
        module,
        "resident_command",
        lambda action, **kwargs: {
            "ok": False,
            "returnCode": None,
            "workerStatus": 404,
            "workerUrl": "http://jigglypuff.tail4859dd.ts.net:8791/fouler/status",
            "json": {"ok": False, "error": "unknown endpoint"},
            "stderr": "",
        },
    )

    def fake_run(command, *, timeout=60):
        captured["command"] = command
        return {"ok": True, "returnCode": 0, "stdout": '{"ok":true,"status":"ready-idle"}', "stderr": ""}

    monkeypatch.setattr(module, "run", fake_run)

    result = module.remote_command("status")

    assert captured["command"][0] == "ssh"
    assert result["json"]["status"] == "ready-idle"
    assert result["residentWorker"]["workerStatus"] == 404


def test_status_tries_lan_ssh_when_tailnet_remote_does_not_return_json(monkeypatch):
    monkeypatch.delenv("FOULER_JIGGLYPUFF_SSH", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_SSH_FALLBACKS", raising=False)
    monkeypatch.setenv("FOULER_JIGGLYPUFF_SCRIPT", IMMUTABLE_REMOTE_SCRIPT)
    module = load_module()
    calls = []

    monkeypatch.setattr(
        module,
        "resident_command",
        lambda action, **kwargs: {
            "ok": False,
            "returnCode": None,
            "workerStatus": 404,
            "workerUrl": "http://jigglypuff.tail4859dd.ts.net:8791/fouler/status",
            "json": {"ok": False, "error": "unknown endpoint"},
            "stderr": "",
        },
    )

    def fake_run(command, *, timeout=60):
        calls.append(command)
        remote = command[5]
        if remote == "Ryanj@jigglypuff.tail4859dd.ts.net":
            return {"ok": False, "returnCode": 255, "stdout": "", "stderr": "dns failed"}
        return {"ok": True, "returnCode": 0, "stdout": '{"ok":true,"status":"running"}', "stderr": ""}

    monkeypatch.setattr(module, "run", fake_run)

    result = module.remote_command("status")

    assert [call[5] for call in calls] == [
        "Ryanj@jigglypuff.tail4859dd.ts.net",
        "Ryanj@JIGGLYPUFF",
    ]
    assert result["remote"] == "Ryanj@JIGGLYPUFF"
    assert result["json"]["status"] == "running"
    assert len(result["sshAttempts"]) == 2


def test_read_only_remote_status_uses_ssh_no_write_and_skips_resident_worker(monkeypatch):
    monkeypatch.setenv("FOULER_JIGGLYPUFF_SCRIPT", IMMUTABLE_REMOTE_SCRIPT)
    module = load_module()
    captured = {}

    monkeypatch.setattr(
        module,
        "resident_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no-write status must not use the resident worker")),
    )

    def fake_run(command, *, timeout=60):
        captured["command"] = command
        return {
            "ok": True,
            "returnCode": 0,
            "stdout": '{"ok":true,"status":"ready-idle","proofArtifact":{"written":false}}',
            "stderr": "",
        }

    monkeypatch.setattr(module, "run", fake_run)

    result = module.remote_command("status", no_remote_write=True)

    assert captured["command"][0] == "ssh"
    assert "-NoWrite" in captured["command"]
    assert result["json"]["proofArtifact"]["written"] is False
    assert result["remoteStatusWriteSkipped"] is True
    assert result["residentWorker"]["attempted"] is False
    assert result["residentWorker"]["skippedNoWrite"] is True


def test_status_flags_battle_process_started_before_git_head():
    module = load_module()

    mirrored = module.mirror_status(
        {
            "ok": True,
            "healthy": True,
            "status": "running",
            "running": True,
            "git": {
                "head": "b7ab8ebc",
                "commitTime": "2026-06-14T17:15:19-04:00",
            },
            "processes": [
                {
                    "pid": 123,
                    "name": "python.exe",
                    "role": "battleSession",
                    "creationDate": "6/14/2026 6:39:27 AM",
                }
            ],
            "blockers": [],
        },
        action="status",
        raw={"remote": "Ryanj@JIGGLYPUFF", "remoteStatusWriteSkipped": True},
        write_mirror=False,
    )

    assert mirrored["ok"] is False
    assert mirrored["healthy"] is False
    assert mirrored["status"] == "blocked"
    assert mirrored["runtimeCodeFreshness"]["processStartPredatesGitHead"] is True
    assert mirrored["runtimeCodeFreshness"]["processStartPredatesRuntimeCode"] is True
    assert mirrored["runtimeCodeFreshness"]["staleProcessCount"] == 1
    assert "runtime-lease restart" in " ".join(mirrored["blockers"])


def test_status_flags_dotnet_json_process_timestamp_before_git_head():
    module = load_module()

    mirrored = module.mirror_status(
        {
            "ok": True,
            "healthy": True,
            "status": "running",
            "running": True,
            "git": {
                "head": "b7ab8ebc",
                "commitTime": "2026-06-14T17:15:19-04:00",
            },
            "processes": [
                {
                    "pid": 123,
                    "name": "python.exe",
                    "role": "battleSession",
                    "creationDate": "/Date(1781419167600)/",
                }
            ],
            "blockers": [],
        },
        action="status",
        raw={"remote": "Ryanj@JIGGLYPUFF", "remoteStatusWriteSkipped": True},
        write_mirror=False,
    )

    assert mirrored["runtimeCodeFreshness"]["processStartPredatesGitHead"] is True
    assert mirrored["runtimeCodeFreshness"]["staleProcesses"][0]["creationDate"] == "/Date(1781419167600)/"


def test_status_keeps_fresh_battle_process_running():
    module = load_module()

    mirrored = module.mirror_status(
        {
            "ok": True,
            "healthy": True,
            "status": "running",
            "running": True,
            "git": {
                "head": "b7ab8ebc",
                "commitTime": "2026-06-14T17:15:19-04:00",
            },
            "processes": [
                {
                    "pid": 123,
                    "name": "python.exe",
                    "role": "battleSession",
                    "creationDate": "2026-06-14T17:16:00-04:00",
                }
            ],
            "blockers": [],
        },
        action="status",
        raw={"remote": "Ryanj@JIGGLYPUFF", "remoteStatusWriteSkipped": True},
        write_mirror=False,
    )

    assert mirrored["ok"] is True
    assert mirrored["healthy"] is True
    assert mirrored["status"] == "running"
    assert mirrored["runtimeCodeFreshness"]["processStartPredatesGitHead"] is False
    assert mirrored["runtimeCodeFreshness"]["processStartPredatesRuntimeCode"] is False
    assert mirrored["runtimeCodeFreshness"]["staleProcessCount"] == 0


def test_status_compares_process_start_to_runtime_code_commit_not_repo_head():
    module = load_module()

    mirrored = module.mirror_status(
        {
            "ok": True,
            "healthy": True,
            "status": "running",
            "running": True,
            "git": {
                "head": "status999",
                "commitTime": "2026-06-14T18:00:00-04:00",
                "runtimeCodeHead": "runtime123",
                "runtimeCodeCommitTime": "2026-06-14T17:00:00-04:00",
            },
            "processes": [
                {
                    "pid": 123,
                    "name": "python.exe",
                    "role": "battleSession",
                    "creationDate": "2026-06-14T17:30:00-04:00",
                }
            ],
            "blockers": [],
        },
        action="status",
        raw={"remote": "Ryanj@JIGGLYPUFF", "remoteStatusWriteSkipped": True},
        write_mirror=False,
    )

    assert mirrored["ok"] is True
    assert mirrored["status"] == "running"
    assert mirrored["runtimeCodeFreshness"]["gitHead"] == "status999"
    assert mirrored["runtimeCodeFreshness"]["runtimeCodeHead"] == "runtime123"
    assert mirrored["runtimeCodeFreshness"]["runtimeCodeCommitTime"] == "2026-06-14T17:00:00-04:00"
    assert mirrored["runtimeCodeFreshness"]["processStartPredatesGitHead"] is False
    assert mirrored["runtimeCodeFreshness"]["processStartPredatesRuntimeCode"] is False


def test_start_runtime_lease_guard_refuses_local_host_validation():
    module = load_module()

    guard = module.start_runtime_lease_guard(
        SimpleNamespace(
            run_count=1,
            max_concurrent_battles=1,
            max_cycles=1,
            runtime_lease="runtime-lease.json",
        )
    )

    assert guard["purpose"] == "jigglypuff-runtime-start"
    assert guard["ok"] is False
    assert guard["status"] == "retired-control-path"
    assert "exact release on JIGGLYPUFF" in " ".join(guard["blockers"])


def test_stop_runtime_lease_guard_refuses_local_host_validation():
    module = load_module()

    guard = module.runtime_lease_guard_for_action(
        "stop",
        SimpleNamespace(runtime_lease="lease.json"),
    )

    assert guard["purpose"] == module.JIGGLYPUFF_RUNTIME_STOP_PURPOSE
    assert guard["ok"] is False
    assert guard["status"] == "retired-control-path"


def test_stop_execute_is_always_blocked_on_retired_remote_path(monkeypatch):
    module = load_module()
    monkeypatch.setattr(
        module,
        "remote_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stop must not execute without lease")),
    )

    code, payload = module.action_mutating(
        "stop",
        SimpleNamespace(execute=True, runtime_lease=None, timeout=120),
    )

    assert code == 2
    assert payload["status"] == "retired-control-path"
    assert payload["blocked"] is True
    assert payload["executeRequested"] is True
    assert "remote mutation is retired" in " ".join(payload["blockers"])


def test_start_execute_fails_closed_on_retired_remote_path(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setattr(
        module,
        "remote_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("remote command must not run")),
    )

    code, payload = module.action_mutating(
        "start",
        SimpleNamespace(
            execute=True,
            run_count=1,
            max_concurrent_battles=1,
            max_cycles=1,
            runtime_lease=str(tmp_path / "missing-runtime-lease.json"),
            obs_only=False,
            enable_auto_improve=False,
            timeout=180,
        ),
    )

    assert code == 2
    assert payload["status"] == "retired-control-path"
    assert payload["blocked"] is True
    assert payload["executeRequested"] is True


def test_start_execute_does_not_inspect_or_trust_local_runtime_lease(monkeypatch, tmp_path):
    module = load_module()
    lease_path = tmp_path / "runtime-lease.json"
    lease_path.write_text('{"approved":true}', encoding="utf-8")
    monkeypatch.setattr(
        module,
        "remote_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("remote command must not run")),
    )

    code, payload = module.action_mutating(
        "start",
        SimpleNamespace(
            execute=True,
            run_count=1,
            max_concurrent_battles=1,
            max_cycles=1,
            runtime_lease=str(lease_path),
            obs_only=False,
            enable_auto_improve=False,
            timeout=180,
        ),
    )

    assert code == 2
    assert payload["status"] == "retired-control-path"
    assert payload["runtimeLease"]["path"] == str(lease_path)
    assert "remote mutation is retired" in " ".join(payload["blockers"])


def test_start_execute_never_calls_remote_command_even_with_bounded_inputs(monkeypatch, tmp_path):
    module = load_module()
    lease_path = tmp_path / "runtime-lease.json"
    monkeypatch.setattr(
        module,
        "remote_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("retired mutation path must not invoke remote command")
        ),
    )

    code, payload = module.action_mutating(
        "start",
        SimpleNamespace(
            execute=True,
            run_count=2,
            max_concurrent_battles=3,
            max_cycles=1,
            runtime_lease=str(lease_path),
            obs_only=False,
            enable_auto_improve=False,
            timeout=180,
        ),
    )

    assert code == 2
    assert payload["status"] == "retired-control-path"
    assert payload["bounds"]["maxCycles"] == 1
    assert payload["bounds"]["runCount"] == 2
    assert payload["bounds"]["maxConcurrentBattles"] == 3


def test_gitignore_excludes_generated_runtime_lease_artifacts():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "devstream/truth/stale-runtime-artifact-backups/" in ignored
    assert "devstream/truth/runtime-lease*.json" in ignored
