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
