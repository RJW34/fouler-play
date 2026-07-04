import importlib
import json

from fp import devstream_chat


def clear_live_env(monkeypatch):
    monkeypatch.delenv("FOULER_DEVSTREAM_LIVE", raising=False)
    monkeypatch.delenv("FOULER_DEVSTREAM_STATUS_JSON", raising=False)
    monkeypatch.delenv("FOULER_DEVSTREAM_STATUS_URL", raising=False)
    monkeypatch.setattr(devstream_chat, "STREAM_STATUS_JSON", "")
    monkeypatch.setattr(devstream_chat, "STREAM_STATUS_URL", "")


def enable_chat(monkeypatch, cooldown=1):
    """Simulate FOULER_POST_BATTLE_CHAT_ENABLED at runtime without touching
    live process env; monkeypatch restores module state after each test."""
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_CHAT_ENABLED", True)
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_CHAT_COOLDOWN_BATTLES", cooldown)
    monkeypatch.setattr(devstream_chat, "_post_battle_chat_calls", 0)


def test_post_battle_chat_disabled_by_default(monkeypatch):
    """Without FOULER_POST_BATTLE_CHAT_ENABLED the bot sends no post-battle
    chat at all (streamer-voice policy: no canned strings by default)."""
    clear_live_env(monkeypatch)
    monkeypatch.delenv("FOULER_POST_BATTLE_CHAT_ENABLED", raising=False)
    monkeypatch.delenv("FOULER_POST_BATTLE_CHAT_COOLDOWN_BATTLES", raising=False)
    try:
        mod = importlib.reload(devstream_chat)
        assert mod.POST_BATTLE_CHAT_ENABLED is False
        assert mod.POST_BATTLE_CHAT_COOLDOWN_BATTLES == 12
        assert mod.post_battle_messages() == []
        assert mod.post_battle_messages() == []

        monkeypatch.setenv("FOULER_POST_BATTLE_CHAT_ENABLED", "true")
        mod = importlib.reload(devstream_chat)
        assert mod.POST_BATTLE_CHAT_ENABLED is True

        monkeypatch.setenv("FOULER_POST_BATTLE_CHAT_ENABLED", "0")
        mod = importlib.reload(devstream_chat)
        assert mod.POST_BATTLE_CHAT_ENABLED is False
    finally:
        # Restore true process env, then rebuild module state from it so
        # later tests see the real import-time configuration.
        monkeypatch.undo()
        importlib.reload(devstream_chat)


def test_post_battle_chat_disabled_gate_short_circuits(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_CHAT_ENABLED", False)
    monkeypatch.setattr(devstream_chat, "_post_battle_chat_calls", 0)

    assert devstream_chat.post_battle_messages() == []
    # The disabled gate must not consume cooldown slots.
    assert devstream_chat._post_battle_chat_calls == 0


def test_post_battle_chat_offline_is_gg_only_when_enabled(monkeypatch):
    clear_live_env(monkeypatch)
    enable_chat(monkeypatch)

    assert devstream_chat.post_battle_messages() == ["gg"]


def test_post_battle_chat_live_keeps_twitch_promo_when_enabled(monkeypatch):
    clear_live_env(monkeypatch)
    enable_chat(monkeypatch)
    monkeypatch.setenv("FOULER_DEVSTREAM_LIVE", "1")

    assert devstream_chat.post_battle_messages() == ["gg", "twitch.tv/thepeakmos"]


def test_post_battle_chat_cooldown_suppresses_between_batches(monkeypatch):
    clear_live_env(monkeypatch)
    enable_chat(monkeypatch, cooldown=3)

    assert devstream_chat.post_battle_messages() == ["gg"]
    assert devstream_chat.post_battle_messages() == []
    assert devstream_chat.post_battle_messages() == []
    assert devstream_chat.post_battle_messages() == ["gg"]
    assert devstream_chat.post_battle_messages() == []


def test_post_battle_chat_reads_deku_metrics_stream_state(monkeypatch, tmp_path):
    clear_live_env(monkeypatch)
    enable_chat(monkeypatch)
    status = tmp_path / "deku-metrics.json"
    status.write_text(
        json.dumps(
            {
                "schemaVersion": "deku-metrics/v1",
                "summary": {"streamActive": False},
                "metrics": [{"name": "deku_obs_stream_active", "value": 0}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FOULER_DEVSTREAM_STATUS_JSON", str(status))

    assert devstream_chat.post_battle_messages() == ["gg"]

    status.write_text(
        json.dumps(
            {
                "schemaVersion": "deku-metrics/v1",
                "summary": {"streamActive": True},
                "metrics": [{"name": "deku_obs_stream_active", "value": 1}],
            }
        ),
        encoding="utf-8",
    )

    assert devstream_chat.post_battle_messages() == ["gg", "twitch.tv/thepeakmos"]
