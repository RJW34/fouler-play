"""Tests for break_repeated_decision -- the hard decision_instability loop-breaker.

The soft repetition penalty only scales weights; this guard runs at the final
selection chokepoint and demotes a repeated best move below a distinct legal
alternative so the bot cannot relive the same loss-driving action loop.
"""

from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from fp.search.main import (
    DecisionProfile,
    _apply_hard_legality_and_safety,
    break_repeated_decision,
    select_move_from_eval_scores,
    _recent_action_history,
)


def _battle(history, last_selected_move=None):
    battle = MagicMock()
    battle.user.action_history = list(history)
    if last_selected_move is None:
        battle.user.last_selected_move = None
    else:
        battle.user.last_selected_move = MagicMock(move=last_selected_move)
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

    def test_none_battle_returns_unchanged(self):
        policy = _sorted({"surf": 100.0, "ivycudgel": 50.0})
        assert break_repeated_decision(policy, None) == policy

    def test_empty_policy_returns_unchanged(self):
        battle = _battle(["surf", "surf", "surf"])
        assert break_repeated_decision([], battle) == []


class TestMctsBackedSelection:
    def test_mcts_policy_bypasses_soft_heuristic_pipeline_by_default(self):
        trace = {}
        policy = {"roost": 100.0, "bodypress": 50.0}

        with patch(
            "fp.search.main.apply_heuristic_bias",
            side_effect=AssertionError("soft heuristic pipeline should be gated"),
        ) as soft_pipeline:
            choice = select_move_from_eval_scores(
                policy,
                decision_profile=DecisionProfile.LOW,
                trace=trace,
                policy_source="mcts",
            )

        assert choice == "roost"
        soft_pipeline.assert_not_called()
        assert trace["mcts_backed"]["penalty_pipeline_enabled"] is False
        assert trace["decision_mode_detail"] == "mcts_backed_hard_safety"

    def test_last_pokemon_pivot_is_demoted_below_real_attack(self):
        battle = _battle([])
        battle.force_switch = False
        battle.request_json = {"active": [{}]}
        battle.user.trapped = False
        ability_state = SimpleNamespace(our_alive_count=1)
        trace_events = []

        result = _apply_hard_legality_and_safety(
            {"uturn": 0.824, "ivycudgel": 0.114, "knockoff": 0.046, "encore": 0.016},
            battle=battle,
            ability_state=ability_state,
            trace_events=trace_events,
        )

        assert result[0][0] == "ivycudgel"
        assert dict(result)["uturn"] < dict(result)["ivycudgel"]
        assert any(
            event["source"] == "pivot_safety"
            and event["reason"] == "pivot_last_pokemon_no_switch_target"
            for event in trace_events
        )

    def test_pivot_is_not_demoted_when_teammates_remain(self):
        battle = _battle([])
        battle.force_switch = False
        battle.request_json = {"active": [{}]}
        battle.user.trapped = False
        ability_state = SimpleNamespace(our_alive_count=3)

        result = _apply_hard_legality_and_safety(
            {"uturn": 0.824, "ivycudgel": 0.114},
            battle=battle,
            ability_state=ability_state,
            trace_events=[],
        )

        assert result[0][0] == "uturn"
