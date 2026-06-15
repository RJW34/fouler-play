"""Tests for break_repeated_decision -- the hard decision_instability loop-breaker.

The soft repetition penalty only scales weights; this guard runs at the final
selection chokepoint and demotes a repeated best move below a distinct legal
alternative so the bot cannot relive the same loss-driving action loop.
"""

from unittest.mock import MagicMock

from fp.search.main import break_repeated_decision, _recent_action_history


def _battle(history, last_selected_move=None):
    battle = MagicMock()
    battle.user.action_history = list(history)
    if last_selected_move is None:
        battle.user.last_selected_move = None
    else:
        battle.user.last_selected_move = MagicMock(move=last_selected_move)
    return battle


def _pokemon(*, hp=100, fainted=False):
    pkmn = MagicMock()
    pkmn.hp = hp
    pkmn.fainted = fainted
    return pkmn


def _last_mon_battle(history):
    battle = _battle(history)
    battle.user.active = _pokemon(hp=97)
    battle.user.reserve = [_pokemon(hp=0, fainted=True) for _ in range(5)]
    return battle


def _sorted(policy: dict) -> list[tuple[str, float]]:
    return sorted(policy.items(), key=lambda x: x[1], reverse=True)


class TestRecentActionHistory:
    def test_appends_pending_last_selected_move(self):
        battle = _battle(["seismictoss", "softboiled"], last_selected_move="seismictoss")
        assert _recent_action_history(battle) == ["seismictoss", "softboiled", "seismictoss"]

    def test_does_not_duplicate_when_history_already_has_last(self):
        battle = _battle(["seismictoss", "softboiled"], last_selected_move="softboiled")
        assert _recent_action_history(battle) == ["seismictoss", "softboiled"]

    def test_none_battle(self):
        assert _recent_action_history(None) == []


class TestBreakRepeatedDecision:
    def test_demotes_repeated_best_move_below_distinct_alternative(self):
        # seismictoss chosen 3x recently; it is the dominant best move but the
        # loop-breaker must demote it below the distinct legal alternative.
        battle = _battle(["seismictoss", "seismictoss", "seismictoss"])
        policy = _sorted({"seismictoss": 100.0, "softboiled": 40.0, "switch blissey": 30.0})
        trace = []
        result = break_repeated_decision(policy, battle, trace_events=trace)
        assert result[0][0] != "seismictoss"
        assert result[0][0] == "softboiled"  # best distinct alternative now leads
        assert any(e["source"] == "decision_loop_break" for e in trace)

    def test_no_change_when_not_repeated_enough(self):
        battle = _battle(["seismictoss", "softboiled", "switch blissey"])
        policy = _sorted({"seismictoss": 100.0, "softboiled": 40.0})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] == "seismictoss"

    def test_no_change_when_no_distinct_alternative(self):
        # Only the repeated move has positive weight -> it is forced, keep it.
        battle = _battle(["shadowball", "shadowball", "shadowball"])
        policy = _sorted({"shadowball": 100.0, "switch x": 0.0})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] == "shadowball"

    def test_demoted_move_remains_present_as_last_resort(self):
        battle = _battle(["surf", "surf", "surf"])
        policy = _sorted({"surf": 100.0, "ivycudgel": 50.0})
        result = break_repeated_decision(policy, battle)
        moves = [m for m, _ in result]
        assert "surf" in moves  # never removed, only demoted

    def test_switch_loop_demoted(self):
        # Repeated switch target should also be broken.
        battle = _battle(["switch blissey", "switch blissey", "switch blissey"])
        policy = _sorted({"switch blissey": 90.0, "sludgebomb": 50.0, "switch dondozo": 45.0})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] != "switch blissey"

    def test_last_mon_repeated_damage_is_not_forced_to_weaker_attack(self):
        battle = _last_mon_battle(["malignantchain", "malignantchain", "malignantchain"])
        policy = _sorted({"malignantchain": 0.36, "foulplay": 0.26, "partingshot": 0.18})
        trace = []
        result = break_repeated_decision(policy, battle, trace_events=trace)
        assert result[0][0] == "malignantchain"
        assert any(
            e["source"] == "decision_loop_break"
            and e["type"] == "skip"
            and "last_mon_damage_progress" in e["reason"]
            for e in trace
        )

    def test_repeated_damage_still_demoted_when_switch_target_alive(self):
        battle = _battle(["malignantchain", "malignantchain", "malignantchain"])
        battle.user.active = _pokemon(hp=97)
        battle.user.reserve = [_pokemon(hp=100, fainted=False)]
        policy = _sorted({"malignantchain": 0.36, "foulplay": 0.26, "partingshot": 0.18})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] != "malignantchain"

    def test_last_mon_repeated_recovery_can_still_be_broken(self):
        battle = _last_mon_battle(["recover", "recover", "recover"])
        policy = _sorted({"recover": 0.90, "foulplay": 0.30})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] == "foulplay"

    def test_none_battle_returns_unchanged(self):
        policy = _sorted({"surf": 100.0, "ivycudgel": 50.0})
        assert break_repeated_decision(policy, None) == policy

    def test_empty_policy_returns_unchanged(self):
        battle = _battle(["surf", "surf", "surf"])
        assert break_repeated_decision([], battle) == []
