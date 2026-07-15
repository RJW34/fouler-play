import importlib

from fp import devstream_chat


def enable_chat(monkeypatch, cooldown=1):
    """Simulate FOULER_POST_BATTLE_CHAT_ENABLED at runtime without touching
    live process env; monkeypatch restores module state after each test."""
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_CHAT_ENABLED", True)
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_CHAT_COOLDOWN_BATTLES", cooldown)
    monkeypatch.setattr(devstream_chat, "_post_battle_chat_calls", 0)


def test_post_battle_chat_disabled_by_default(monkeypatch):
    """Without FOULER_POST_BATTLE_CHAT_ENABLED the bot sends no post-battle
    chat at all (streamer-voice policy: no canned strings by default)."""
    monkeypatch.delenv("FOULER_POST_BATTLE_CHAT_ENABLED", raising=False)
    monkeypatch.delenv("FOULER_POST_BATTLE_PROMO_AUTHORIZED", raising=False)
    monkeypatch.delenv("FOULER_POST_BATTLE_CHAT_COOLDOWN_BATTLES", raising=False)
    try:
        mod = importlib.reload(devstream_chat)
        assert mod.POST_BATTLE_CHAT_ENABLED is False
        assert mod.POST_BATTLE_PROMO_AUTHORIZED is False
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
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_CHAT_ENABLED", False)
    monkeypatch.setattr(devstream_chat, "_post_battle_chat_calls", 0)

    assert devstream_chat.post_battle_messages() == []
    # The disabled gate must not consume cooldown slots.
    assert devstream_chat._post_battle_chat_calls == 0


def test_post_battle_chat_is_gg_only_when_enabled(monkeypatch):
    enable_chat(monkeypatch)

    assert devstream_chat.post_battle_messages() == ["gg"]


def test_post_battle_chat_live_promo_requires_explicit_authorization(monkeypatch):
    enable_chat(monkeypatch)
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_PROMO_AUTHORIZED", False)
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_LIVE_PROMO_MESSAGE", "twitch.tv/thepeakmos")
    monkeypatch.setattr(devstream_chat, "devstream_is_live", lambda: True)

    assert devstream_chat.post_battle_messages() == ["gg"]


def test_post_battle_chat_preserves_live_only_promo_when_authorized(monkeypatch):
    enable_chat(monkeypatch)
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_PROMO_AUTHORIZED", True)
    monkeypatch.setattr(devstream_chat, "POST_BATTLE_LIVE_PROMO_MESSAGE", "twitch.tv/thepeakmos")
    monkeypatch.setattr(devstream_chat, "devstream_is_live", lambda: False)

    assert devstream_chat.post_battle_messages() == ["gg"]

    monkeypatch.setattr(devstream_chat, "devstream_is_live", lambda: True)
    assert devstream_chat.post_battle_messages() == ["gg", "twitch.tv/thepeakmos"]


def test_static_live_environment_is_not_stream_truth(monkeypatch):
    monkeypatch.setenv("FOULER_DEVSTREAM_LIVE", "1")
    monkeypatch.delenv("FOULER_DEVSTREAM_STATUS_JSON", raising=False)
    monkeypatch.delenv("FOULER_DEVSTREAM_STATUS_URL", raising=False)
    monkeypatch.setattr(devstream_chat, "STREAM_STATUS_JSON", "")
    monkeypatch.setattr(devstream_chat, "STREAM_STATUS_URL", "")

    assert devstream_chat.devstream_is_live() is False


def test_post_battle_chat_cooldown_suppresses_between_batches(monkeypatch):
    enable_chat(monkeypatch, cooldown=3)

    assert devstream_chat.post_battle_messages() == ["gg"]
    assert devstream_chat.post_battle_messages() == []
    assert devstream_chat.post_battle_messages() == []
    assert devstream_chat.post_battle_messages() == ["gg"]
    assert devstream_chat.post_battle_messages() == []
