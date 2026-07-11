import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "refresh_matchup_weights.py"


def load_module():
    spec = importlib.util.spec_from_file_location("refresh_matchup_weights_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_flag_reads_dotenv_without_scheduler_environment(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.delenv("MATCHUP_MEMORY_ENABLED", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("MATCHUP_MEMORY_ENABLED=0\n", encoding="utf-8")

    assert module.matchup_memory_enabled(env_path) is False


def test_disabled_runtime_gate_returns_before_scanning_replays(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "matchup_memory_enabled", lambda: False)
    monkeypatch.setattr(
        module,
        "resolve_bot_username",
        lambda: (_ for _ in ()).throw(AssertionError("replay scan should not start")),
    )

    assert module.main() == 0
