"""
Tests for opponent response prediction module.

Tests:
- Predict stays when opponent is trapped
- Predict stays when opponent has no reserves
- Predict switch when opponent is in bad matchup
- Counter-signal: boosted opponent stays
- Switch target prediction prefers type-resistant Pokemon
- After-KO switch-in prediction
"""

import unittest
from unittest.mock import MagicMock
from collections import defaultdict

import constants


def _make_pokemon(
    name="testmon",
    hp=100,
    max_hp=100,
    types=None,
    stats=None,
    moves=None,
    boosts=None,
    item="",
    ability="",
    status=None,
    volatile_statuses=None,
    fainted=False,
):
    pkmn = MagicMock()
    pkmn.name = name
    pkmn.hp = hp
    pkmn.max_hp = max_hp
    pkmn.types = types or ["normal"]
    pkmn.stats = stats or {
        constants.ATTACK: 100,
        constants.DEFENSE: 100,
        constants.SPECIAL_ATTACK: 100,
        constants.SPECIAL_DEFENSE: 100,
        constants.SPEED: 100,
        constants.HITPOINTS: max_hp,
    }
    pkmn.boosts = defaultdict(int, boosts or {})
    pkmn.item = item
    pkmn.ability = ability
    pkmn.status = status
    pkmn.volatile_statuses = volatile_statuses or []
    pkmn.fainted = fainted
    pkmn.terastallized = False
    pkmn.tera_type = None

    mock_moves = []
    for m in (moves or []):
        move_obj = MagicMock()
        if isinstance(m, str):
            move_obj.name = m
            move_obj.disabled = False
            move_obj.current_pp = 10
        elif isinstance(m, dict):
            move_obj.name = m.get("name", "tackle")
            move_obj.disabled = m.get("disabled", False)
            move_obj.current_pp = m.get("pp", 10)
        mock_moves.append(move_obj)
    pkmn.moves = mock_moves

    return pkmn


def _make_battler(active=None, reserve=None, side_conditions=None):
    battler = MagicMock()
    battler.active = active
    battler.reserve = reserve or []
    battler.side_conditions = defaultdict(int, side_conditions or {})
    return battler


def _make_battle(
    user_active=None,
    user_reserve=None,
    opp_active=None,
    opp_reserve=None,
    user_side_conditions=None,
    opp_side_conditions=None,
):
    battle = MagicMock()
    battle.user = _make_battler(
        active=user_active,
        reserve=user_reserve or [],
        side_conditions=user_side_conditions,
    )
    battle.opponent = _make_battler(
        active=opp_active,
        reserve=opp_reserve or [],
        side_conditions=opp_side_conditions,
    )
    return battle


