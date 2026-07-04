"""Account-history-aware bot-side detection (contamination root-fix, 2026-06-24).

The bug these tests lock down: ``_detect_bot_side`` previously matched the replay
``players`` field against only the single CURRENT lease account. The windowed
replay corpus is dominated by games played under PRIOR accounts, so those replays
fell back to ``p1``, inverting the win/loss label and recording the bot's OWN team
as "threats". The fix matches against the SET of every known bot account (current
lease + env + canonical historical list).

Coverage required by the task:
  * old-account replay where the bot is p1
  * old-account replay where the bot is p2
  * new-account (current lease) replay
  * unknown-account replay (neither side is a known bot account)
"""

import pytest

from replay_analysis.account_identity import resolve_bot_accounts
from replay_analysis.loss_learning import LossLogIngestor, build_loss_artifact


CURRENT_ACCOUNT = "thepeakmons"
OLD_ACCOUNT = "LEBOTJAMESXD00N"


@pytest.fixture(autouse=True)
def _isolate_identity(monkeypatch, tmp_path):
    """Make identity resolution deterministic: no live runtime lease, and the
    current lease account simulated via env. The canonical historical list is
    always available regardless of env, which is the point of the fix."""
    monkeypatch.setenv("FOULER_RUNTIME_LEASE_PATH", str(tmp_path / "no-such-lease.json"))
    monkeypatch.setenv("PS_USERNAME", CURRENT_ACCOUNT)
    monkeypatch.setenv("SHOWDOWN_USER_ID", CURRENT_ACCOUNT)
    monkeypatch.setenv("SHOWDOWN_ACCOUNTS", CURRENT_ACCOUNT)
    monkeypatch.delenv("BOT_USERNAME", raising=False)


def _replay(p1_name: str, p2_name: str, winner: str, replay_id: str = "gen9ou-side") -> dict:
    """Minimal but well-formed loss/win replay.

    The bot's team is Corviknight (the bot's OWN mon). The opponent's KO comes
    from Great Tusk (a genuine opponent threat). If side detection is wrong the
    win/loss label inverts and Corviknight gets recorded as a "problem pokemon"
    KO source -- exactly the contamination we are fixing.
    """
    log = f"""
|player|p1|{p1_name}|1|1000
|player|p2|{p2_name}|2|1000
|gen|9
|tier|[Gen 9] OU
|clearpoke
|poke|p1|Corviknight, F|
|poke|p2|Great Tusk|
|teampreview
|start
|switch|p1a: Corviknight|Corviknight, F|100/100
|switch|p2a: Great Tusk|Great Tusk|100/100
|turn|1
|move|p2a: Great Tusk|Earthquake|p1a: Corviknight
|-damage|p1a: Corviknight|0 fnt
|faint|p1a: Corviknight
|win|{winner}
""".strip()
    return {
        "id": replay_id,
        "format": "[Gen 9] OU",
        "formatid": "gen9ou",
        "players": [p1_name, p2_name],
        "log": log,
    }


def test_old_account_replay_bot_is_p1():
    """Bot laddered under the OLD account, sitting on p1, and lost. Side must
    resolve to p1 and the result must be a loss (NOT an inverted win)."""
    art = build_loss_artifact(_replay(OLD_ACCOUNT, "RandomOpponent", winner="RandomOpponent"))
    assert art["bot_side"] == "p1"
    assert art["result"] == "loss"
    # Bot's own mon (Corviknight, on p1) fainted; the KO source is Great Tusk
    # (p2). The bot's own team must never be the KO attacker recorded.
    ko_attackers = {ko.get("attacker") for ko in art["key_kos"]}
    assert "Corviknight" not in ko_attackers


def test_old_account_replay_bot_is_p2():
    """Same bot, OLD account, but sitting on p2. The reliable players field must
    place the bot on p2 -- the old code blindly fell back to p1 here."""
    # Put the bot's own mon on p2 and the threat on p1 by swapping the log sides.
    log = f"""
|player|p1|RandomOpponent|1|1000
|player|p2|{OLD_ACCOUNT}|2|1000
|gen|9
|tier|[Gen 9] OU
|clearpoke
|poke|p1|Great Tusk|
|poke|p2|Corviknight, F|
|teampreview
|start
|switch|p1a: Great Tusk|Great Tusk|100/100
|switch|p2a: Corviknight|Corviknight, F|100/100
|turn|1
|move|p1a: Great Tusk|Earthquake|p2a: Corviknight
|-damage|p2a: Corviknight|0 fnt
|faint|p2a: Corviknight
|win|RandomOpponent
""".strip()
    replay = {
        "id": "gen9ou-side-p2",
        "format": "[Gen 9] OU",
        "formatid": "gen9ou",
        "players": ["RandomOpponent", OLD_ACCOUNT],
        "log": log,
    }
    art = build_loss_artifact(replay)
    assert art["bot_side"] == "p2"
    assert art["result"] == "loss"
    ko_attackers = {ko.get("attacker") for ko in art["key_kos"]}
    assert "Corviknight" not in ko_attackers


def test_new_account_replay_resolves_current_lease():
    """A replay under the CURRENT lease account must still resolve correctly."""
    art = build_loss_artifact(_replay(CURRENT_ACCOUNT, "RandomOpponent", winner="RandomOpponent"))
    assert art["bot_side"] == "p1"
    assert art["result"] == "loss"


def test_unknown_account_replay_falls_back_to_p1():
    """Neither side is a known bot account: deterministic legacy fallback to p1.
    Such replays are not the bot's and contribute no useful matchup signal."""
    art = build_loss_artifact(_replay("StrangerOne", "StrangerTwo", winner="StrangerTwo"))
    assert art["bot_side"] == "p1"


def test_resolve_bot_accounts_includes_historical_and_current():
    """The canonical account set must contain both the current lease account and
    the historical accounts, normalized."""
    accounts = resolve_bot_accounts()
    assert "thepeakmons" in accounts
    assert "lebotjamesxd00n" in accounts  # normalized LEBOTJAMESXD00N
    assert "oubotbeepboop" in accounts


def test_explicit_bot_username_override_still_matches():
    """An explicit single bot_username must always be included in the match set
    even if it is not in the historical list."""
    ingestor = LossLogIngestor(bot_username="BrandNewLease99", bot_accounts=set())
    assert "brandnewlease99" in ingestor.bot_accounts
