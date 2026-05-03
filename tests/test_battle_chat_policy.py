import json

from fp import devstream_chat


def clear_live_env(monkeypatch):
    monkeypatch.delenv("FOULER_DEVSTREAM_LIVE", raising=False)
    monkeypatch.delenv("FOULER_DEVSTREAM_STATUS_JSON", raising=False)
    monkeypatch.delenv("FOULER_DEVSTREAM_STATUS_URL", raising=False)
    monkeypatch.setattr(devstream_chat, "STREAM_STATUS_JSON", "")
    monkeypatch.setattr(devstream_chat, "STREAM_STATUS_URL", "")


def test_post_battle_chat_offline_is_gg_only(monkeypatch):
    clear_live_env(monkeypatch)

    assert devstream_chat.post_battle_messages() == ["gg"]


def test_post_battle_chat_live_keeps_twitch_promo(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("FOULER_DEVSTREAM_LIVE", "1")

    assert devstream_chat.post_battle_messages() == ["gg", "twitch.tv/thepeakmos"]


def test_post_battle_chat_reads_deku_metrics_stream_state(monkeypatch, tmp_path):
    clear_live_env(monkeypatch)
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