class TestPredictOpponentAction(unittest.TestCase):

    def test_trapped_opponent_stays(self):
        """Trapped opponent should always be predicted to stay."""
        from fp.search.opponent_predict import predict_opponent_action

        our = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake"],
            stats={
                constants.ATTACK: 300, constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 100, constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 333, constants.HITPOINTS: 357,
            },
            hp=357, max_hp=357,
        )
        opp = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            moves=["magmastorm"],
            hp=50, max_hp=386,
            volatile_statuses=["partiallytrapped"],
        )
        reserve = _make_pokemon(name="slowking", hp=300, max_hp=300)
        battle = _make_battle(
            user_active=our, opp_active=opp, opp_reserve=[reserve],
        )

        result = predict_opponent_action(battle)
        self.assertEqual(result.action, "stays")
        self.assertEqual(result.confidence, 1.0)

    def test_no_reserves_stays(self):
        """Opponent with no alive reserves must stay."""
        from fp.search.opponent_predict import predict_opponent_action

        our = _make_pokemon(name="garchomp", types=["dragon", "ground"], moves=["earthquake"])
        opp = _make_pokemon(name="heatran", types=["fire", "steel"], moves=["magmastorm"], hp=100, max_hp=386)
        # All reserves are fainted
        dead_reserve = _make_pokemon(name="slowking", hp=0, max_hp=300, fainted=True)
        battle = _make_battle(user_active=our, opp_active=opp, opp_reserve=[dead_reserve])

        result = predict_opponent_action(battle)
        self.assertEqual(result.action, "stays")
        self.assertEqual(result.confidence, 1.0)

    def test_bad_matchup_predicts_switch(self):
        """Opponent in terrible matchup should be predicted to switch."""
        from fp.search.opponent_predict import predict_opponent_action

        # Our Pokemon: strong fire type with powerful fire moves
        our = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            moves=["lavaplume", "earthpower"],
            stats={
                constants.ATTACK: 90, constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 300, constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 180, constants.HITPOINTS: 386,
            },
            hp=386, max_hp=386,
        )
        # Opponent: weak bug type that can't touch us
        opp = _make_pokemon(
            name="scizor",
            types=["bug", "steel"],
            moves=["bulletpunch"],  # steel vs steel+fire = minimal damage
            stats={
                constants.ATTACK: 200, constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 55, constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 150, constants.HITPOINTS: 344,
            },
            hp=344, max_hp=344,
        )
        reserve1 = _make_pokemon(name="gastrodon", types=["water", "ground"], hp=300, max_hp=300)
        reserve2 = _make_pokemon(name="slowking", types=["water", "psychic"], hp=300, max_hp=300)
        battle = _make_battle(
            user_active=our, opp_active=opp,
            opp_reserve=[reserve1, reserve2],
        )

        result = predict_opponent_action(battle)
        self.assertEqual(result.action, "switches")
        self.assertGreater(result.confidence, 0.50)

    def test_boosted_opponent_stays(self):
        """Boosted opponent should be predicted to stay despite bad type matchup."""
        from fp.search.opponent_predict import predict_opponent_action

        our = _make_pokemon(
            name="hippowdon",
            types=["ground"],
            moves=["earthquake"],
            stats={
                constants.ATTACK: 250, constants.DEFENSE: 350,
                constants.SPECIAL_ATTACK: 68, constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 97, constants.HITPOINTS: 420,
            },
            hp=420, max_hp=420,
        )
        # Opponent has boosted — even if matchup isn't great, they invested
        opp = _make_pokemon(
            name="dragapult",
            types=["dragon", "ghost"],
            moves=["dragondarts", "phantomforce"],
            stats={
                constants.ATTACK: 240, constants.DEFENSE: 170,
                constants.SPECIAL_ATTACK: 200, constants.SPECIAL_DEFENSE: 170,
                constants.SPEED: 420, constants.HITPOINTS: 290,
            },
            hp=290, max_hp=290,
            boosts={constants.ATTACK: 2, constants.SPEED: 2},
        )
        reserve = _make_pokemon(name="slowking", hp=300, max_hp=300)
        battle = _make_battle(
            user_active=our, opp_active=opp, opp_reserve=[reserve],
        )

        result = predict_opponent_action(battle)
        self.assertEqual(result.action, "stays")

    def test_switch_target_prefers_resist(self):
        """Switch target prediction should prefer Pokemon that resist our STAB."""
        from fp.search.opponent_predict import _predict_switch_target

        our = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake", "outrage"],
            hp=357, max_hp=357,
        )
        # Reserve 1: weak to ground (fire type)
        weak_reserve = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            hp=300, max_hp=386,
            moves=["magmastorm"],
        )
        # Reserve 2: resists ground (flying type)
        resist_reserve = _make_pokemon(
            name="skarmory",
            types=["steel", "flying"],
            hp=334, max_hp=334,
            moves=["bravebird"],
        )
        battle = _make_battle(
            user_active=our,
            opp_reserve=[weak_reserve, resist_reserve],
        )

        target = _predict_switch_target(battle)
        self.assertEqual(target, "skarmory")

    def test_after_ko_switchin(self):
        """After-KO switch-in should use same logic as switch target."""
        from fp.search.opponent_predict import predict_after_ko_switchin

        our = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake"],
            hp=357, max_hp=357,
        )
        reserve = _make_pokemon(
            name="corviknight",
            types=["steel", "flying"],
            hp=399, max_hp=399,
        )
        battle = _make_battle(
            user_active=our,
            opp_reserve=[reserve],
        )

        target = predict_after_ko_switchin(battle)
        self.assertEqual(target, "corviknight")


class TestSwitchTargetScoring(unittest.TestCase):

    def test_fainted_reserves_excluded(self):
        """Fainted reserves should not be predicted as switch targets."""
        from fp.search.opponent_predict import _predict_switch_target

        our = _make_pokemon(name="garchomp", types=["dragon", "ground"], hp=357, max_hp=357)
        dead = _make_pokemon(name="heatran", hp=0, max_hp=386, fainted=True)
        alive = _make_pokemon(name="slowking", types=["water", "psychic"], hp=300, max_hp=300)
        battle = _make_battle(user_active=our, opp_reserve=[dead, alive])

        target = _predict_switch_target(battle)
        self.assertEqual(target, "slowking")

    def test_hazard_cost_penalizes_switch_target(self):
        """Switch targets with high hazard cost should be scored lower."""
        from fp.search.opponent_predict import _predict_switch_target

        our = _make_pokemon(name="garchomp", types=["dragon", "ground"], hp=357, max_hp=357)
        # Fire type takes 25% from SR
        fire_mon = _make_pokemon(
            name="volcarona",
            types=["bug", "fire"],
            hp=300, max_hp=300,
            item="leftovers",
        )
        # Steel/flying resists SR
        steel_mon = _make_pokemon(
            name="skarmory",
            types=["steel", "flying"],
            hp=300, max_hp=334,
            item="leftovers",
        )
        battle = _make_battle(
            user_active=our,
            opp_reserve=[fire_mon, steel_mon],
            opp_side_conditions={constants.STEALTH_ROCK: 1},
        )

        target = _predict_switch_target(battle)
        # Skarmory takes less SR damage AND resists ground — should be preferred
        self.assertEqual(target, "skarmory")


if __name__ == "__main__":
    unittest.main()
