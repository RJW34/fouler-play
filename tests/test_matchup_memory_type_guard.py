"""Type-token guard for matchup-memory weights (2026-06-24).

A nickname->species fallthrough (slot never seen in a switch/drag line) can leak
a bare Pokemon TYPE token ("water", "dark", "ground", ...) into the species key.
No real OU species shares a name with a bare type, so such a key is a parse
artifact and must never become a flagged "threat species".
"""

from fp import matchup_memory


def _loss_artifact(opp_team, ko_attackers, bot_side="p1"):
    opp_side = "p2" if bot_side == "p1" else "p1"
    return {
        "bot_side": bot_side,
        "result": "loss",
        "teams": {bot_side: ["Corviknight"], opp_side: opp_team},
        "key_kos": [
            {"attacker": atk, "target_side": bot_side} for atk in ko_attackers
        ],
    }


def test_type_tokens_excluded_from_bad_matchups():
    art = _loss_artifact(
        opp_team=["water", "dark", "ground", "Great Tusk"],
        ko_attackers=["Great Tusk"],
    )
    weights = matchup_memory.update_weights_from_artifacts([art])
    bad = weights["bad_matchups"]
    assert "greattusk" in bad
    for token in ("water", "dark", "ground", "ice", "fire", "steel", "flying"):
        assert token not in bad, f"type token {token!r} leaked into bad_matchups"


def test_type_tokens_excluded_from_problem_pokemon():
    art = _loss_artifact(
        opp_team=["Great Tusk"],
        ko_attackers=["water", "dark", "ground", "Great Tusk"],
    )
    weights = matchup_memory.update_weights_from_artifacts([art])
    problem = weights["problem_pokemon"]
    assert "greattusk" in problem
    for token in ("water", "dark", "ground", "ice", "fire", "steel", "flying"):
        assert token not in problem, f"type token {token!r} leaked into problem_pokemon"


def test_type_token_never_flags_on_read_path():
    """Even if a stale weights file already contains a type-token entry, the live
    read path must refuse to flag it."""
    weights = {
        "problem_pokemon": {
            "water": {"kos_on_us": 99, "losses_present": 99},
            "greattusk": {"kos_on_us": 30, "losses_present": 12},
        },
        "bad_matchups": {},
    }
    assert matchup_memory.opponent_is_flagged("water", weights) is None
    assert matchup_memory.opponent_is_flagged("greattusk", weights) is not None
