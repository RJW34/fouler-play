from __future__ import annotations

import json
import os
import re
from pathlib import Path


UNKNOWN_BOT_USERNAME = "unknown-bot"
ROOT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_LEASE_PATH = Path(
    os.getenv("FOULER_RUNTIME_LEASE_PATH", ROOT_DIR / "devstream" / "truth" / "runtime-lease.json")
)

# Canonical, single-source-of-truth list of every Showdown account the bot has
# ever laddered under. This MATTERS for loss-learning: the live lease only knows
# the CURRENT account (e.g. ``thepeakmons``), but the windowed replay corpus is
# dominated by games played under PRIOR accounts. If side-detection only matches
# the current account, ~every old-account replay falls back to ``p1`` and its
# win/loss label inverts -- which records the bot's OWN team as "threats". So any
# account the bot has used (current OR historical) must be matched against the
# replay ``players`` field. Verified against the live 500-replay window
# (2026-06-24): LEBOTJAMESXD00N=527 name-occurrences, OUBotBeepBoop=7,
# thepeakmons=7; every other player name is an opponent (<=5).
#
# To register a NEW lease account: prefer the runtime lease / SHOWDOWN_ACCOUNTS
# env (picked up automatically below). Add a name here only for accounts that
# have ALREADY left the live config but still appear in the replay window.
HISTORICAL_BOT_ACCOUNTS = (
    "LEBOTJAMESXD00N",
    "OUBotBeepBoop",
    "thepeakmons",
    "npctypebeat",
)


def _norm_account(value: object) -> str:
    """Normalize an account name for comparison (Showdown user-id rules: lower,
    strip non-alphanumerics). Mirrors loss_learning.normalize_id so the two sides
    compare identically."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _runtime_lease_account() -> str:
    try:
        lease = json.loads(RUNTIME_LEASE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(lease, dict):
        return ""
    battle_scope = lease.get("battleScope") if isinstance(lease.get("battleScope"), dict) else {}
    for value in (
        lease.get("account"),
        lease.get("psUsername"),
        lease.get("showdownAccount"),
        battle_scope.get("account"),
        battle_scope.get("psUsername"),
    ):
        account = str(value or "").strip()
        if account:
            return account
    return ""


def resolve_bot_username(default: str = UNKNOWN_BOT_USERNAME) -> str:
    """Resolve the active Showdown bot account without stale hard-coded names."""
    lease_account = _runtime_lease_account()
    if lease_account:
        return lease_account

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


def resolve_bot_accounts() -> set[str]:
    """Return the NORMALIZED set of every account the bot is or has been known as.

    Single source of truth for "is this player the bot?" across the loss-learning
    pipeline. Combines, in order of authority:
      1. the live runtime-lease account (current lease, e.g. ``thepeakmons``),
      2. env accounts (``PS_USERNAME`` / ``SHOWDOWN_USER_ID`` / ``BOT_USERNAME`` /
         every entry in ``SHOWDOWN_ACCOUNTS``),
      3. the canonical ``HISTORICAL_BOT_ACCOUNTS`` list above.

    Returned ids are normalized (lower + alphanumerics only) so callers can match
    them directly against ``normalize_id(replay_player_name)``. Empty/unknown
    names are dropped. The result is never empty in practice (the historical list
    is always present) which is what lets old-account replays resolve correctly.
    """
    candidates: list[str] = []

    lease_account = _runtime_lease_account()
    if lease_account:
        candidates.append(lease_account)

    for key in ("PS_USERNAME", "SHOWDOWN_USER_ID", "BOT_USERNAME"):
        value = os.getenv(key, "").strip()
        if value:
            candidates.append(value)

    candidates.extend(
        account.strip()
        for account in os.getenv("SHOWDOWN_ACCOUNTS", "").split(",")
        if account.strip()
    )

    candidates.extend(HISTORICAL_BOT_ACCOUNTS)

    return {norm for norm in (_norm_account(c) for c in candidates) if norm}
