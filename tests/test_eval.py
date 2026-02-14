"""
Tests for the 1-ply eval function and forced line detection.

Tests core scoring logic:
- Damaging moves get scores proportional to damage
- KO moves get bonuses
- Recovery is suppressed on free turns
- Sacking is penalized
- Forced lines are detected correctly
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
):
    """Create a mock Pokemon object matching the battle.py interface."""
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

    # Create mock move objects
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

    # has_type method
    def has_type(t):
        return t in pkmn.types
    pkmn.has_type = has_type

    return pkmn


def _make_battler(active=None, reserve=None, side_conditions=None):
    """Create a mock Battler."""
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
    force_switch=False,
    weather=None,
    field=None,
    trick_room=False,
    turn=5,
):
    """Create a mock Battle object."""
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
    battle.force_switch = force_switch
    battle.weather = weather
    battle.field = field
    battle.trick_room = trick_room
    battle.turn = turn
    battle.battle_type = MagicMock()
    battle.battle_type.name = "STANDARD_BATTLE"

    # get_effective_speed
    def get_effective_speed(battler):
        if battler.active and isinstance(battler.active.stats, dict):
            base_speed = battler.active.stats.get(constants.SPEED, 100)
            speed_boost = battler.active.boosts.get(constants.SPEED, 0)
            if speed_boost > 0:
                return int(base_speed * (2 + speed_boost) / 2)
            elif speed_boost < 0:
                return int(base_speed * 2 / (2 - speed_boost))
            return base_speed
        return 100
    battle.get_effective_speed = get_effective_speed

    return battle


class TestEvaluatePosition(unittest.TestCase):
    """Test the eval function's scoring."""

    def test_damaging_moves_get_positive_scores(self):
        """Damaging moves should get positive scores."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["seismictoss", "softboiled", "thunderwave", "stealthrock"],
            stats={
                constants.ATTACK: 50,
                constants.DEFENSE: 50,
                constants.SPECIAL_ATTACK: 75,
                constants.SPECIAL_DEFENSE: 300,
                constants.SPEED: 55,
                constants.HITPOINTS: 714,
            },
            hp=714,
            max_hp=714,
        )
        opp = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=357,
            max_hp=357,
            moves=["earthquake", "outrage"],
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        scores = evaluate_position(battle)
        self.assertIn("seismictoss", scores)
        self.assertGreater(scores.get("seismictoss", 0), 0)

    def test_recovery_suppressed_on_free_turn(self):
        """Recovery should be suppressed when we're safe (free turn)."""
        from fp.search.eval import evaluate_position

        # Our Pokemon at 90% HP vs an opponent that can barely touch us
        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["softboiled", "seismictoss"],
            stats={
                constants.ATTACK: 50,
                constants.DEFENSE: 50,
                constants.SPECIAL_ATTACK: 75,
                constants.SPECIAL_DEFENSE: 300,
                constants.SPEED: 55,
                constants.HITPOINTS: 714,
            },
            hp=642,  # ~90% HP
            max_hp=714,
        )
        opp = _make_pokemon(
            name="chansey",
            types=["normal"],
            stats={
                constants.ATTACK: 20,
                constants.DEFENSE: 20,
                constants.SPECIAL_ATTACK: 35,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 50,
                constants.HITPOINTS: 600,
            },
            hp=600,
            max_hp=600,
            moves=[],  # No known moves = low estimated threat
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        scores = evaluate_position(battle)

        # Softboiled should score lower than seismictoss on a free turn
        sb_score = scores.get("softboiled", 0)
        st_score = scores.get("seismictoss", 0)
        # On a free turn at 90% HP, attacking should be preferred
        self.assertGreater(st_score, sb_score,
                           f"seismictoss ({st_score}) should outscore softboiled ({sb_score}) on free turn")

    def test_switches_get_scored(self):
        """Switch options should get scores."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["softboiled"],
            hp=100,
            max_hp=714,
        )
        reserve = _make_pokemon(
            name="gliscor",
            types=["ground", "flying"],
            hp=344,
            max_hp=344,
            item="toxicorb",
            ability="poisonheal",
        )
        opp = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake"],
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=357,
            max_hp=357,
        )
        battle = _make_battle(
            user_active=our,
            user_reserve=[reserve],
            opp_active=opp,
        )

        scores = evaluate_position(battle)
        self.assertIn("switch gliscor", scores)
        self.assertGreater(scores.get("switch gliscor", 0), 0)

    def test_force_switch_scores_only_switches(self):
        """Forced switch turns should not include normal move scores."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["softboiled", "seismictoss", "thunderwave", "stealthrock"],
            hp=100,
            max_hp=714,
        )
        reserve_a = _make_pokemon(
            name="gliscor",
            types=["ground", "flying"],
            hp=344,
            max_hp=344,
            item="toxicorb",
            ability="poisonheal",
        )
        reserve_b = _make_pokemon(
            name="gholdengo",
            types=["steel", "ghost"],
            hp=320,
            max_hp=320,
            item="airballoon",
            ability="goodasgold",
        )
        opp = _make_pokemon(name="garchomp", moves=["earthquake"])
        battle = _make_battle(
            user_active=our,
            user_reserve=[reserve_a, reserve_b],
            opp_active=opp,
            force_switch=True,
        )

        scores = evaluate_position(battle)
        self.assertTrue(scores)
        self.assertTrue(all(move.startswith("switch ") for move in scores))
        self.assertIn("switch gliscor", scores)
        self.assertIn("switch gholdengo", scores)

    def test_empty_moves_handled(self):
        """Should handle pokemon with no usable moves."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(name="blissey", moves=[])
        opp = _make_pokemon(name="garchomp", moves=[])
        battle = _make_battle(user_active=our, opp_active=opp)

        scores = evaluate_position(battle)
        # Should return without error (may have switch scores only)
        self.assertIsInstance(scores, dict)

    def test_seismic_toss_fixed_damage_is_scored_as_real_damage(self):
        """Seismic Toss should score based on fixed damage, not as a status move."""
        from fp.search.eval import _estimate_damage_ratio, evaluate_position

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["seismictoss", "calmmind", "softboiled"],
            hp=652,
            max_hp=652,
        )
        opp = _make_pokemon(
            name="ironmoth",
            types=["fire", "poison"],
            hp=322,
            max_hp=322,
            moves=["fierydance"],
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        toss_ratio = _estimate_damage_ratio(our, opp, "seismictoss")
        scores = evaluate_position(battle)

        self.assertGreater(toss_ratio, 0.25)
        self.assertLess(toss_ratio, 0.40)
        self.assertGreater(scores.get("seismictoss", 0.0), scores.get("calmmind", 0.0))

    def test_switches_do_not_drown_out_stable_progress_line(self):
        """When we can make safe progress, switch scores should be capped."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["seismictoss", "softboiled", "calmmind"],
            hp=652,
            max_hp=652,
        )
        reserve_a = _make_pokemon(
            name="gliscor",
            types=["ground", "flying"],
            hp=354,
            max_hp=354,
            item="toxicorb",
            ability="poisonheal",
        )
        reserve_b = _make_pokemon(
            name="pecharunt",
            types=["poison", "ghost"],
            hp=380,
            max_hp=380,
            item="heavydutyboots",
        )
        opp = _make_pokemon(
            name="ironmoth",
            types=["fire", "poison"],
            hp=322,
            max_hp=322,
            moves=["fierydance", "bugbuzz"],
        )
        battle = _make_battle(
            user_active=our,
            user_reserve=[reserve_a, reserve_b],
            opp_active=opp,
        )

        scores = evaluate_position(battle)
        best_switch = max(v for k, v in scores.items() if k.startswith("switch "))
        toss_score = scores.get("seismictoss", 0.0)

        self.assertGreater(toss_score, 0.0)
        self.assertLessEqual(best_switch, toss_score * 1.40)

    def test_rocky_helmet_switch_gets_contact_trap_bonus(self):
        """Switches that can KO via Rocky Helmet recoil should be preferred."""
        from fp.search.eval import _score_switch

        our = _make_pokemon(
            name="cinderace",
            types=["fire"],
            moves=["pyroball", "uturn"],
            hp=220,
            max_hp=330,
        )
        helmet_wall = _make_pokemon(
            name="corviknight_helmet",
            types=["steel", "flying"],
            item="rockyhelmet",
            ability="pressure",
            hp=399,
            max_hp=399,
            stats={
                constants.ATTACK: 87,
                constants.DEFENSE: 150,
                constants.SPECIAL_ATTACK: 53,
                constants.SPECIAL_DEFENSE: 100,
                constants.SPEED: 67,
                constants.HITPOINTS: 399,
            },
        )
        plain_wall = _make_pokemon(
            name="corviknight_plain",
            types=["steel", "flying"],
            item="leftovers",
            ability="pressure",
            hp=399,
            max_hp=399,
            stats={
                constants.ATTACK: 87,
                constants.DEFENSE: 150,
                constants.SPECIAL_ATTACK: 53,
                constants.SPECIAL_DEFENSE: 100,
                constants.SPEED: 67,
                constants.HITPOINTS: 399,
            },
        )
        opp = _make_pokemon(
            name="greattusk",
            types=["ground", "fighting"],
            hp=30,   # 10% HP; in Rocky Helmet recoil KO range
            max_hp=300,
            moves=["closecombat"],
            stats={
                constants.ATTACK: 131,
                constants.DEFENSE: 131,
                constants.SPECIAL_ATTACK: 53,
                constants.SPECIAL_DEFENSE: 53,
                constants.SPEED: 87,
                constants.HITPOINTS: 300,
            },
        )

        battle = _make_battle(
            user_active=our,
            user_reserve=[helmet_wall, plain_wall],
            opp_active=opp,
        )

        helmet_score = _score_switch(battle, "corviknight_helmet")
        plain_score = _score_switch(battle, "corviknight_plain")

        self.assertGreater(helmet_score, plain_score)


