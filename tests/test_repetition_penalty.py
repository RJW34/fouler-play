"""Tests for apply_repetition_penalty — detect and penalize repeated actions."""

import pytest
from unittest.mock import MagicMock

from fp.search.main import apply_repetition_penalty


def _make_battle_with_history(history: list[str], last_selected_move: str | None = None):
    """Create a minimal battle mock with action_history on user."""
    battle = MagicMock()
    battle.user.action_history = list(history)
    if last_selected_move is None:
        battle.user.last_selected_move = None
    else:
        battle.user.last_selected_move = MagicMock(move=last_selected_move)
    return battle


class TestRepetitionPenalty:
    """Tests for the repetition penalty pipeline step."""

    def test_no_penalty_with_short_history(self):
        """No penalty when fewer than 3 actions in history."""
        battle = _make_battle_with_history(["recover", "recover"])
        policy = {"recover": 100.0, "shadowball": 80.0}
        result = apply_repetition_penalty(policy, battle)
        assert result["recover"] == 100.0
        assert result["shadowball"] == 80.0

    def test_penalizes_3x_repeat(self):
        """3 repeats of same move in last 6 → 0.6x penalty."""
        battle = _make_battle_with_history([
            "recover", "shadowball", "recover", "recover", "shadowball", "recover",
        ])
        policy = {"recover": 100.0, "shadowball": 80.0}
        trace = []
        result = apply_repetition_penalty(policy, battle, trace_events=trace)
        # recover appears 4 times in last 6 → 0.35x penalty
        assert result["recover"] < 100.0
        assert result["recover"] == pytest.approx(35.0, abs=1.0)
        assert result["shadowball"] == 80.0  # not penalized (only 2x)
        assert len(trace) >= 1
        assert trace[0]["source"] == "repetition_detection"

    def test_penalizes_5x_repeat_severely(self):
        """5+ repeats → 0.15x penalty."""
        battle = _make_battle_with_history([
            "recover", "recover", "recover", "recover", "recover", "recover",
        ])
        policy = {"recover": 100.0, "shadowball": 80.0}
        result = apply_repetition_penalty(policy, battle)
        assert result["recover"] == pytest.approx(15.0, abs=1.0)

    def test_floor_at_10_percent(self):
        """Never penalize below 10% of original weight."""
        battle = _make_battle_with_history([
            "recover", "recover", "recover", "recover", "recover", "recover",
        ])
        policy = {"recover": 50.0, "shadowball": 80.0}
        result = apply_repetition_penalty(policy, battle)
        # 50 * 0.15 = 7.5, but floor is 50 * 0.1 = 5.0; 7.5 > 5.0 so no floor
        assert result["recover"] == pytest.approx(7.5, abs=0.5)

    def test_switch_oscillation_detected(self):
        """Detects A→B→A→B switch oscillation pattern."""
        battle = _make_battle_with_history([
            "switch skarmory",
            "shadowball",
            "switch skarmory",
            "recover",
            "switch skarmory",
            "shadowball",
        ])
        policy = {
            "switch skarmory": 90.0,
            "switch blissey": 85.0,
            "recover": 70.0,
        }
        trace = []
        result = apply_repetition_penalty(policy, battle, trace_events=trace)
        # "switch skarmory" appears 3 times → both repetition and oscillation penalty
        assert result["switch skarmory"] < 90.0
        assert result["switch blissey"] == 85.0  # not penalized

    def test_no_penalty_for_varied_play(self):
        """No penalty when actions are varied."""
        battle = _make_battle_with_history([
            "shadowball", "recover", "switch skarmory",
            "toxic", "shadowball", "switch blissey",
        ])
        policy = {
            "shadowball": 90.0,
            "recover": 80.0,
            "switch skarmory": 70.0,
        }
        result = apply_repetition_penalty(policy, battle)
        assert result["shadowball"] == 90.0
        assert result["recover"] == 80.0
        assert result["switch skarmory"] == 70.0

    def test_last_selected_move_triggers_alternating_loop_penalty(self):
        """Use last_selected_move so pending protect/attack loops get penalized before logging catches up."""
        battle = _make_battle_with_history(
            ["earthquake", "protect", "earthquake"],
            last_selected_move="protect",
        )
        policy = {
            "protect": 100.0,
            "earthquake": 95.0,
            "swordsdance": 60.0,
        }
        trace = []
        result = apply_repetition_penalty(policy, battle, trace_events=trace)
        assert result["protect"] == pytest.approx(55.0, abs=1.0)
        assert result["earthquake"] == 95.0
        assert any(event["source"] == "alternating_loop" for event in trace)

    def test_none_battle_returns_unchanged(self):
        """Returns unchanged policy when battle is None."""
        policy = {"recover": 100.0}
        result = apply_repetition_penalty(policy, None)
        assert result == policy

    def test_missing_action_history_returns_unchanged(self):
        """Returns unchanged policy when action_history is missing."""
        battle = MagicMock(spec=[])
        battle.user = MagicMock(spec=[])
        policy = {"recover": 100.0}
        result = apply_repetition_penalty(policy, battle)
        assert result == policy


