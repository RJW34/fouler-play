"""Tests for break_repeated_decision -- the hard decision_instability loop-breaker.

2026-07-04: the breaker was trace-proven to demote correct repeated play on stall
teams (249/265 = 94% of played-vs-policy inversions in the loss corpus, e.g.
gen9ou-2643766855 t20 where protect 0.751 was demoted to 0.039). It is now:
  - gated by FOULER_LOOP_BREAK (default 1 = enabled; 0 = no-op, search trusted)
  - guarded when enabled: never fires against a decisive search, and requires
    provable position stagnation (identical board fingerprints) over the
    repetition window. The last-mon damage-progress exemption is retained.
"""

from unittest.mock import MagicMock

import pytest

from fp.search.main import (
    _position_fingerprint,
    _position_stagnant,
    _recent_action_history,
    break_repeated_decision,
)


@pytest.fixture(autouse=True)
def _enable_loop_breaker(monkeypatch):
    monkeypatch.setenv("FOULER_LOOP_BREAK", "1")


def _pokemon(name="blissey", *, hp=100, fainted=False):
    pkmn = MagicMock()
    pkmn.name = name
    pkmn.hp = hp
    pkmn.fainted = fainted
    return pkmn


def _battle(history, last_selected_move=None, *, turn=20):
    battle = MagicMock()
    battle.turn = turn
    battle.user.action_history = list(history)
    if last_selected_move is None:
        battle.user.last_selected_move = None
    else:
        battle.user.last_selected_move = MagicMock(move=last_selected_move)
    battle.user.active = _pokemon("gliscor", hp=211)
    battle.user.reserve = [
        _pokemon("blissey", hp=652),
        _pokemon("skarmory", hp=269),
    ]
    battle.opponent.active = _pokemon("corviknight", hp=329)
    battle.opponent.reserve = [_pokemon("kingambit", hp=362)]
    return battle


def _last_mon_battle(history):
    battle = _battle(history)
    battle.user.active = _pokemon("pecharunt", hp=97)
    battle.user.reserve = [_pokemon(hp=0, fainted=True) for _ in range(5)]
    return battle


def _stagnate(battle, n=5):
    """Seed a fingerprint history proving the board has not changed for n turns."""
    fp = _position_fingerprint(battle)
    assert fp is not None, "test battle must produce a readable fingerprint"
    battle._loop_break_fp_history = [(battle.turn - i, fp) for i in range(n, 0, -1)]
    return battle


def _progressing(battle, n=5):
    """Seed a fingerprint history where opponent HP dropped every recent turn."""
    fp = _position_fingerprint(battle)
    assert fp is not None
    battle._loop_break_fp_history = [
        (battle.turn - i, (fp[0], fp[1], fp[2], fp[3] + 25.0 * i))
        for i in range(n, 0, -1)
    ]
    return battle


def _sorted(policy: dict) -> list[tuple[str, float]]:
    return sorted(policy.items(), key=lambda x: x[1], reverse=True)


# Non-decisive policy shape: best holds 40% of mass and only 1.25x the runner-up,
# so neither decisive-search condition applies and the guards under test are the
# stagnation requirement / repetition logic themselves.
CLOSE_POLICY = {"seismictoss": 0.40, "softboiled": 0.32, "switch blissey": 0.28}


class TestRecentActionHistory:
    def test_appends_pending_last_selected_move(self):
        battle = _battle(["seismictoss", "softboiled"], last_selected_move="seismictoss")
        assert _recent_action_history(battle) == ["seismictoss", "softboiled", "seismictoss"]

    def test_does_not_duplicate_when_history_already_has_last(self):
        battle = _battle(["seismictoss", "softboiled"], last_selected_move="softboiled")
        assert _recent_action_history(battle) == ["seismictoss", "softboiled"]

    def test_none_battle(self):
        assert _recent_action_history(None) == []


class TestKillSwitch:
    def test_disabled_is_identity_even_on_stagnant_repetition(self, monkeypatch):
        monkeypatch.setenv("FOULER_LOOP_BREAK", "0")
        battle = _stagnate(_battle(["seismictoss"] * 3))
        policy = _sorted(CLOSE_POLICY)
        trace = []
        result = break_repeated_decision(policy, battle, trace_events=trace)
        assert result == policy
        assert trace == []

    def test_disabled_is_identity_for_cycles(self, monkeypatch):
        monkeypatch.setenv("FOULER_LOOP_BREAK", "0")
        battle = _stagnate(_battle(["seismictoss", "softboiled"] * 2))
        policy = _sorted(CLOSE_POLICY)
        result = break_repeated_decision(policy, battle)
        assert result == policy

    def test_default_is_enabled(self, monkeypatch):
        monkeypatch.delenv("FOULER_LOOP_BREAK", raising=False)
        battle = _stagnate(_battle(["seismictoss"] * 3))
        result = break_repeated_decision(_sorted(CLOSE_POLICY), battle)
        assert result[0][0] == "softboiled"


