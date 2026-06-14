import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "jigglypuff_devstream_control.py"
    spec = importlib.util.spec_from_file_location("jigglypuff_devstream_control", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_runtime_lease(
    module,
    path: Path,
    *,
    account: str = "bot",
    allowed_purposes: list[str] | None = None,
) -> Path:
    payload = {
        "schemaVersion": "fouler-play-runtime-lease/v1",
        "projectId": "fouler-play",
        "leaseId": "lease-test",
        "status": "active",
        "machine": "JIGGLYPUFF",
        "account": account,
        "allowedPurposes": allowed_purposes or [module.JIGGLYPUFF_RUNTIME_START_PURPOSE],
        "maxRunCount": 10,
        "maxCycles": 2,
        "maxConcurrentBattles": 3,
        "replayBehavior": "save",
        "proofWindow": {
            "startsAt": "2026-06-08T00:00:00+00:00",
            "expiresAt": "2099-01-01T00:00:00+00:00",
        },
    }
    path.write_text(module.json.dumps(payload), encoding="utf-8")
    return path


def test_control_mirrors_jigglypuff_live_battle_state(monkeypatch, tmp_path):
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

    active = module.json.loads((tmp_path / "active_battles.json").read_text(encoding="utf-8"))
    assert active["count"] == 1
    assert active["battles"][0]["id"] == "battle-gen9ou-1"
    assert active["observedAt"]
    assert mirrored["liveStateMirror"]["activeBattlesMirrored"] is True
    assert mirrored["liveStateMirror"]["observedAt"]


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

    active = module.json.loads((tmp_path / "active_battles.json").read_text(encoding="utf-8"))
    assert mirrored["status"] == "degraded-live"
    assert mirrored["running"] is True
    assert mirrored["healthy"] is False
    assert mirrored["readiness"]["runtimeReady"] is False
    assert mirrored["activeBattleCount"] == 3
    assert "did not return JSON" not in " ".join(mirrored["blockers"])
    assert "drain/adopt" in mirrored["blockers"][0]
    assert mirrored["liveStateMirror"]["battleCount"] == 3
    assert active["count"] == 3


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


def test_start_runtime_lease_guard_uses_jiggly_runtime_purpose_and_account(monkeypatch, tmp_path):
    module = load_module()
    captured = {}

    monkeypatch.setattr(module, "load_env_files", lambda: {"PS_USERNAME": "claudechamp"})

    def fake_validate_runtime_lease(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "purpose": kwargs["purpose"], "requested": {"account": kwargs["requested_account"]}}

    monkeypatch.setattr(module, "validate_runtime_lease", fake_validate_runtime_lease)

    guard = module.start_runtime_lease_guard(
        SimpleNamespace(
            run_count=1,
            max_concurrent_battles=1,
            max_cycles=1,
            runtime_lease=str(tmp_path / "runtime-lease.json"),
        )
    )

    assert guard["purpose"] == "jigglypuff-runtime-start"
    assert captured["purpose"] == module.JIGGLYPUFF_RUNTIME_START_PURPOSE
    assert captured["requested_account"] == "claudechamp"
    assert captured["require_run_count"] is True
    assert captured["require_max_cycles"] is True
    assert captured["require_max_concurrent_battles"] is True
    assert captured["require_replay_behavior"] is True


def test_stop_runtime_lease_guard_uses_stop_purpose(monkeypatch):
    module = load_module()
    captured = {}

    monkeypatch.setattr(module, "load_env_files", lambda: {"PS_USERNAME": "runtimebot"})

    def fake_validate_runtime_lease(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "purpose": kwargs["purpose"]}

    monkeypatch.setattr(module, "validate_runtime_lease", fake_validate_runtime_lease)

    guard = module.runtime_lease_guard_for_action(
        "stop",
        SimpleNamespace(runtime_lease="lease.json"),
    )

    assert guard["purpose"] == module.JIGGLYPUFF_RUNTIME_STOP_PURPOSE
    assert captured["purpose"] == module.JIGGLYPUFF_RUNTIME_STOP_PURPOSE
    assert captured["requested_run_count"] == 1
    assert captured["requested_max_cycles"] == 1
    assert captured["requested_max_concurrent_battles"] == 1
    assert captured["requested_account"] == "runtimebot"
    assert captured["require_replay_behavior"] is True


def test_stop_execute_is_blocked_without_runtime_lease(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "load_env_files", lambda: {"PS_USERNAME": "runtimebot"})
    monkeypatch.setattr(
        module,
        "validate_runtime_lease",
        lambda **kwargs: {
            "ok": False,
            "purpose": kwargs["purpose"],
            "blockers": ["missing lease"],
        },
    )
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
    assert payload["status"] == "blocked-runtime-lease"
    assert payload["runtimeLease"]["purpose"] == module.JIGGLYPUFF_RUNTIME_STOP_PURPOSE
    assert payload["blockers"] == ["missing lease"]


def test_start_execute_fails_closed_without_runtime_lease(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setattr(module, "load_env_files", lambda: {"PS_USERNAME": "bot"})
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
    assert payload["status"] == "blocked-runtime-lease"
    assert payload["runtimeLease"]["ok"] is False


def test_start_execute_blocks_runtime_lease_account_mismatch(monkeypatch, tmp_path):
    module = load_module()
    lease_path = write_runtime_lease(module, tmp_path / "runtime-lease.json", account="wrongbot")

    monkeypatch.setattr(module, "load_env_files", lambda: {"PS_USERNAME": "claudechamp"})
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
    assert payload["status"] == "blocked-runtime-lease"
    assert payload["runtimeLease"]["requested"]["account"] == "claudechamp"
    assert "does not match requested account claudechamp" in " ".join(payload["blockers"])


def test_start_execute_passes_max_cycles_after_runtime_lease(monkeypatch, tmp_path):
    module = load_module()
    lease_path = write_runtime_lease(module, tmp_path / "runtime-lease.json")
    captured = {}

    def fake_remote_command(action, **kwargs):
        captured["action"] = action
        captured["kwargs"] = kwargs
        return {"returnCode": 0, "json": {"ok": True, "status": "ready-idle"}, "stderr": ""}

    monkeypatch.setattr(module, "remote_command", fake_remote_command)
    monkeypatch.setattr(module, "mirror_status", lambda payload, **kwargs: dict(payload))
    monkeypatch.setattr(module, "load_env_files", lambda: {"PS_USERNAME": "bot"})

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

    assert code == 0
    assert captured["action"] == "start"
    assert captured["kwargs"]["max_cycles"] == 1
    assert captured["kwargs"]["runtime_lease"] == str(lease_path)
    assert payload["status"] == "ready-idle"


def test_gitignore_excludes_generated_runtime_lease_artifacts():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "devstream/truth/stale-runtime-artifact-backups/" in ignored
    assert "devstream/truth/runtime-lease*.json" in ignored