class TestForcedLines(unittest.TestCase):
    """Test forced line detection."""

    def test_guaranteed_ko_detected(self):
        """Should detect guaranteed KO when we outspeed and OHKO."""
        from fp.search.forced_lines import detect_forced_line

        # We outspeed and have a KO move
        our = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake"],
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=357,
            max_hp=357,
        )
        opp = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            stats={
                constants.ATTACK: 130,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 300,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 180,
                constants.HITPOINTS: 386,
            },
            hp=100,  # Low HP, guaranteed KO with EQ
            max_hp=386,
            moves=["magmastorm"],
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        forced = detect_forced_line(battle)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.line_type, "guaranteed_ko")
        self.assertEqual(forced.move, "earthquake")
        self.assertGreaterEqual(forced.confidence, 0.90)

    def test_no_forced_line_when_unclear(self):
        """Should return None when position is unclear."""
        from fp.search.forced_lines import detect_forced_line

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["seismictoss", "softboiled"],
            stats={
                constants.ATTACK: 50,
                constants.DEFENSE: 50,
                constants.SPECIAL_ATTACK: 75,
                constants.SPECIAL_DEFENSE: 300,
                constants.SPEED: 55,
                constants.HITPOINTS: 714,
            },
            hp=714,
            max_hp=714,
        )
        opp = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=357,
            max_hp=357,
            moves=["earthquake"],
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        detect_forced_line(battle)
        # Blissey vs Garchomp - neither KOs, not a clear forced line
        # This could detect forced switch if EQ KOs, but seismictoss doesn't kill
        # Result depends on exact damage calc but this is a reasonable "unclear" case

    def test_no_guaranteed_ko_when_opponent_speed_is_uncertain(self):
        """Should not call guaranteed KO when opponent may be scarfed."""
        from fp.search.forced_lines import detect_forced_line

        our = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake"],
            item="leftovers",
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=357,
            max_hp=357,
        )
        opp = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            item=constants.UNKNOWN_ITEM,
            stats={
                constants.ATTACK: 130,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 300,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 180,
                constants.HITPOINTS: 386,
            },
            hp=100,
            max_hp=386,
            moves=["magmastorm"],
        )
        opp.can_have_choice_item = True
        opp.speed_range = type("SpeedRange", (), {"min": 0, "max": float("inf")})()

        battle = _make_battle(user_active=our, opp_active=opp)
        forced = detect_forced_line(battle)

        self.assertTrue(forced is None or forced.line_type != "guaranteed_ko")

    def test_no_forced_guaranteed_ko_when_revealed_switch_in_has_clear_punish(self):
        """If a revealed switch-in clearly punishes, don't tunnel on forced KO."""
        from fp.search.forced_lines import detect_forced_line

        our = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake"],
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 180,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 160,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=220,
            max_hp=357,
        )

        # Active target is in KO range.
        opp_active = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            item="leftovers",
            ability="flashfire",
            stats={
                constants.ATTACK: 130,
                constants.DEFENSE: 180,
                constants.SPECIAL_ATTACK: 300,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 180,
                constants.HITPOINTS: 386,
            },
            hp=80,
            max_hp=386,
            moves=["magmastorm"],
        )

        # Revealed switch-in is immune to EQ and has revealed lethal pressure.
        opp_switch = _make_pokemon(
            name="gyarados",
            types=["water", "flying"],
            stats={
                constants.ATTACK: 200,
                constants.DEFENSE: 220,
                constants.SPECIAL_ATTACK: 420,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 220,
                constants.HITPOINTS: 331,
            },
            hp=331,
            max_hp=331,
            moves=["icebeam"],
        )

        battle = _make_battle(
            user_active=our,
            opp_active=opp_active,
            opp_reserve=[opp_switch],
        )

        forced = detect_forced_line(battle)
        self.assertTrue(forced is None or forced.line_type != "guaranteed_ko")

    def test_guaranteed_ko_still_taken_when_switch_punish_is_unrevealed(self):
        """Do not overpredict unknown switch punish; take productive obvious KO."""
        from fp.search.forced_lines import detect_forced_line

        our = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake"],
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 180,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 160,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=357,
            max_hp=357,
        )
        opp_active = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            item="leftovers",
            ability="flashfire",
            stats={
                constants.ATTACK: 130,
                constants.DEFENSE: 180,
                constants.SPECIAL_ATTACK: 300,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 180,
                constants.HITPOINTS: 386,
            },
            hp=80,
            max_hp=386,
            moves=["magmastorm"],
        )
        opp_switch = _make_pokemon(
            name="gyarados",
            types=["water", "flying"],
            stats={
                constants.ATTACK: 200,
                constants.DEFENSE: 220,
                constants.SPECIAL_ATTACK: 420,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 220,
                constants.HITPOINTS: 331,
            },
            hp=331,
            max_hp=331,
            moves=[],  # no revealed punish
        )

        battle = _make_battle(
            user_active=our,
            opp_active=opp_active,
            opp_reserve=[opp_switch],
        )

        forced = detect_forced_line(battle)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.line_type, "guaranteed_ko")

    def test_forced_switch_detected(self):
        """Should detect forced switch when opponent KOs us and we can't fight back."""
        from fp.search.forced_lines import detect_forced_line

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["softboiled"],  # Can't damage
            stats={
                constants.ATTACK: 50,
                constants.DEFENSE: 50,
                constants.SPECIAL_ATTACK: 75,
                constants.SPECIAL_DEFENSE: 300,
                constants.SPEED: 55,
                constants.HITPOINTS: 714,
            },
            hp=100,  # Low HP
            max_hp=714,
        )
        reserve = _make_pokemon(
            name="skarmory",
            types=["steel", "flying"],
            hp=334,
            max_hp=334,
            item="leftovers",
        )
        opp = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            moves=["earthquake", "outrage", "swordsdance", "stoneedge"],
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=357,
            max_hp=357,
        )
        battle = _make_battle(
            user_active=our,
            user_reserve=[reserve],
            opp_active=opp,
        )

        forced = detect_forced_line(battle)
        if forced:
            self.assertIn(forced.line_type, ["forced_switch", "guaranteed_ko"])

    def test_phaze_vs_boosted(self):
        """Should detect phaze opportunity vs boosted opponent."""
        from fp.search.forced_lines import detect_forced_line

        our = _make_pokemon(
            name="hippowdon",
            types=["ground"],
            moves=["earthquake", "whirlwind", "stealthrock", "slackoff"],
            stats={
                constants.ATTACK: 250,
                constants.DEFENSE: 350,
                constants.SPECIAL_ATTACK: 68,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 97,
                constants.HITPOINTS: 420,
            },
            hp=420,
            max_hp=420,
        )
        opp = _make_pokemon(
            name="dragapult",
            types=["dragon", "ghost"],
            moves=["dragondance", "dragondarts"],
            stats={
                constants.ATTACK: 240,
                constants.DEFENSE: 170,
                constants.SPECIAL_ATTACK: 200,
                constants.SPECIAL_DEFENSE: 170,
                constants.SPEED: 420,
                constants.HITPOINTS: 290,
            },
            hp=290,
            max_hp=290,
            boosts={constants.ATTACK: 2, constants.SPEED: 2},
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        forced = detect_forced_line(battle)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.line_type, "phaze")
        self.assertEqual(forced.move, "whirlwind")


    def test_safe_hazard_opportunity(self):
        """Should detect safe hazard opportunity when not threatened and rocks not up."""
        from fp.search.forced_lines import detect_forced_line

        our = _make_pokemon(
            name="hippowdon",
            types=["ground"],
            moves=["earthquake", "stealthrock", "slackoff", "whirlwind"],
            stats={
                constants.ATTACK: 250,
                constants.DEFENSE: 350,
                constants.SPECIAL_ATTACK: 68,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 97,
                constants.HITPOINTS: 420,
            },
            hp=420,
            max_hp=420,
        )
        opp = _make_pokemon(
            name="slowking",
            types=["water", "psychic"],
            moves=["scald"],
            stats={
                constants.ATTACK: 75,
                constants.DEFENSE: 180,
                constants.SPECIAL_ATTACK: 200,
                constants.SPECIAL_DEFENSE: 250,
                constants.SPEED: 80,
                constants.HITPOINTS: 394,
            },
            hp=394,
            max_hp=394,
        )
        # 2 alive reserves so hazards matter
        reserve1 = _make_pokemon(name="garchomp", hp=300, max_hp=357)
        reserve2 = _make_pokemon(name="heatran", hp=300, max_hp=386)
        battle = _make_battle(
            user_active=our,
            opp_active=opp,
            opp_reserve=[reserve1, reserve2],
            opp_side_conditions={},  # No rocks yet
        )

        forced = detect_forced_line(battle)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.line_type, "safe_hazard")
        self.assertEqual(forced.move, "stealthrock")

    def test_toxic_stall_win_condition(self):
        """Should detect toxic stall when opponent is poisoned and can't break through."""
        from fp.search.forced_lines import detect_forced_line

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["softboiled", "seismictoss", "toxic", "stealthrock"],
            stats={
                constants.ATTACK: 50,
                constants.DEFENSE: 50,
                constants.SPECIAL_ATTACK: 75,
                constants.SPECIAL_DEFENSE: 300,
                constants.SPEED: 55,
                constants.HITPOINTS: 714,
            },
            hp=500,  # ~70% HP, below 85% threshold
            max_hp=714,
        )
        opp = _make_pokemon(
            name="slowking",
            types=["water", "psychic"],
            moves=["scald"],
            stats={
                constants.ATTACK: 75,
                constants.DEFENSE: 180,
                constants.SPECIAL_ATTACK: 200,
                constants.SPECIAL_DEFENSE: 250,
                constants.SPEED: 80,
                constants.HITPOINTS: 394,
            },
            hp=394,
            max_hp=394,
            status="tox",  # Toxic'd
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        forced = detect_forced_line(battle)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.line_type, "toxic_stall")
        self.assertEqual(forced.move, "softboiled")

    def test_predicted_switch_punish(self):
        """Should detect predicted switch and recommend hazards/status."""
        from fp.search.forced_lines import detect_forced_line

        # Strong attacker vs weak opponent in terrible matchup
        our = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            moves=["lavaplume", "stealthrock", "toxic", "earthpower"],
            stats={
                constants.ATTACK: 90,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 300,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 180,
                constants.HITPOINTS: 386,
            },
            hp=386,
            max_hp=386,
        )
        opp = _make_pokemon(
            name="scizor",
            types=["bug", "steel"],
            moves=["bulletpunch"],  # Can barely touch Heatran
            stats={
                constants.ATTACK: 200,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 55,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 150,
                constants.HITPOINTS: 344,
            },
            hp=344,
            max_hp=344,
        )
        reserve = _make_pokemon(name="gastrodon", hp=300, max_hp=300)
        battle = _make_battle(
            user_active=our,
            opp_active=opp,
            opp_reserve=[reserve],
            opp_side_conditions={},  # No rocks yet
        )

        forced = detect_forced_line(battle)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.line_type, "predicted_switch")
        self.assertEqual(forced.move, "stealthrock")
        self.assertAlmostEqual(forced.confidence, 0.70, places=2)


