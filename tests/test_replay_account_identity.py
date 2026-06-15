from replay_analysis.account_identity import resolve_bot_username


def test_resolve_bot_username_prefers_runtime_account(monkeypatch):
    monkeypatch.setenv("PS_USERNAME", "LEBOTJAMESXD00N")
    monkeypatch.setenv("SHOWDOWN_USER_ID", "other")
    monkeypatch.setenv("SHOWDOWN_ACCOUNTS", "first,second")

    assert resolve_bot_username() == "LEBOTJAMESXD00N"


def test_resolve_bot_username_uses_showdown_user_id(monkeypatch):
    monkeypatch.delenv("PS_USERNAME", raising=False)
    monkeypatch.setenv("SHOWDOWN_USER_ID", "LEBOTJAMESXD00N")
    monkeypatch.setenv("SHOWDOWN_ACCOUNTS", "first,second")

    assert resolve_bot_username() == "LEBOTJAMESXD00N"


def test_resolve_bot_username_falls_back_to_first_account_alias(monkeypatch):
    monkeypatch.delenv("PS_USERNAME", raising=False)
    monkeypatch.delenv("SHOWDOWN_USER_ID", raising=False)
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    monkeypatch.setenv("SHOWDOWN_ACCOUNTS", " LEBOTJAMESXD00N , backup ")

    assert resolve_bot_username() == "LEBOTJAMESXD00N"


def test_resolve_bot_username_uses_unknown_not_stale_account(monkeypatch):
    monkeypatch.delenv("PS_USERNAME", raising=False)
    monkeypatch.delenv("SHOWDOWN_USER_ID", raising=False)
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    monkeypatch.delenv("SHOWDOWN_ACCOUNTS", raising=False)

    assert resolve_bot_username() == "unknown-bot"
