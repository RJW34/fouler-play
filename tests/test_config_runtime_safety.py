from config import (
    BotModes,
    FoulPlayConfig,
    _coerce_ladder_search_time_ms,
    _env_int_prefer,
)


def test_env_int_prefer_uses_first_valid_value(monkeypatch):
    monkeypatch.setenv("SEARCH_TIME_MS", "")
    monkeypatch.setenv("PS_SEARCH_TIME_MS", "2800")
    assert _env_int_prefer(("SEARCH_TIME_MS", "PS_SEARCH_TIME_MS"), 100) == 2800


def test_env_int_prefer_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("SEARCH_TIME_MS", raising=False)
    monkeypatch.setenv("PS_SEARCH_TIME_MS", "not-an-int")
    assert _env_int_prefer(("SEARCH_TIME_MS", "PS_SEARCH_TIME_MS"), 100) == 100


def test_coerce_ladder_search_time_applies_floor_for_hybrid():
    value, clamped = _coerce_ladder_search_time_ms(
        search_time_ms=900,
        bot_mode=BotModes.search_ladder,
        pokemon_format="gen9ou",
        decision_policy="hybrid",
        min_search_time_ms=1200,
    )
    assert clamped is True
    assert value == 1500


def test_coerce_ladder_search_time_no_clamp_outside_ladder_ou():
    value, clamped = _coerce_ladder_search_time_ms(
        search_time_ms=500,
        bot_mode=BotModes.challenge_user,
        pokemon_format="gen9ou",
        decision_policy="hybrid",
        min_search_time_ms=1200,
    )
    assert clamped is False
    assert value == 500


def test_config_accepts_runtime_launcher_args(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run.py",
            "--websocket-uri",
            "ws://localhost/showdown/websocket",
            "--ps-username",
            "bot",
            "--bot-mode",
            "search_ladder",
            "--pokemon-format",
            "gen9ou",
            "--team-name",
            "gen9ou",
            "--max-concurrent-battles",
            "3",
            "--max-mcts-battles",
            "4",
            "--decision-policy",
            "eval",
            "--spectator-username",
            "observer",
        ],
    )

    FoulPlayConfig.configure()

    assert FoulPlayConfig.max_concurrent_battles == 3
    assert FoulPlayConfig.max_mcts_battles == 4
    assert FoulPlayConfig.decision_policy == "eval"
    assert FoulPlayConfig.spectator_username == "observer"


def test_config_still_validates_challenge_user_target(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run.py",
            "--websocket-uri",
            "ws://localhost/showdown/websocket",
            "--ps-username",
            "bot",
            "--bot-mode",
            "challenge_user",
            "--pokemon-format",
            "gen9ou",
        ],
    )

    try:
        FoulPlayConfig.configure()
    except AssertionError as exc:
        assert "USER_TO_CHALLENGE" in str(exc)
    else:
        raise AssertionError("challenge_user without user_to_challenge should fail validation")
