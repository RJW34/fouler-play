from __future__ import annotations

import os


UNKNOWN_BOT_USERNAME = "unknown-bot"


def resolve_bot_username(default: str = UNKNOWN_BOT_USERNAME) -> str:
    """Resolve the active Showdown bot account without stale hard-coded names."""
    for key in ("PS_USERNAME", "SHOWDOWN_USER_ID", "BOT_USERNAME"):
        value = os.getenv(key, "").strip()
        if value:
            return value

    accounts = [
        account.strip()
        for account in os.getenv("SHOWDOWN_ACCOUNTS", "").split(",")
        if account.strip()
    ]
    if accounts:
        return accounts[0]
    return default
