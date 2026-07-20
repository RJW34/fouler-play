"""Discriminating regression test for the consecutive-Protect failure guard.

Reconstructed from a REAL loss, not a synthetic scenario:

    replay gen9ou-2652198322 (DekuFoulerFresh = p2), turns 27-29

        |turn|27  |move|p2a: Gliscor|Protect  -> |-activate| (success #1)
        |turn|28  |move|p2a: Gliscor|Protect  -> |-activate| (success #2)
        |turn|29  |move|p2a: Gliscor|Protect||[still]
                  |-fail|p2a: Gliscor
                  |move|p1a: Gholdengo|Make It Rain|p2a: Gliscor
                  |-crit| |-damage|p2a: Gliscor|0 fnt |faint|p2a: Gliscor
                  |win|Imnotgcb
                  DekuFoulerFresh's rating: 1303 -> 1285 (-18 for losing)

The third consecutive Protect had a 1/9 success probability. It failed, Gliscor
(at 29/100 HP) took a critical Make It Rain, fainted, and the battle was lost on
that move. The mechanical error is that the state serialized to the search engine
carries no stall counter, so MCTS valued the repeat Protect as a certainty.

Corpus rate at the time of the fix: 38 failed consecutive-Protect turns across
189 replays (20.1 per 100 battles), 18 of them inside losses.
"""

import unittest
from collections import defaultdict
from types import SimpleNamespace

import constants
from fp.search.main import (
    DecisionProfile,
    OpponentAbilityState,
    select_move_from_eval_scores,
)


def _mk_move(name: str):
    return SimpleNamespace(name=name)


def _mk_gliscor_battle(protect_streak: int, last_move: str):
    """Reconstruct the p2 side of gen9ou-2652198322 at its turn-29 decision."""
    active = SimpleNamespace(
        name="gliscor",
        base_name="gliscor",
        hp=29,
        max_hp=100,
        ability="poisonheal",
        moves=[_mk_move(m) for m in ("protect", "earthquake", "knockoff", "toxic")],
        base_stats={
            constants.HITPOINTS: 75,
            constants.DEFENSE: 125,
            constants.SPECIAL_DEFENSE: 75,
        },
        boosts={},
        volatile_statuses=[],
    )
    side_conditions = defaultdict(int)
    side_conditions[constants.PROTECT] = protect_streak
    user = SimpleNamespace(
        active=active,
        reserve=[],
        side_conditions=side_conditions,
        trapped=False,
        last_selected_move=SimpleNamespace(move=last_move, pokemon_name="gliscor"),
        last_used_move=SimpleNamespace(move=last_move, pokemon_name="gliscor"),
    )
    opponent = SimpleNamespace(
        active=SimpleNamespace(
            name="gholdengo",
            hp=100,
            max_hp=100,
            boosts={},
            volatile_statuses=[],
        ),
        reserve=[],
        side_conditions=defaultdict(int),
    )
    return SimpleNamespace(
        user=user,
        opponent=opponent,
        force_switch=False,
        turn=29,
        request_json=None,
    )


class TestConsecutiveProtectGuard(unittest.TestCase):
    def _choose(self, battle, policy):
        trace = {}
        choice = select_move_from_eval_scores(
            policy,
            ability_state=OpponentAbilityState(),
            battle=battle,
            decision_profile=DecisionProfile.LOW,
            trace=trace,
            policy_source="mcts",
        )
        return choice, trace

    def test_third_consecutive_protect_is_not_selected(self):
        """The exact turn-29 decision that lost gen9ou-2652198322.

        Without the guard the MCTS argmax is `protect` and this test fails.
        """
        battle = _mk_gliscor_battle(protect_streak=2, last_move="protect")
        # MCTS strongly prefers protect because it models it as always succeeding.
        policy = {"protect": 0.75, "earthquake": 0.15, "knockoff": 0.07, "toxic": 0.03}

        choice, trace = self._choose(battle, policy)

        self.assertNotEqual(
            choice, "protect",
            "third consecutive Protect (p=1/9) must not be selected over a real move",
        )
        self.assertEqual(choice, "earthquake")

        event = next(
            e for e in trace["mcts_only"]["events"]
            if e.get("reason") == "consecutive_protect_fail_risk"
        )
        self.assertEqual(event["protectStreak"], 2)
        self.assertAlmostEqual(event["successProbability"], 1.0 / 9.0, places=4)
        # Capped strictly below the best real alternative.
        self.assertLess(event["after"], 0.15)

    def test_second_consecutive_protect_is_also_demoted(self):
        """One prior success => 1/3 success chance, still dominated."""
        battle = _mk_gliscor_battle(protect_streak=1, last_move="protect")
        policy = {"protect": 0.60, "earthquake": 0.30, "knockoff": 0.10}

        choice, trace = self._choose(battle, policy)

        self.assertEqual(choice, "earthquake")
        event = next(
            e for e in trace["mcts_only"]["events"]
            if e.get("reason") == "consecutive_protect_fail_risk"
        )
        self.assertAlmostEqual(event["successProbability"], 1.0 / 3.0, places=4)

    def test_first_protect_is_untouched(self):
        """No streak => Protect always succeeds => the guard must not fire.

        This is the over-correction guard: Protect is a legitimate stall-team move
        and the fix must not suppress its first use.
        """
        battle = _mk_gliscor_battle(protect_streak=0, last_move="earthquake")
        policy = {"protect": 0.75, "earthquake": 0.15, "knockoff": 0.10}

        choice, trace = self._choose(battle, policy)

        self.assertEqual(choice, "protect")
        self.assertFalse(
            [e for e in trace["mcts_only"]["events"]
             if e.get("reason") == "consecutive_protect_fail_risk"],
            "guard must not fire when the Pokemon did not just use Protect",
        )

    def test_streak_broken_by_a_different_move_is_untouched(self):
        """A stale nonzero side-condition counter must not penalize a broken streak."""
        battle = _mk_gliscor_battle(protect_streak=2, last_move="earthquake")
        policy = {"protect": 0.75, "earthquake": 0.15, "knockoff": 0.10}

        choice, trace = self._choose(battle, policy)

        self.assertEqual(choice, "protect")
        self.assertFalse(
            [e for e in trace["mcts_only"]["events"]
             if e.get("reason") == "consecutive_protect_fail_risk"]
        )

    def test_protect_survives_when_it_is_the_only_option(self):
        """Guard demotes, it must never make a legal move unreachable."""
        battle = _mk_gliscor_battle(protect_streak=2, last_move="protect")
        choice, _ = self._choose(battle, {"protect": 0.75})
        self.assertEqual(choice, "protect")


if __name__ == "__main__":
    unittest.main()