class TestStagnationSwitchBoost:
    """Tests for the stagnation switch boost that addresses bugs #3, #5, #7."""

    def _make_battle(self, history, opp_types=None):
        """Create a battle mock with action history and opponent types."""
        battle = _make_battle_with_history(history)
        if opp_types is not None:
            battle.opponent.active.types = opp_types
            battle.opponent.active.terastallized = False
            battle.opponent.active.tera_type = None
        else:
            battle.opponent = None
        return battle

    def test_resisted_move_spam_boosts_switches(self):
        """Bug #3: Repeated resisted move (Ghost into Dark) boosts switch options."""
        battle = self._make_battle(
            ["hex", "hex", "hex", "spikes", "hex", "hex"],
            opp_types=["dark", "ground"],
        )
        policy = {
            "hex": 60.0,
            "spikes": 40.0,
            "switch skarmory": 50.0,
            "switch blissey": 45.0,
        }
        trace = []
        result = apply_repetition_penalty(policy, battle, trace_events=trace)
        # hex should be penalized by repetition AND switches should be boosted
        assert result["hex"] < 60.0
        assert result["switch skarmory"] > 50.0
        assert result["switch blissey"] > 45.0
        assert any(e["source"] == "stagnation_switch_boost" for e in trace)

    def test_recovery_loop_boosts_switches(self):
        """Bug #5: Repeated Recover boosts switch options."""
        battle = self._make_battle(
            ["recover", "recover", "recover", "seismictoss", "recover"],
            opp_types=["fighting"],
        )
        policy = {
            "recover": 80.0,
            "seismictoss": 50.0,
            "switch gliscor": 55.0,
            "switch skarmory": 48.0,
        }
        trace = []
        result = apply_repetition_penalty(policy, battle, trace_events=trace)
        assert result["recover"] < 80.0  # repetition penalty
        assert result["switch gliscor"] > 55.0  # stagnation boost
        assert result["switch skarmory"] > 48.0
        assert any(e["source"] == "stagnation_switch_boost" for e in trace)

    def test_switch_oscillation_boosts_attacks(self):
        """Bug #7: Switch oscillation boosts attack options."""
        battle = self._make_battle(
            ["switch blissey", "switch corviknight", "switch blissey",
             "switch corviknight", "switch blissey"],
            opp_types=["dragon", "normal"],
        )
        policy = {
            "toxic": 40.0,
            "recover": 35.0,
            "switch blissey": 80.0,
            "switch corviknight": 75.0,
        }
        trace = []
        result = apply_repetition_penalty(policy, battle, trace_events=trace)
        assert result["toxic"] > 40.0  # stagnation attack boost
        assert result["recover"] > 35.0
        assert any(e["source"] == "stagnation_attack_boost" for e in trace)

    def test_no_boost_when_varied_play(self):
        """No stagnation boost when play is varied."""
        battle = self._make_battle(
            ["shadowball", "recover", "toxic", "switch skarmory", "spikes"],
            opp_types=["water"],
        )
        policy = {
            "shadowball": 90.0,
            "switch skarmory": 70.0,
        }
        result = apply_repetition_penalty(policy, battle)
        assert result["shadowball"] == 90.0
        assert result["switch skarmory"] == 70.0

    def test_neutral_move_spam_does_not_boost_switches(self):
        """Repeated neutral (1.0x) move doesn't trigger stagnation boost."""
        battle = self._make_battle(
            ["shadowball", "shadowball", "shadowball", "shadowball"],
            opp_types=["water"],  # Ghost is neutral vs Water
        )
        policy = {
            "shadowball": 90.0,
            "switch skarmory": 70.0,
        }
        trace = []
        result = apply_repetition_penalty(policy, battle, trace_events=trace)
        # Repetition penalty still fires, but no switch boost
        assert result["shadowball"] < 90.0
        assert not any(e["source"] == "stagnation_switch_boost" for e in trace)
