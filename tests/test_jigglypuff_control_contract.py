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


def write_runtime_lease(module, path: Path) -> Path:
    payload = {
        "schemaVersion": "fouler-play-runtime-lease/v1",
        "projectId": "fouler-play",
        "leaseId": "lease-test",
        "status": "active",
        "machine": "JIGGLYPUFF",
        "account": "bot",
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
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "TRUTH_DIR", tmp_path / "devstream" / "truth")
    monkeypatch.setattr(
        module,
        "remote_command",
        lambda action, **kwargs: {
            "ok": True,
            "returnCode": 0,
            "json": {"ok": True, "status": "ready-idle"},
        },
    )
    monkeypatch.setattr(
        module,
        "mirror_live_state",
        lambda: (_ for _ in ()).throw(AssertionError("read-only status should not mirror live state")),
    )

    code, payload = module.action_status(SimpleNamespace(timeout=45, mirror=False))

    assert code == 0
    assert payload["status"] == "ready-idle"
    assert payload["mirrorSkipped"] is True
    assert payload["liveStateMirror"]["skipped"] is True
    assert not (tmp_path / "active_battles.json").exists()
    assert not (tmp_path / "devstream" / "truth" / "jigglypuff-runtime.json").exists()


def test_control_supports_direct_ip_env_overrides(monkeypatch):
    monkeypatch.setenv("FOULER_JIGGLYPUFF_SSH", "Ryanj@192.168.1.40")
    monkeypatch.setenv("FOULER_JIGGLYPUFF_OBS_HTTP", "http://192.168.1.40:8777/")
    monkeypatch.setenv("FOULER_JIGGLYPUFF_WORKER_HTTP", "http://192.168.1.40:8791/")

    module = load_module()

    assert module.REMOTE == "Ryanj@192.168.1.40"
    assert module.OBS_HTTP == "http://192.168.1.40:8777"
    assert module.WORKER_HTTP == "http://192.168.1.40:8791"


def test_control_defaults_to_jigglypuff_direct_ip_with_tailnet_fallback(monkeypatch):
    monkeypatch.delenv("FOULER_JIGGLYPUFF_OBS_HTTP", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_WORKER_HTTP", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_OBS_HTTP_FALLBACKS", raising=False)
    monkeypatch.delenv("FOULER_JIGGLYPUFF_WORKER_HTTP_FALLBACKS", raising=False)

    module = load_module()

    assert module.OBS_HTTP == "http://192.168.1.126:8777"
    assert module.WORKER_HTTP == "http://192.168.1.126:8791"
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


def test_start_execute_fails_closed_without_runtime_lease(monkeypatch, tmp_path):
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
    assert payload["status"] == "blocked-runtime-lease"
    assert payload["runtimeLease"]["ok"] is False


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