class TestDecisiveSearchGuard:
    def test_skips_when_best_holds_decisive_policy_mass(self):
        battle = _stagnate(_battle(["protect"] * 3))
        policy = _sorted({"protect": 0.60, "earthquake": 0.25, "knockoff": 0.15})
        trace = []
        result = break_repeated_decision(policy, battle, trace_events=trace)
        assert result[0] == ("protect", 0.60)
        assert any(
            e["type"] == "skip" and "search_decisive" in e["reason"] for e in trace
        )

    def test_skips_when_best_dominates_runner_up(self):
        # Best is only ~39% of mass (below the absolute bar) but 3x the runner-up.
        battle = _stagnate(_battle(["protect"] * 3))
        policy = {"protect": 0.30, "knockoff": 0.10}
        for i in range(12):
            policy[f"switch mon{i}"] = 0.03
        result = break_repeated_decision(_sorted(policy), battle, trace_events=[])
        assert result[0] == ("protect", 0.30)

    def test_close_search_is_not_decisive(self):
        battle = _stagnate(_battle(["seismictoss"] * 3))
        result = break_repeated_decision(_sorted(CLOSE_POLICY), battle)
        assert result[0][0] == "softboiled"


class TestStagnationGuard:
    def test_no_break_when_position_progressing(self):
        # Salt-cure-style plan: opponent HP dropping every turn while we repeat.
        battle = _progressing(_battle(["saltcure"] * 3))
        policy = _sorted({"saltcure": 0.40, "recover": 0.32, "switch blissey": 0.28})
        trace = []
        result = break_repeated_decision(policy, battle, trace_events=trace)
        assert result[0] == ("saltcure", 0.40)
        assert any(
            e["type"] == "skip" and "position_not_stagnant" in e["reason"]
            for e in trace
        )

    def test_no_break_without_fingerprint_history(self):
        # Fresh battle object: only the current turn's fingerprint exists, so
        # stagnation is unproven and the breaker must not fire.
        battle = _battle(["seismictoss"] * 3)
        battle._loop_break_fp_history = None
        result = break_repeated_decision(_sorted(CLOSE_POLICY), battle)
        assert result[0][0] == "seismictoss"

    def test_no_break_when_fingerprint_unreadable(self):
        battle = _battle(["seismictoss"] * 3)
        battle._loop_break_fp_history = [(17, None), (18, None), (19, None)]
        battle.opponent = None  # current fingerprint unreadable too
        result = break_repeated_decision(_sorted(CLOSE_POLICY), battle)
        assert result[0][0] == "seismictoss"

    def test_breaks_when_position_stagnant(self):
        battle = _stagnate(_battle(["seismictoss"] * 3))
        trace = []
        result = break_repeated_decision(_sorted(CLOSE_POLICY), battle, trace_events=trace)
        assert result[0][0] == "softboiled"
        assert any(
            e["type"] == "override" and e["source"] == "decision_loop_break"
            for e in trace
        )

    def test_position_stagnant_helper(self):
        fp = ("a", 100.0, "b", 200.0)
        other = ("a", 90.0, "b", 200.0)
        assert _position_stagnant([(1, fp), (2, fp), (3, fp)])
        assert not _position_stagnant([(1, fp), (2, other), (3, fp)])
        assert not _position_stagnant([(2, fp), (3, fp)])  # too short
        assert not _position_stagnant([(1, fp), (2, None), (3, fp)])
        assert not _position_stagnant([])


