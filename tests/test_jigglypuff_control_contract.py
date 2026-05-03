import importlib.util
from pathlib import Path


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
    assert mirrored["liveStateMirror"]["activeBattlesMirrored"] is True