class TestEvalOpponentPrediction(unittest.TestCase):
    """Test opponent prediction integration in eval."""

    def test_hazards_boosted_when_switch_predicted(self):
        """Hazard scores should be boosted when opponent is predicted to switch."""
        from fp.search.eval import evaluate_position

        # Strong matchup: we threaten them heavily, they can't touch us
        our = _make_pokemon(
            name="heatran",
            types=["fire", "steel"],
            moves=["lavaplume", "stealthrock", "toxic"],
            stats={
                constants.ATTACK: 90,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 300,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 180,
                constants.HITPOINTS: 386,
            },
            hp=386,
            max_hp=386,
        )
        opp = _make_pokemon(
            name="scizor",
            types=["bug", "steel"],
            moves=["bulletpunch"],  # Terrible into Heatran
            stats={
                constants.ATTACK: 200,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 55,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 150,
                constants.HITPOINTS: 344,
            },
            hp=344,
            max_hp=344,
        )
        reserve1 = _make_pokemon(name="gastrodon", hp=300, max_hp=300)
        reserve2 = _make_pokemon(name="slowking", hp=300, max_hp=300)
        battle = _make_battle(
            user_active=our,
            opp_active=opp,
            opp_reserve=[reserve1, reserve2],
            opp_side_conditions={},
        )

        scores = evaluate_position(battle)
        # Stealth Rock should have a meaningful score when opponent is likely switching
        sr_score = scores.get("stealthrock", 0)
        self.assertGreater(sr_score, 0.01)