class TestBreakRepeatedDecision:
    def test_demotes_repeated_best_move_below_distinct_alternative(self):
        # seismictoss chosen 3x recently with a provably static board and a close
        # policy; the loop-breaker demotes it below the distinct alternative.
        battle = _stagnate(_battle(["seismictoss", "seismictoss", "seismictoss"]))
        policy = _sorted(CLOSE_POLICY)
        trace = []
        result = break_repeated_decision(policy, battle, trace_events=trace)
        assert result[0][0] != "seismictoss"
        assert result[0][0] == "softboiled"  # best distinct alternative now leads
        assert any(e["source"] == "decision_loop_break" for e in trace)

    def test_no_change_when_not_repeated_enough(self):
        battle = _stagnate(_battle(["seismictoss", "softboiled", "switch blissey"]))
        policy = _sorted({"seismictoss": 0.40, "softboiled": 0.32, "toxic": 0.28})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] == "seismictoss"

    def test_no_change_when_no_distinct_alternative(self):
        # Only the repeated move has positive weight -> it is forced, keep it.
        battle = _stagnate(_battle(["shadowball", "shadowball", "shadowball"]))
        policy = _sorted({"shadowball": 0.40, "switch x": 0.0})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] == "shadowball"

    def test_demoted_move_remains_present_as_last_resort(self):
        battle = _stagnate(_battle(["surf", "surf", "surf"]))
        policy = _sorted({"surf": 0.40, "ivycudgel": 0.33, "icebeam": 0.27})
        result = break_repeated_decision(policy, battle)
        moves = [m for m, _ in result]
        assert "surf" in moves  # never removed, only demoted

    def test_switch_loop_demoted(self):
        # Repeated switch target should also be broken.
        battle = _stagnate(
            _battle(["switch blissey", "switch blissey", "switch blissey"])
        )
        policy = _sorted(
            {"switch blissey": 0.38, "sludgebomb": 0.33, "switch dondozo": 0.29}
        )
        result = break_repeated_decision(policy, battle)
        assert result[0][0] != "switch blissey"

    def test_last_mon_repeated_damage_is_not_forced_to_weaker_attack(self):
        battle = _stagnate(
            _last_mon_battle(["malignantchain", "malignantchain", "malignantchain"])
        )
        policy = _sorted({"malignantchain": 0.34, "foulplay": 0.28, "partingshot": 0.20})
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
        battle.user.active = _pokemon("pecharunt", hp=97)
        battle.user.reserve = [_pokemon("blissey", hp=100, fainted=False)]
        _stagnate(battle)
        policy = _sorted({"malignantchain": 0.34, "foulplay": 0.28, "partingshot": 0.20})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] != "malignantchain"

    def test_last_mon_repeated_recovery_can_still_be_broken(self):
        battle = _stagnate(_last_mon_battle(["recover", "recover", "recover"]))
        policy = _sorted({"recover": 0.40, "foulplay": 0.35, "shadowball": 0.25})
        result = break_repeated_decision(policy, battle)
        assert result[0][0] == "foulplay"

    def test_none_battle_returns_unchanged(self):
        policy = _sorted({"surf": 0.60, "ivycudgel": 0.40})
        assert break_repeated_decision(policy, None) == policy

    def test_empty_policy_returns_unchanged(self):
        battle = _battle(["surf", "surf", "surf"])
        assert break_repeated_decision([], battle) == []


class TestGen9ou2643766855Turn20Regression:
    """Exact policy shape from logs/decision_traces/battle-gen9ou-2643766855-*
    _turn20_*.json -- the trace-proven defect: SD-Gliscor stall wincon where the
    search put 75.1% on protect and the unguarded breaker demoted it to 0.039
    ("protect_repeated_3_in_last_6_forcing_distinct"), playing earthquake into a
    losing line. protect must survive the loop-breaker in every mode."""

    POLICY = {
        "earthquake": 0.07801180362199853,
        "knockoff": 0.07652505175983437,
        "protect": 0.7512681754360916,
        "switch blissey": 0.0034471335760691084,
        "switch gholdengo": 0.009507155945836607,
        "switch pecharunt": 0.006535408486233075,
        "switch skarmory": 0.002712981604435878,
        "switch zamazenta": 0.0026158634968230176,
        "swordsdance": 0.06937642607267795,
    }

    def _t20_battle(self):
        battle = MagicMock()
        battle.turn = 20
        # protect repeated 3x in the last 6 actions, exactly the trace's trigger.
        battle.user.action_history = [
            "protect",
            "earthquake",
            "protect",
            "swordsdance",
            "protect",
        ]
        battle.user.last_selected_move = None
        battle.user.active = _pokemon("gliscor", hp=211)
        battle.user.reserve = [
            _pokemon("zamazenta", hp=325),
            _pokemon("blissey", hp=652),
            _pokemon("pecharunt", hp=380),
            _pokemon("skarmory", hp=269),
            _pokemon("gholdengo", hp=222),
        ]
        battle.opponent.active = _pokemon("corviknight", hp=329.36)
        battle.opponent.reserve = [
            _pokemon("blaziken", hp=322),
            _pokemon("dragapult", hp=338),
            _pokemon("kingambit", hp=362),
            _pokemon("landorustherian", hp=210.8),
            _pokemon("clodsire", hp=422.0),
        ]
        return battle

    def test_protect_survives_decisive_guard_even_when_stagnant(self):
        battle = _stagnate(self._t20_battle())
        trace = []
        result = break_repeated_decision(
            _sorted(dict(self.POLICY)), battle, trace_events=trace
        )
        assert result[0] == ("protect", self.POLICY["protect"])
        assert any(
            e["type"] == "skip" and "search_decisive" in e["reason"] for e in trace
        )

    def test_protect_survives_live_shape_without_seeded_history(self):
        # Live shape: toxic chip moves HP every turn, so stagnation never holds
        # either -- but the decisive guard alone must already protect the wincon.
        battle = self._t20_battle()
        result = break_repeated_decision(_sorted(dict(self.POLICY)), battle)
        assert result[0] == ("protect", self.POLICY["protect"])

    def test_kill_switch_off_is_identity(self, monkeypatch):
        monkeypatch.setenv("FOULER_LOOP_BREAK", "0")
        battle = _stagnate(self._t20_battle())
        policy = _sorted(dict(self.POLICY))
        assert break_repeated_decision(policy, battle) == policy