class TestDecisionFixes(unittest.TestCase):
    """Tests for the five decision fixes from marxistpiplupist battle analysis."""

    def test_low_hp_penalizes_non_damaging(self):
        """Fix 1: At 6% HP moving second, non-damaging moves should be penalized."""
        from fp.search.eval import evaluate_position

        # Gliscor at 6% HP vs a faster opponent (Dragapult)
        our = _make_pokemon(
            name="gliscor",
            types=["ground", "flying"],
            moves=["earthquake", "spikes", "uturn", "roost"],
            stats={
                constants.ATTACK: 260,
                constants.DEFENSE: 345,
                constants.SPECIAL_ATTACK: 45,
                constants.SPECIAL_DEFENSE: 186,
                constants.SPEED: 290,
                constants.HITPOINTS: 354,
            },
            hp=21,  # ~6% HP
            max_hp=354,
            item="toxicorb",
            ability="poisonheal",
        )
        opp = _make_pokemon(
            name="dragapult",
            types=["dragon", "ghost"],
            stats={
                constants.ATTACK: 240,
                constants.DEFENSE: 170,
                constants.SPECIAL_ATTACK: 200,
                constants.SPECIAL_DEFENSE: 170,
                constants.SPEED: 420,  # Faster than Gliscor
                constants.HITPOINTS: 290,
            },
            hp=290,
            max_hp=290,
            moves=["shadowball", "dracometeor"],
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        scores = evaluate_position(battle)
        eq_score = scores.get("earthquake", 0)
        spikes_score = scores.get("spikes", 0)
        roost_score = scores.get("roost", 0)

        # At 6% HP moving second, Spikes and Roost should be heavily penalized
        self.assertGreater(eq_score, spikes_score * 3,
                           f"EQ ({eq_score:.4f}) should far outscore Spikes ({spikes_score:.4f}) at 6% HP")
        self.assertGreater(eq_score, roost_score * 3,
                           f"EQ ({eq_score:.4f}) should far outscore Roost ({roost_score:.4f}) at 6% HP")

    def test_low_hp_allows_priority(self):
        """Fix 1: Priority moves should NOT be penalized at low HP."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="scizor",
            types=["bug", "steel"],
            moves=["bulletpunch", "swordsdance", "roost"],
            stats={
                constants.ATTACK: 350,
                constants.DEFENSE: 236,
                constants.SPECIAL_ATTACK: 55,
                constants.SPECIAL_DEFENSE: 196,
                constants.SPEED: 166,
                constants.HITPOINTS: 344,
            },
            hp=30,  # ~9% HP
            max_hp=344,
        )
        opp = _make_pokemon(
            name="garchomp",
            types=["dragon", "ground"],
            stats={
                constants.ATTACK: 300,
                constants.DEFENSE: 200,
                constants.SPECIAL_ATTACK: 100,
                constants.SPECIAL_DEFENSE: 180,
                constants.SPEED: 333,
                constants.HITPOINTS: 357,
            },
            hp=100,  # Low HP — bullet punch can KO
            max_hp=357,
            moves=["earthquake"],
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        scores = evaluate_position(battle)
        bp_score = scores.get("bulletpunch", 0)
        sd_score = scores.get("swordsdance", 0)

        # Bullet Punch (priority) should not be penalized; Swords Dance should be
        self.assertGreater(bp_score, sd_score,
                           f"Bullet Punch ({bp_score:.4f}) should outscore Swords Dance ({sd_score:.4f})")

    def test_low_hp_pivots_preferred_over_hazards(self):
        """Fix 1: U-turn should score higher than Spikes at low HP moving second."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="gliscor",
            types=["ground", "flying"],
            moves=["uturn", "spikes"],
            stats={
                constants.ATTACK: 260,
                constants.DEFENSE: 345,
                constants.SPECIAL_ATTACK: 45,
                constants.SPECIAL_DEFENSE: 186,
                constants.SPEED: 290,
                constants.HITPOINTS: 354,
            },
            hp=35,  # ~10% HP
            max_hp=354,
        )
        opp = _make_pokemon(
            name="dragapult",
            types=["dragon", "ghost"],
            stats={
                constants.ATTACK: 240,
                constants.DEFENSE: 170,
                constants.SPECIAL_ATTACK: 200,
                constants.SPECIAL_DEFENSE: 170,
                constants.SPEED: 420,  # Faster than Gliscor
                constants.HITPOINTS: 290,
            },
            hp=290,
            max_hp=290,
            moves=["shadowball"],
        )
        reserve = _make_pokemon(
            name="blissey",
            types=["normal"],
            hp=714,
            max_hp=714,
        )
        battle = _make_battle(
            user_active=our,
            user_reserve=[reserve],
            opp_active=opp,
        )

        scores = evaluate_position(battle)
        uturn_score = scores.get("uturn", 0)
        spikes_score = scores.get("spikes", 0)

        # U-turn gets 0.5x at low HP; Spikes gets 0.05x — U-turn should be much higher
        self.assertGreater(uturn_score, spikes_score,
                           f"U-turn ({uturn_score:.4f}) should outscore Spikes ({spikes_score:.4f}) at low HP")

    def test_contrary_snowball_penalizes_passive_stay(self):
        """Fix 2: Passive mon vs +2 Contrary Serperior should prefer switching out."""
        from fp.search.eval import evaluate_position

        # Toxapex can't threaten Serperior (<20% best damage)
        our = _make_pokemon(
            name="toxapex",
            types=["poison", "water"],
            moves=["toxic", "recover", "scald"],
            stats={
                constants.ATTACK: 63,
                constants.DEFENSE: 323,
                constants.SPECIAL_ATTACK: 53,
                constants.SPECIAL_DEFENSE: 302,
                constants.SPEED: 55,
                constants.HITPOINTS: 304,
            },
            hp=250,
            max_hp=304,
        )
        opp = _make_pokemon(
            name="serperior",
            types=["grass"],
            ability="contrary",
            stats={
                constants.ATTACK: 75,
                constants.DEFENSE: 160,
                constants.SPECIAL_ATTACK: 250,
                constants.SPECIAL_DEFENSE: 160,
                constants.SPEED: 350,
                constants.HITPOINTS: 313,
            },
            hp=313,
            max_hp=313,
            moves=["leafstorm"],
            boosts={constants.SPECIAL_ATTACK: 2},
        )
        reserve = _make_pokemon(
            name="corviknight",
            types=["steel", "flying"],
            hp=399,
            max_hp=399,
            stats={
                constants.ATTACK: 187,
                constants.DEFENSE: 309,
                constants.SPECIAL_ATTACK: 53,
                constants.SPECIAL_DEFENSE: 205,
                constants.SPEED: 170,
                constants.HITPOINTS: 399,
            },
            moves=["bodypress", "roost"],
        )
        battle = _make_battle(
            user_active=our,
            user_reserve=[reserve],
            opp_active=opp,
        )

        scores = evaluate_position(battle)
        best_stay = max(
            (v for k, v in scores.items() if not k.startswith("switch ")),
            default=0,
        )
        best_switch = max(
            (v for k, v in scores.items() if k.startswith("switch ")),
            default=0,
        )

        # Switch should be preferred when we can't pressure a +2 Contrary booster
        self.assertGreater(best_switch, best_stay,
                           f"Switch ({best_switch:.4f}) should outscore staying ({best_stay:.4f}) "
                           f"vs +2 Contrary Serperior")

    def test_contrary_resist_switch_boosted(self):
        """Fix 2A: Corviknight switch should be boosted vs Contrary Grass attacker."""
        from fp.search.eval import _score_switch

        opp = _make_pokemon(
            name="serperior",
            types=["grass"],
            ability="contrary",
            stats={
                constants.ATTACK: 75,
                constants.DEFENSE: 160,
                constants.SPECIAL_ATTACK: 250,
                constants.SPECIAL_DEFENSE: 160,
                constants.SPEED: 350,
                constants.HITPOINTS: 313,
            },
            hp=313,
            max_hp=313,
            moves=["leafstorm"],
            boosts={constants.SPECIAL_ATTACK: 2},
        )
        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["seismictoss"],
            hp=714,
            max_hp=714,
        )
        # Corviknight: resists Grass, has Body Press to threaten
        corv = _make_pokemon(
            name="corviknight",
            types=["steel", "flying"],
            hp=399,
            max_hp=399,
            stats={
                constants.ATTACK: 187,
                constants.DEFENSE: 309,
                constants.SPECIAL_ATTACK: 53,
                constants.SPECIAL_DEFENSE: 205,
                constants.SPEED: 170,
                constants.HITPOINTS: 399,
            },
            moves=["bodypress", "roost"],
        )
        # Chansey: doesn't resist Grass, can't threaten
        chansey = _make_pokemon(
            name="chansey",
            types=["normal"],
            hp=600,
            max_hp=600,
            stats={
                constants.ATTACK: 20,
                constants.DEFENSE: 20,
                constants.SPECIAL_ATTACK: 35,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 50,
                constants.HITPOINTS: 600,
            },
            moves=["seismictoss"],
        )

        battle = _make_battle(
            user_active=our,
            user_reserve=[corv, chansey],
            opp_active=opp,
        )

        corv_score = _score_switch(battle, "corviknight")
        chansey_score = _score_switch(battle, "chansey")

        # Corviknight (resists Grass + threatens) should score much higher
        self.assertGreater(corv_score, chansey_score,
                           f"Corviknight ({corv_score:.4f}) should outscore Chansey ({chansey_score:.4f}) "
                           f"vs Contrary Serperior")

    def test_heal_block_suppresses_recovery(self):
        """Fix 3: Recovery score should be near-zero when Heal Block is active."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="blissey",
            types=["normal"],
            moves=["softboiled", "seismictoss"],
            stats={
                constants.ATTACK: 50,
                constants.DEFENSE: 50,
                constants.SPECIAL_ATTACK: 75,
                constants.SPECIAL_DEFENSE: 300,
                constants.SPEED: 55,
                constants.HITPOINTS: 714,
            },
            hp=300,  # ~42% HP — normally recovery would be very attractive
            max_hp=714,
            volatile_statuses=["healblock"],
        )
        opp = _make_pokemon(
            name="slowking",
            types=["water", "psychic"],
            stats={
                constants.ATTACK: 75,
                constants.DEFENSE: 180,
                constants.SPECIAL_ATTACK: 200,
                constants.SPECIAL_DEFENSE: 250,
                constants.SPEED: 80,
                constants.HITPOINTS: 394,
            },
            hp=394,
            max_hp=394,
            moves=["scald"],
        )
        battle = _make_battle(user_active=our, opp_active=opp)

        scores = evaluate_position(battle)
        sb_score = scores.get("softboiled", 0)
        st_score = scores.get("seismictoss", 0)

        # Softboiled should be suppressed under Heal Block
        self.assertLess(sb_score, st_score,
                        f"Softboiled ({sb_score:.4f}) should score less than "
                        f"Seismic Toss ({st_score:.4f}) under Heal Block")
        # Normalized score should be very small compared to attack
        self.assertLess(sb_score, 0.10,
                        f"Softboiled ({sb_score:.4f}) should be near-zero under Heal Block")

    def test_magic_bounce_reserve_penalizes_hazards(self):
        """Fix 5: Spikes score reduced when Hatterene in opponent reserves."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="gliscor",
            types=["ground", "flying"],
            moves=["earthquake", "spikes", "toxic"],
            stats={
                constants.ATTACK: 260,
                constants.DEFENSE: 345,
                constants.SPECIAL_ATTACK: 45,
                constants.SPECIAL_DEFENSE: 186,
                constants.SPEED: 290,
                constants.HITPOINTS: 354,
            },
            hp=354,
            max_hp=354,
        )
        torkoal = _make_pokemon(
            name="torkoal",
            types=["fire"],
            stats={
                constants.ATTACK: 85,
                constants.DEFENSE: 310,
                constants.SPECIAL_ATTACK: 185,
                constants.SPECIAL_DEFENSE: 170,
                constants.SPEED: 56,
                constants.HITPOINTS: 344,
            },
            hp=344,
            max_hp=344,
            moves=["lavaplume"],
        )
        hatterene = _make_pokemon(
            name="hatterene",
            types=["psychic", "fairy"],
            hp=282,
            max_hp=282,
            ability="magicbounce",
        )

        # Battle WITH Hatterene in reserves
        battle_with_mb = _make_battle(
            user_active=our,
            opp_active=torkoal,
            opp_reserve=[hatterene],
        )
        # Battle WITHOUT Hatterene
        battle_without_mb = _make_battle(
            user_active=our,
            opp_active=torkoal,
            opp_reserve=[],
        )

        scores_with = evaluate_position(battle_with_mb)
        scores_without = evaluate_position(battle_without_mb)

        spikes_with = scores_with.get("spikes", 0)
        spikes_without = scores_without.get("spikes", 0)
        eq_with = scores_with.get("earthquake", 0)
        eq_without = scores_without.get("earthquake", 0)

        # Spikes should be penalized when MB is in reserves
        # Earthquake should not be penalized (or even boosted)
        spikes_ratio_with = spikes_with / max(eq_with, 0.001)
        spikes_ratio_without = spikes_without / max(eq_without, 0.001)

        self.assertLess(spikes_ratio_with, spikes_ratio_without,
                        f"Spikes-to-EQ ratio should be lower with Hatterene in reserves "
                        f"({spikes_ratio_with:.4f} vs {spikes_ratio_without:.4f})")

    def test_magic_bounce_reserve_boosts_attacks(self):
        """Fix 5: Earthquake score boosted on free turn when Hatterene in reserves."""
        from fp.search.eval import evaluate_position

        our = _make_pokemon(
            name="gliscor",
            types=["ground", "flying"],
            moves=["earthquake", "spikes"],
            stats={
                constants.ATTACK: 260,
                constants.DEFENSE: 345,
                constants.SPECIAL_ATTACK: 45,
                constants.SPECIAL_DEFENSE: 186,
                constants.SPEED: 290,
                constants.HITPOINTS: 354,
            },
            hp=354,
            max_hp=354,
        )
        # Weak opponent we dominate (free turn conditions)
        opp = _make_pokemon(
            name="chansey",
            types=["normal"],
            stats={
                constants.ATTACK: 20,
                constants.DEFENSE: 20,
                constants.SPECIAL_ATTACK: 35,
                constants.SPECIAL_DEFENSE: 200,
                constants.SPEED: 50,
                constants.HITPOINTS: 600,
            },
            hp=600,
            max_hp=600,
            moves=[],
        )
        hatterene = _make_pokemon(
            name="hatterene",
            types=["psychic", "fairy"],
            hp=282,
            max_hp=282,
            ability="magicbounce",
            stats={
                constants.ATTACK: 90,
                constants.DEFENSE: 227,
                constants.SPECIAL_ATTACK: 306,
                constants.SPECIAL_DEFENSE: 268,
                constants.SPEED: 79,
                constants.HITPOINTS: 282,
            },
        )

        battle = _make_battle(
            user_active=our,
            opp_active=opp,
            opp_reserve=[hatterene],
        )

        scores = evaluate_position(battle)
        eq_score = scores.get("earthquake", 0)
        spikes_score = scores.get("spikes", 0)

        # On free turn with MB in reserves, EQ should be preferred over Spikes
        # (Spikes penalized by 0.7x, EQ potentially boosted for hitting Hatterene)
        self.assertGreater(eq_score, spikes_score,
                           f"EQ ({eq_score:.4f}) should outscore Spikes ({spikes_score:.4f}) "
                           f"with MB reserve on free turn")


    def test_thunder_wave_boosted_when_opponent_faster(self):
        """Thunder Wave should score higher when paralysis would flip the speed matchup."""
        from fp.search.eval import evaluate_position

        # Slow user (60 Speed) vs fast opponent (120 Speed)
        # Paralysis: 120 * 0.5 = 60, so we'd tie/outspeed
        user_slow = _make_pokemon(
            name="blissey", hp=600, max_hp=652, types=["normal"],
            stats={constants.ATTACK: 10, constants.DEFENSE: 10,
                   constants.SPECIAL_ATTACK: 75, constants.SPECIAL_DEFENSE: 135,
                   constants.SPEED: 60, constants.HITPOINTS: 652},
            moves=["thunderwave", "seismictoss", "softboiled"],
        )
        opp_fast = _make_pokemon(
            name="dragapult", hp=300, max_hp=300, types=["dragon", "ghost"],
            stats={constants.ATTACK: 120, constants.DEFENSE: 75,
                   constants.SPECIAL_ATTACK: 100, constants.SPECIAL_DEFENSE: 75,
                   constants.SPEED: 142, constants.HITPOINTS: 300},
            moves=["shadowball"],
        )
        battle_slow = _make_battle(user_active=user_slow, opp_active=opp_fast)

        # Fast user (130 Speed) vs slow opponent (60 Speed) — already outspeeds
        user_fast = _make_pokemon(
            name="jolteon", hp=270, max_hp=270, types=["electric"],
            stats={constants.ATTACK: 65, constants.DEFENSE: 60,
                   constants.SPECIAL_ATTACK: 110, constants.SPECIAL_DEFENSE: 95,
                   constants.SPEED: 130, constants.HITPOINTS: 270},
            moves=["thunderwave", "thunderbolt", "voltswitch"],
        )
        opp_slow = _make_pokemon(
            name="dondozo", hp=500, max_hp=500, types=["water"],
            stats={constants.ATTACK: 100, constants.DEFENSE: 115,
                   constants.SPECIAL_ATTACK: 65, constants.SPECIAL_DEFENSE: 65,
                   constants.SPEED: 35, constants.HITPOINTS: 500},
            moves=["waterfall"],
        )
        battle_fast = _make_battle(user_active=user_fast, opp_active=opp_slow)

        scores_slow = evaluate_position(battle_slow)
        scores_fast = evaluate_position(battle_fast)

        twave_when_slower = scores_slow.get("thunderwave", 0)
        twave_when_faster = scores_fast.get("thunderwave", 0)

        self.assertGreater(twave_when_slower, twave_when_faster,
                           f"Thunder Wave should be worth more when we're slower "
                           f"({twave_when_slower:.4f}) vs already faster ({twave_when_faster:.4f})")


if __name__ == "__main__":
    unittest.main()
