import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import patch

import constants
from fp.search.main import (
    DecisionProfile,
    OpponentAbilityState,
    _apply_mcts_eval_anchor_choice_guard,
    _build_mcts_legal_move_set,
    _should_activate_mcts_blend,
    apply_ability_penalties,
    apply_conversion_progress_bias,
    apply_hazard_maintenance_bias,
    apply_team_strategy_bias,
    apply_switch_chain_progress_bias,
    apply_threat_switch_bias,
    apply_switch_penalties,
    apply_heuristic_bias,
    detect_odd_move,
    select_move_from_eval_scores,
)
from fp.playstyle_config import Playstyle
from sweep_fix import smart_sweep_prevention


def _mk_move(name: str):
    return SimpleNamespace(name=name)


def _mk_battle(active_hp: int, active_max_hp: int, move_names: list[str] | None = None):
    active = SimpleNamespace(
        name="skarmory",
        base_name="skarmory",
        hp=active_hp,
        max_hp=active_max_hp,
        ability="sturdy",
        moves=[_mk_move(m) for m in (move_names or ["whirlwind", "roost"])],
        base_stats={
            constants.HITPOINTS: 65,
            constants.DEFENSE: 140,
            constants.SPECIAL_DEFENSE: 70,
        },
    )
    user = SimpleNamespace(
        active=active,
        reserve=[],
        last_selected_move=SimpleNamespace(move=""),
        last_used_move=SimpleNamespace(move=""),
    )
    opponent = SimpleNamespace(active=None, reserve=[])
    return SimpleNamespace(user=user, opponent=opponent, force_switch=False, turn=2)


def _mk_ability_state(boost: int = 2):
    return SimpleNamespace(
        opponent_active_is_threat=True,
        opponent_attack_boost=boost,
        opponent_spa_boost=0,
        opponent_has_offensive_boost=True,
        opponent_hp_percent=1.0,
    )


class TestThreatSwitchBias(unittest.TestCase):
    def test_prefers_reset_over_switch_loop_when_healthy(self):
        battle = _mk_battle(active_hp=300, active_max_hp=334)
        ability_state = _mk_ability_state(boost=2)
        policy = {
            "whirlwind": 0.45,
            "switch pecharunt": 1.20,
            "spikes": 0.50,
            "roost": 0.40,
            "bodypress": 0.20,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        self.assertGreater(adjusted["whirlwind"], adjusted["switch pecharunt"])
        self.assertLess(adjusted["spikes"], adjusted["whirlwind"])
        self.assertLess(adjusted["roost"], adjusted["whirlwind"])

    def test_penalizes_passive_when_no_switch_available(self):
        battle = _mk_battle(active_hp=260, active_max_hp=334, move_names=["stealthrock", "softboiled", "shadowball"])
        ability_state = _mk_ability_state(boost=3)
        policy = {
            "stealthrock": 0.90,
            "softboiled": 0.70,
            "shadowball": 0.30,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        self.assertGreater(adjusted["shadowball"], adjusted["stealthrock"])
        self.assertGreater(adjusted["shadowball"], adjusted["softboiled"])

    def test_caps_switch_when_viable_strong_attack_exists(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["earthquake", "spikes", "toxic", "protect"],
        )
        ability_state = _mk_ability_state(boost=2)
        policy = {
            "earthquake": 0.07,
            "spikes": 0.10,
            "switch corviknight": 0.30,
            "switch pecharunt": 0.28,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        self.assertLessEqual(adjusted["switch corviknight"], adjusted["earthquake"] * 1.02 + 1e-6)
        self.assertLess(adjusted["spikes"], adjusted["earthquake"])

    def test_breaks_back_to_back_switch_chain_when_progress_exists(self):
        battle = _mk_battle(
            active_hp=290,
            active_max_hp=334,
            move_names=["uturn", "bodypress", "roost"],
        )
        battle.user.last_selected_move = SimpleNamespace(move="switch corviknight")
        battle.user.last_used_move = SimpleNamespace(move="switch corviknight")
        ability_state = _mk_ability_state(boost=2)
        policy = {
            "uturn": 0.31,
            "bodypress": 0.30,
            "switch pecharunt": 0.82,
            "switch walkingwake": 0.79,
            "roost": 0.08,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        self.assertLess(adjusted["switch pecharunt"], adjusted["uturn"])
        self.assertLess(adjusted["switch walkingwake"], adjusted["uturn"])

    def test_caps_recovery_loop_vs_plus_one_when_attack_available(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["recover", "foulplay", "partingshot"],
        )
        ability_state = _mk_ability_state(boost=1)
        policy = {
            "recover": 0.92,
            "switch corviknight": 0.98,
            "switch gliscor": 0.95,
            "foulplay": 0.31,
            "partingshot": 0.44,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        self.assertLess(adjusted["recover"], adjusted["foulplay"])
        self.assertLess(adjusted["switch corviknight"], adjusted["foulplay"])
        self.assertLess(adjusted["switch gliscor"], adjusted["foulplay"])

    def test_keeps_stabilizing_recovery_live_vs_boosted_threat(self):
        battle = _mk_battle(
            active_hp=324,
            active_max_hp=652,
            move_names=["softboiled", "seismictoss", "stealthrock"],
        )
        battle.user.active.name = "blissey"
        battle.user.active.types = ["normal"]
        battle.user.active.stats = {
            constants.ATTACK: 50,
            constants.DEFENSE: 50,
            constants.SPECIAL_ATTACK: 75,
            constants.SPECIAL_DEFENSE: 300,
            constants.SPEED: 55,
            constants.HITPOINTS: 652,
        }
        battle.opponent.active = SimpleNamespace(
            name="ironmoth",
            hp=230,
            max_hp=322,
            moves=[_mk_move("fierydance"), _mk_move("bugbuzz")],
            boosts={constants.SPECIAL_ATTACK: 2},
            types=["fire", "poison"],
            stats={
                constants.ATTACK: 70,
                constants.DEFENSE: 90,
                constants.SPECIAL_ATTACK: 140,
                constants.SPECIAL_DEFENSE: 100,
                constants.SPEED: 110,
                constants.HITPOINTS: 322,
            },
        )
        ability_state = _mk_ability_state(boost=2)
        ability_state.opponent_hp_percent = 230 / 322
        policy = {
            "softboiled": 0.20,
            "seismictoss": 0.18,
            "stealthrock": 0.19,
            "switch dondozo": 0.21,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        self.assertGreater(adjusted["softboiled"], adjusted["stealthrock"])
        self.assertGreaterEqual(adjusted["softboiled"], adjusted["seismictoss"] * 0.90)

    def test_type_immune_move_stays_zero_vs_boosted_threat(self):
        """Seismic Toss (Fighting) vs Ghost type: weight 0.0 must never be boosted."""
        battle = _mk_battle(
            active_hp=652,
            active_max_hp=652,
            move_names=["softboiled", "calmmind", "seismictoss", "stealthrock"],
        )
        battle.user.active.name = "blissey"
        battle.user.active.types = ["normal"]
        battle.opponent.active = SimpleNamespace(
            name="gholdengo",
            hp=265,
            max_hp=336,
            moves=[_mk_move("nastyplot"), _mk_move("shadowball")],
            boosts={constants.SPECIAL_ATTACK: 4},
            types=["steel", "ghost"],
            stats={
                constants.ATTACK: 60,
                constants.DEFENSE: 91,
                constants.SPECIAL_ATTACK: 133,
                constants.SPECIAL_DEFENSE: 91,
                constants.SPEED: 84,
                constants.HITPOINTS: 336,
            },
        )
        ability_state = _mk_ability_state(boost=4)
        policy = {
            "softboiled": 0.026,
            "calmmind": 0.378,
            "seismictoss": 0.0,   # Blocked by type immunity filter
            "stealthrock": 1.005,
            "switch corviknight": 0.005,
            "switch gliscor": 0.002,
            "switch pecharunt": 0.002,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        self.assertEqual(adjusted["seismictoss"], 0.0,
                         "Type-immune move must stay at 0.0 after threat bias")

    def test_unaware_hold_mode_avoids_immediate_switch_out(self):
        battle = _mk_battle(
            active_hp=410,
            active_max_hp=503,
            move_names=["wavecrash", "rest", "bodypress"],
        )
        battle.user.active.name = "dondozo"
        battle.user.active.base_name = "dondozo"
        battle.user.active.ability = "unaware"
        battle.user.active.types = ["water"]
        battle.user.last_selected_move = SimpleNamespace(move="switch dondozo")
        battle.user.last_used_move = SimpleNamespace(move="switch dondozo")
        battle.opponent.active = SimpleNamespace(
            name="kingambit",
            hp=260,
            max_hp=404,
            moves=[_mk_move("kowtowcleave"), _mk_move("suckerpunch")],
            boosts={constants.ATTACK: 2},
            types=["dark", "steel"],
            stats={
                constants.ATTACK: 135,
                constants.DEFENSE: 120,
                constants.SPECIAL_ATTACK: 60,
                constants.SPECIAL_DEFENSE: 85,
                constants.SPEED: 50,
                constants.HITPOINTS: 404,
            },
        )
        ability_state = _mk_ability_state(boost=2)
        ability_state.opponent_has_offensive_boost = True
        policy = {
            "switch blissey": 0.88,
            "switch corviknight": 0.84,
            "wavecrash": 0.31,
            "rest": 0.28,
            "bodypress": 0.26,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        self.assertGreater(adjusted["wavecrash"], adjusted["switch blissey"])
        self.assertGreater(adjusted["bodypress"], adjusted["switch corviknight"])


class TestSwitchChainProgressBias(unittest.TestCase):
    def test_caps_switches_after_switch_when_progress_exists(self):
        battle = _mk_battle(
            active_hp=280,
            active_max_hp=334,
            move_names=["earthquake", "spikes", "roost"],
        )
        battle.user.last_selected_move = SimpleNamespace(move="switch corviknight")
        battle.user.last_used_move = SimpleNamespace(move="switch corviknight")
        ability_state = _mk_ability_state(boost=0)
        ability_state.opponent_has_offensive_boost = False
        policy = {
            "switch pecharunt": 0.80,
            "switch gliscor": 0.72,
            "earthquake": 0.26,
            "spikes": 0.12,
        }

        adjusted = apply_switch_chain_progress_bias(policy, battle, ability_state)

        self.assertLess(adjusted["switch pecharunt"], adjusted["earthquake"])
        self.assertLess(adjusted["switch gliscor"], adjusted["earthquake"])

    def test_does_not_force_when_progress_line_is_too_weak(self):
        battle = _mk_battle(
            active_hp=280,
            active_max_hp=334,
            move_names=["earthquake", "spikes", "roost"],
        )
        battle.user.last_selected_move = SimpleNamespace(move="switch corviknight")
        battle.user.last_used_move = SimpleNamespace(move="switch corviknight")
        ability_state = _mk_ability_state(boost=0)
        ability_state.opponent_has_offensive_boost = False
        policy = {
            "switch pecharunt": 0.90,
            "switch gliscor": 0.75,
            "earthquake": 0.05,
            "spikes": 0.02,
        }

        adjusted = apply_switch_chain_progress_bias(policy, battle, ability_state)

        self.assertEqual(adjusted["switch pecharunt"], policy["switch pecharunt"])
        self.assertEqual(adjusted["earthquake"], policy["earthquake"])

    def test_counts_partingshot_as_progress_in_non_boosted_switch_chain(self):
        battle = _mk_battle(
            active_hp=240,
            active_max_hp=334,
            move_names=["partingshot", "recover", "foulplay"],
        )
        battle.user.last_selected_move = SimpleNamespace(move="switch gliscor")
        battle.user.last_used_move = SimpleNamespace(move="switch gliscor")
        ability_state = _mk_ability_state(boost=0)
        ability_state.opponent_has_offensive_boost = False
        policy = {
            "switch gliscor": 0.36,
            "switch blissey": 0.28,
            "partingshot": 0.15,
            "foulplay": 0.05,
            "recover": 0.04,
        }

        adjusted = apply_switch_chain_progress_bias(policy, battle, ability_state)

        self.assertGreater(adjusted["partingshot"], adjusted["switch gliscor"])
        self.assertGreater(adjusted["partingshot"], adjusted["switch blissey"])


class TestSwitchChainSelectionOverride(unittest.TestCase):
    def test_prefers_progress_move_after_recent_switch(self):
        battle = _mk_battle(active_hp=300, active_max_hp=334, move_names=["bodypress", "roost"])
        battle.user.last_selected_move = SimpleNamespace(move="switch gliscor")
        battle.turn = 5

        eval_scores = {
            "switch pecharunt": 1.00,
            "bodypress": 0.97,
            "roost": 0.20,
        }

        choice = select_move_from_eval_scores(eval_scores, ability_state=None, battle=battle)

        self.assertEqual(choice, "bodypress")

    def test_prefers_non_switch_when_switch_only_slightly_better(self):
        battle = _mk_battle(active_hp=300, active_max_hp=334, move_names=["bodypress", "roost"])
        battle.turn = 6

        eval_scores = {
            "switch pecharunt": 1.00,
            "bodypress": 0.97,
            "roost": 0.20,
        }

        choice = select_move_from_eval_scores(eval_scores, ability_state=None, battle=battle)

        self.assertEqual(choice, "bodypress")

    def test_ahead_state_prefers_progress_even_with_bigger_switch_gap(self):
        battle = _mk_battle(active_hp=300, active_max_hp=334, move_names=["bodypress", "roost"])
        battle.turn = 8

        eval_scores = {
            "switch pecharunt": 1.00,
            "bodypress": 0.89,
            "roost": 0.20,
        }

        choice = select_move_from_eval_scores(eval_scores, ability_state=None, battle=battle)

        self.assertEqual(choice, "bodypress")

    def test_allows_repeat_spikes_while_layers_can_progress(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["spikes", "bodypress", "roost"],
        )
        battle.turn = 4
        battle.user.last_selected_move = SimpleNamespace(move="spikes")
        battle.user.last_used_move = SimpleNamespace(move="spikes")
        battle.opponent.side_conditions = defaultdict(int)
        battle.opponent.side_conditions[constants.SPIKES] = 1

        eval_scores = {
            "spikes": 1.0,
            "bodypress": 0.8,
            "roost": 0.4,
        }

        choice = select_move_from_eval_scores(eval_scores, ability_state=None, battle=battle)

        self.assertEqual(choice, "spikes")

    def test_penalizes_repeat_spikes_when_layers_are_maxed(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["spikes", "bodypress", "roost"],
        )
        battle.turn = 4
        battle.user.last_selected_move = SimpleNamespace(move="spikes")
        battle.user.last_used_move = SimpleNamespace(move="spikes")
        battle.opponent.side_conditions = defaultdict(int)
        battle.opponent.side_conditions[constants.SPIKES] = 3

        eval_scores = {
            "spikes": 1.0,
            "bodypress": 0.8,
            "roost": 0.4,
        }

        choice = select_move_from_eval_scores(eval_scores, ability_state=None, battle=battle)

        self.assertEqual(choice, "bodypress")

    def test_penalizes_spikes_when_layers_are_maxed_without_repeat(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["spikes", "bodypress", "roost"],
        )
        battle.turn = 4
        battle.opponent.side_conditions = defaultdict(int)
        battle.opponent.side_conditions[constants.SPIKES] = 3

        eval_scores = {
            "spikes": 1.0,
            "bodypress": 0.8,
            "roost": 0.4,
        }

        choice = select_move_from_eval_scores(eval_scores, ability_state=None, battle=battle)

        self.assertEqual(choice, "bodypress")

    def test_hazard_maintenance_penalizes_restack_into_active_spinner(self):
        battle = _mk_battle(
            active_hp=320,
            active_max_hp=354,
            move_names=["spikes", "earthquake", "toxic", "protect"],
        )
        battle.user.active.name = "gliscor"
        battle.opponent.active = SimpleNamespace(
            name="irontreads",
            hp=320,
            max_hp=384,
            moves=[_mk_move("rapidspin"), _mk_move("highhorsepower")],
            types=["ground", "steel"],
        )
        battle.opponent.side_conditions = defaultdict(int)
        battle.opponent.side_conditions[constants.STEALTH_ROCK] = 1
        battle.opponent.side_conditions[constants.SPIKES] = 1

        ability_state = OpponentAbilityState(
            opponent_hp_percent=320 / 384,
            our_hp_percent=320 / 354,
            opponent_alive_count=5,
            opponent_has_hazard_removal=True,
            opponent_hazard_layers=3,
            opponent_active_has_hazard_removal=True,
            opponent_active_removal_is_spin=True,
        )

        policy = {
            "spikes": 1.0,
            "earthquake": 0.82,
            "toxic": 0.35,
            "protect": 0.22,
        }

        adjusted = apply_hazard_maintenance_bias(policy, battle, ability_state)

        self.assertLess(adjusted["spikes"], adjusted["earthquake"])

    def test_hazard_maintenance_prefers_spinblock_over_passive_pivot(self):
        battle = _mk_battle(
            active_hp=190,
            active_max_hp=380,
            move_names=["shadowball", "toxic", "partingshot", "recover"],
        )
        battle.user.active.name = "pecharunt"
        battle.user.active.types = ["poison", "ghost"]
        battle.user.reserve = [
            SimpleNamespace(
                name="gholdengo",
                hp=200,
                max_hp=300,
                types=["steel", "ghost"],
            ),
            SimpleNamespace(
                name="blissey",
                hp=520,
                max_hp=652,
                types=["normal"],
            ),
        ]
        battle.opponent.active = SimpleNamespace(
            name="irontreads",
            hp=300,
            max_hp=384,
            moves=[_mk_move("rapidspin"), _mk_move("highhorsepower")],
            types=["ground", "steel"],
        )
        battle.opponent.side_conditions = defaultdict(int)
        battle.opponent.side_conditions[constants.STEALTH_ROCK] = 1
        battle.opponent.side_conditions[constants.SPIKES] = 1

        ability_state = OpponentAbilityState(
            opponent_hp_percent=300 / 384,
            our_hp_percent=190 / 380,
            opponent_alive_count=4,
            opponent_has_hazard_removal=True,
            opponent_hazard_layers=3,
            opponent_active_has_hazard_removal=True,
            opponent_active_removal_is_spin=True,
            our_has_alive_spinblocker=True,
        )

        policy = {
            "partingshot": 1.0,
            "switch blissey": 0.96,
            "switch gholdengo": 0.62,
            "shadowball": 0.70,
            "recover": 0.64,
        }

        adjusted = apply_hazard_maintenance_bias(policy, battle, ability_state)

        self.assertGreater(adjusted["switch gholdengo"], adjusted["switch blissey"])
        self.assertLess(adjusted["partingshot"], adjusted["shadowball"])

    def test_trapped_state_blocks_switch_selection(self):
        battle = _mk_battle(active_hp=280, active_max_hp=334, move_names=["bodypress", "roost"])
        battle.turn = 15
        battle.user.trapped = True
        battle.request_json = {
            constants.ACTIVE: [{constants.TRAPPED: True}],
        }

        eval_scores = {
            "switch pecharunt": 1.0,
            "bodypress": 0.40,
            "roost": 0.20,
        }

        choice = select_move_from_eval_scores(eval_scores, ability_state=None, battle=battle)

        self.assertEqual(choice, "bodypress")

    @patch("fp.search.main._eval_opponent_best_damage", return_value=0.70)
    def test_survival_override_prefers_switch_when_stayin_is_ko_risk(self, _mock_damage):
        battle = _mk_battle(
            active_hp=100,
            active_max_hp=334,
            move_names=["earthquake", "spikes", "toxic", "protect"],
        )
        battle.turn = 2
        battle.user.active.status = None
        battle.user.active.item = "leftovers"
        battle.opponent.active = SimpleNamespace(
            name="jolteon",
            hp=230,
            max_hp=292,
            moves=[_mk_move("terablast")],
            types=["electric"],
            boosts={},
            stats={
                constants.ATTACK: 65,
                constants.DEFENSE: 60,
                constants.SPECIAL_ATTACK: 110,
                constants.SPECIAL_DEFENSE: 95,
                constants.SPEED: 130,
                constants.HITPOINTS: 292,
            },
        )

        eval_scores = {
            "earthquake": 1.00,
            "switch blissey": 0.88,
            "switch pecharunt": 0.62,
            "spikes": 0.25,
        }

        choice = select_move_from_eval_scores(eval_scores, ability_state=None, battle=battle)

        self.assertEqual(choice, "switch blissey")


class TestSmartSweepPrevention(unittest.TestCase):
    def test_does_not_auto_boost_switch_at_plus_one_with_counterplay(self):
        battle = _mk_battle(active_hp=260, active_max_hp=334, move_names=["whirlwind", "roost"])
        ability_state = _mk_ability_state(boost=1)

        switch_penalty, _ = smart_sweep_prevention(
            penalty=1.0,
            reason="",
            move="switch blissey",
            move_name="switch blissey",
            ability_state=ability_state,
            battle=battle,
            PENALTY_PASSIVE_VS_BOOSTED=constants.PENALTY_PASSIVE_VS_BOOSTED,
            BOOST_SWITCH_VS_BOOSTED=constants.BOOST_SWITCH_VS_BOOSTED,
            BOOST_PHAZE_VS_BOOSTED=constants.BOOST_PHAZE_VS_BOOSTED,
            BOOST_REVENGE_VS_BOOSTED=constants.BOOST_REVENGE_VS_BOOSTED,
            SETUP_MOVES=constants.SETUP_MOVES,
            STATUS_ONLY_MOVES=constants.STATUS_ONLY_MOVES,
            PHAZING_MOVES=constants.PHAZING_MOVES,
            PRIORITY_MOVES=constants.PRIORITY_MOVES,
        )

        self.assertEqual(switch_penalty, 1.0)

    def test_still_boosts_switch_at_plus_two_without_counterplay(self):
        battle = _mk_battle(active_hp=120, active_max_hp=334, move_names=["roost", "bodypress"])
        ability_state = _mk_ability_state(boost=2)

        switch_penalty, _ = smart_sweep_prevention(
            penalty=1.0,
            reason="",
            move="switch blissey",
            move_name="switch blissey",
            ability_state=ability_state,
            battle=battle,
            PENALTY_PASSIVE_VS_BOOSTED=constants.PENALTY_PASSIVE_VS_BOOSTED,
            BOOST_SWITCH_VS_BOOSTED=constants.BOOST_SWITCH_VS_BOOSTED,
            BOOST_PHAZE_VS_BOOSTED=constants.BOOST_PHAZE_VS_BOOSTED,
            BOOST_REVENGE_VS_BOOSTED=constants.BOOST_REVENGE_VS_BOOSTED,
            SETUP_MOVES=constants.SETUP_MOVES,
            STATUS_ONLY_MOVES=constants.STATUS_ONLY_MOVES,
            PHAZING_MOVES=constants.PHAZING_MOVES,
            PRIORITY_MOVES=constants.PRIORITY_MOVES,
        )

        self.assertGreater(switch_penalty, 1.0)

    def test_does_not_boost_roost_just_because_phaze_exists(self):
        battle = _mk_battle(active_hp=260, active_max_hp=334, move_names=["whirlwind", "roost"])
        ability_state = _mk_ability_state(boost=1)

        roost_penalty, roost_reason = smart_sweep_prevention(
            penalty=1.0,
            reason="",
            move="roost",
            move_name="roost",
            ability_state=ability_state,
            battle=battle,
            PENALTY_PASSIVE_VS_BOOSTED=constants.PENALTY_PASSIVE_VS_BOOSTED,
            BOOST_SWITCH_VS_BOOSTED=constants.BOOST_SWITCH_VS_BOOSTED,
            BOOST_PHAZE_VS_BOOSTED=constants.BOOST_PHAZE_VS_BOOSTED,
            BOOST_REVENGE_VS_BOOSTED=constants.BOOST_REVENGE_VS_BOOSTED,
            SETUP_MOVES=constants.SETUP_MOVES,
            STATUS_ONLY_MOVES=constants.STATUS_ONLY_MOVES,
            PHAZING_MOVES=constants.PHAZING_MOVES,
            PRIORITY_MOVES=constants.PRIORITY_MOVES,
        )
        phaze_penalty, _ = smart_sweep_prevention(
            penalty=1.0,
            reason="",
            move="whirlwind",
            move_name="whirlwind",
            ability_state=ability_state,
            battle=battle,
            PENALTY_PASSIVE_VS_BOOSTED=constants.PENALTY_PASSIVE_VS_BOOSTED,
            BOOST_SWITCH_VS_BOOSTED=constants.BOOST_SWITCH_VS_BOOSTED,
            BOOST_PHAZE_VS_BOOSTED=constants.BOOST_PHAZE_VS_BOOSTED,
            BOOST_REVENGE_VS_BOOSTED=constants.BOOST_REVENGE_VS_BOOSTED,
            SETUP_MOVES=constants.SETUP_MOVES,
            STATUS_ONLY_MOVES=constants.STATUS_ONLY_MOVES,
            PHAZING_MOVES=constants.PHAZING_MOVES,
            PRIORITY_MOVES=constants.PRIORITY_MOVES,
        )

        self.assertLess(roost_penalty, 1.0)
        self.assertIn("PASSIVE", roost_reason)
        self.assertGreater(phaze_penalty, 1.0)

    def test_fixed_damage_ko_line_not_treated_as_weak_vs_boosted_threat(self):
        battle = _mk_battle(
            active_hp=324,
            active_max_hp=652,
            move_names=["seismictoss", "softboiled"],
        )
        battle.user.active.name = "blissey"
        battle.user.active.types = ["normal"]
        battle.opponent.active = SimpleNamespace(
            name="ironmoth",
            hp=45,
            max_hp=322,
            moves=[_mk_move("fierydance")],
            boosts={constants.SPECIAL_ATTACK: 2},
            types=["fire", "poison"],
        )
        ability_state = _mk_ability_state(boost=2)
        ability_state.opponent_hp_percent = 45 / 322

        toss_penalty, toss_reason = smart_sweep_prevention(
            penalty=1.0,
            reason="",
            move="seismictoss",
            move_name="seismictoss",
            ability_state=ability_state,
            battle=battle,
            PENALTY_PASSIVE_VS_BOOSTED=constants.PENALTY_PASSIVE_VS_BOOSTED,
            BOOST_SWITCH_VS_BOOSTED=constants.BOOST_SWITCH_VS_BOOSTED,
            BOOST_PHAZE_VS_BOOSTED=constants.BOOST_PHAZE_VS_BOOSTED,
            BOOST_REVENGE_VS_BOOSTED=constants.BOOST_REVENGE_VS_BOOSTED,
            SETUP_MOVES=constants.SETUP_MOVES,
            STATUS_ONLY_MOVES=constants.STATUS_ONLY_MOVES,
            PHAZING_MOVES=constants.PHAZING_MOVES,
            PRIORITY_MOVES=constants.PRIORITY_MOVES,
        )

        self.assertGreaterEqual(toss_penalty, 1.0)
        self.assertIn("KO", toss_reason)


class TestConversionAndMCTSBlend(unittest.TestCase):
    def test_conversion_bias_caps_switch_when_ahead(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["bodypress", "roost", "spikes"],
        )
        policy = {
            "switch pecharunt": 1.00,
            "bodypress": 0.80,
            "roost": 0.70,
            "spikes": 0.60,
        }

        adjusted = apply_conversion_progress_bias(policy, battle, ability_state=None)

        self.assertLess(adjusted["switch pecharunt"], adjusted["bodypress"])
        self.assertLess(adjusted["roost"], adjusted["bodypress"])

    def test_mcts_blend_skips_clear_eval_progress_line(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["bodypress", "roost"],
        )
        should_blend, reasons = _should_activate_mcts_blend(
            battle=battle,
            ability_state=None,
            eval_policy={
                "bodypress": 1.00,
                "switch pecharunt": 0.60,
                "roost": 0.20,
            },
            decision_profile=DecisionProfile.DEFAULT,
        )

        self.assertFalse(should_blend)
        self.assertIn("clear_eval_progress", reasons)

    def test_mcts_blend_stays_available_for_boosted_threat(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["bodypress", "roost"],
        )
        ability_state = _mk_ability_state(boost=2)
        should_blend, reasons = _should_activate_mcts_blend(
            battle=battle,
            ability_state=ability_state,
            eval_policy={
                "bodypress": 1.00,
                "switch pecharunt": 0.60,
                "roost": 0.20,
            },
            decision_profile=DecisionProfile.DEFAULT,
        )

        self.assertTrue(should_blend)
        self.assertIn("opponent_boosted", reasons)

    def test_mcts_legal_move_set_filters_low_eval_tail(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["earthquake", "toxic", "protect"],
        )
        legal = _build_mcts_legal_move_set(
            {
                "switch pecharunt": 0.40,
                "switch gliscor": 0.35,
                "earthquake": 0.20,
                "toxic": 0.004,
                "protect": 0.003,
            },
            battle=battle,
            ability_state=None,
        )

        self.assertIn("switch pecharunt", legal)
        self.assertIn("earthquake", legal)
        self.assertNotIn("toxic", legal)
        self.assertNotIn("protect", legal)

    def test_mcts_anchor_guard_keeps_unaware_progress_line(self):
        battle = _mk_battle(
            active_hp=420,
            active_max_hp=503,
            move_names=["wavecrash", "bodypress", "rest"],
        )
        battle.user.active.name = "dondozo"
        battle.user.active.base_name = "dondozo"
        battle.user.active.ability = "unaware"
        ability_state = _mk_ability_state(boost=2)
        ability_state.opponent_has_offensive_boost = True
        guarded_choice, metadata = _apply_mcts_eval_anchor_choice_guard(
            battle=battle,
            ability_state=ability_state,
            eval_policy={
                "wavecrash": 0.55,
                "bodypress": 0.30,
                "switch blissey": 0.15,
            },
            final_policy={
                "switch blissey": 0.44,
                "wavecrash": 0.32,
                "bodypress": 0.24,
            },
            eval_choice="wavecrash",
            proposed_choice="switch blissey",
            decision_profile=DecisionProfile.LOW,
        )

        self.assertEqual("wavecrash", guarded_choice)
        self.assertTrue(metadata.get("applied"))
        self.assertEqual("unaware_hold_keep_progress_line", metadata.get("reason"))

    def test_mcts_anchor_guard_blocks_setup_override_vs_boosted_threat(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["shadowball", "recover", "calmmind"],
        )
        ability_state = _mk_ability_state(boost=2)
        guarded_choice, metadata = _apply_mcts_eval_anchor_choice_guard(
            battle=battle,
            ability_state=ability_state,
            eval_policy={
                "shadowball": 0.47,
                "recover": 0.35,
                "calmmind": 0.18,
            },
            final_policy={
                "calmmind": 0.46,
                "shadowball": 0.31,
                "recover": 0.23,
            },
            eval_choice="shadowball",
            proposed_choice="calmmind",
            decision_profile=DecisionProfile.DEFAULT,
        )

        self.assertEqual("shadowball", guarded_choice)
        self.assertTrue(metadata.get("applied"))
        self.assertEqual("boosted_threat_block_setup_override", metadata.get("reason"))

    def test_mcts_anchor_guard_does_not_force_hazard_eval_under_threat(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["stealthrock", "earthquake", "recover"],
        )
        ability_state = _mk_ability_state(boost=2)
        guarded_choice, metadata = _apply_mcts_eval_anchor_choice_guard(
            battle=battle,
            ability_state=ability_state,
            eval_policy={
                "stealthrock": 0.58,
                "earthquake": 0.28,
                "recover": 0.14,
            },
            final_policy={
                "earthquake": 0.52,
                "stealthrock": 0.25,
                "recover": 0.23,
            },
            eval_choice="stealthrock",
            proposed_choice="earthquake",
            decision_profile=DecisionProfile.DEFAULT,
        )

        self.assertEqual("earthquake", guarded_choice)
        self.assertFalse(metadata.get("applied"))

    def test_mcts_anchor_guard_blocks_hazard_override_while_threat_active(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["seismictoss", "stealthrock", "softboiled"],
        )
        ability_state = _mk_ability_state(boost=2)
        ability_state.opponent_has_offensive_boost = True

        guarded_choice, metadata = _apply_mcts_eval_anchor_choice_guard(
            battle=battle,
            ability_state=ability_state,
            eval_policy={
                "seismictoss": 0.34,
                "switch pecharunt": 0.33,
                "stealthrock": 0.20,
                "softboiled": 0.13,
            },
            final_policy={
                "stealthrock": 0.51,
                "seismictoss": 0.26,
                "switch pecharunt": 0.23,
            },
            eval_choice="seismictoss",
            proposed_choice="stealthrock",
            decision_profile=DecisionProfile.LOW,
        )

        self.assertEqual("seismictoss", guarded_choice)
        self.assertTrue(metadata.get("applied"))
        self.assertEqual("boosted_threat_block_hazard_override", metadata.get("reason"))

    @patch("fp.search.main._eval_opponent_best_damage", return_value=0.70)
    def test_mcts_anchor_guard_prefers_switch_under_immediate_survival_risk(self, _mock_damage):
        battle = _mk_battle(
            active_hp=100,
            active_max_hp=334,
            move_names=["earthquake", "spikes", "toxic", "protect"],
        )
        battle.opponent.active = SimpleNamespace(
            name="jolteon",
            hp=230,
            max_hp=292,
            moves=[_mk_move("terablast")],
            types=["electric"],
            boosts={},
            stats={
                constants.ATTACK: 65,
                constants.DEFENSE: 60,
                constants.SPECIAL_ATTACK: 110,
                constants.SPECIAL_DEFENSE: 95,
                constants.SPEED: 130,
                constants.HITPOINTS: 292,
            },
        )
        ability_state = _mk_ability_state(boost=0)
        ability_state.opponent_active_is_threat = False
        ability_state.opponent_has_offensive_boost = False

        guarded_choice, metadata = _apply_mcts_eval_anchor_choice_guard(
            battle=battle,
            ability_state=ability_state,
            eval_policy={
                "earthquake": 0.60,
                "switch blissey": 0.25,
                "switch pecharunt": 0.15,
            },
            final_policy={
                "switch blissey": 0.52,
                "earthquake": 0.30,
                "switch pecharunt": 0.18,
            },
            eval_choice="earthquake",
            proposed_choice="switch blissey",
            decision_profile=DecisionProfile.LOW,
        )

        self.assertEqual("switch blissey", guarded_choice)
        self.assertTrue(metadata.get("applied"))
        self.assertEqual("survival_risk_prefer_switch", metadata.get("reason"))

    @patch("fp.search.main._eval_opponent_best_damage", return_value=0.22)
    def test_mcts_anchor_guard_blocks_setup_override_over_progress(self, _mock_damage):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["waterfall", "curse", "rest"],
        )
        battle.user.active.name = "dondozo"
        battle.user.active.base_name = "dondozo"
        battle.user.active.ability = "unaware"
        battle.user.active.stats = {
            constants.ATTACK: 100,
            constants.DEFENSE: 130,
            constants.SPECIAL_ATTACK: 65,
            constants.SPECIAL_DEFENSE: 65,
            constants.SPEED: 35,
            constants.HITPOINTS: 334,
        }
        battle.opponent.active = SimpleNamespace(
            name="volcanion",
            hp=250,
            max_hp=321,
            moves=[_mk_move("steameruption")],
            types=["fire", "water"],
            boosts={},
            stats={
                constants.ATTACK: 110,
                constants.DEFENSE: 120,
                constants.SPECIAL_ATTACK: 130,
                constants.SPECIAL_DEFENSE: 90,
                constants.SPEED: 70,
                constants.HITPOINTS: 321,
            },
        )
        ability_state = _mk_ability_state(boost=0)
        ability_state.opponent_active_is_threat = False
        ability_state.opponent_has_offensive_boost = False

        guarded_choice, metadata = _apply_mcts_eval_anchor_choice_guard(
            battle=battle,
            ability_state=ability_state,
            eval_policy={
                "waterfall": 0.42,
                "switch corviknight": 0.36,
                "curse": 0.12,
                "rest": 0.10,
            },
            final_policy={
                "curse": 0.48,
                "waterfall": 0.24,
                "switch corviknight": 0.20,
                "rest": 0.08,
            },
            eval_choice="waterfall",
            proposed_choice="curse",
            decision_profile=DecisionProfile.LOW,
        )

        self.assertEqual("waterfall", guarded_choice)
        self.assertTrue(metadata.get("applied"))
        self.assertIn(
            metadata.get("reason"),
            {"block_setup_override_under_pressure", "block_setup_override_over_progress"},
        )

    def test_destiny_bond_penalizes_attack_at_low_hp(self):
        """Fix: Damaging moves penalized when opponent has Destiny Bond at ≤40% HP."""
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["shadowball", "recover"],
        )
        battle.opponent.active = SimpleNamespace(
            name="gengar",
            hp=80,
            max_hp=261,
            moves=[_mk_move("destinybond"), _mk_move("shadowball")],
            types=["ghost", "poison"],
            boosts={},
            stats={},
        )

        oddities = detect_odd_move(battle, "shadowball", ability_state=None)

        self.assertIn("risk:destiny_bond_likely", oddities)

    def test_destiny_bond_not_triggered_at_high_hp(self):
        """Destiny Bond penalty should NOT apply when opponent is healthy."""
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["shadowball", "recover"],
        )
        battle.opponent.active = SimpleNamespace(
            name="gengar",
            hp=220,
            max_hp=261,
            moves=[_mk_move("destinybond"), _mk_move("shadowball")],
            types=["ghost", "poison"],
            boosts={},
            stats={},
        )

        oddities = detect_odd_move(battle, "shadowball", ability_state=None)

        self.assertNotIn("risk:destiny_bond_likely", oddities)

    def test_destiny_bond_not_triggered_for_status_moves(self):
        """Status moves should not be penalized by Destiny Bond (they don't KO)."""
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["toxic", "recover"],
        )
        battle.opponent.active = SimpleNamespace(
            name="gengar",
            hp=50,
            max_hp=261,
            moves=[_mk_move("destinybond"), _mk_move("shadowball")],
            types=["ghost", "poison"],
            boosts={},
            stats={},
        )

        oddities = detect_odd_move(battle, "toxic", ability_state=None)

        self.assertNotIn("risk:destiny_bond_likely", oddities)

    def test_setup_no_stat_attack_flags_calm_mind_with_seismic_toss(self):
        """Fix: Calm Mind flagged when only damaging move is fixed-damage Seismic Toss."""
        battle = _mk_battle(
            active_hp=652,
            active_max_hp=652,
            move_names=["calmmind", "seismictoss", "softboiled", "thunderwave"],
        )
        battle.user.active.name = "blissey"

        oddities = detect_odd_move(battle, "calmmind", ability_state=None)

        self.assertIn("waste_turn:setup_no_stat_attack", oddities)

    def test_setup_no_stat_attack_allows_nasty_plot_with_real_special(self):
        """Nasty Plot should NOT be flagged when a real SpA move exists."""
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=380,
            move_names=["nastyplot", "shadowball", "hex", "recover"],
        )
        battle.user.active.name = "gholdengo"

        oddities = detect_odd_move(battle, "nastyplot", ability_state=None)

        self.assertNotIn("waste_turn:setup_no_stat_attack", oddities)

    def test_setup_no_stat_attack_flags_swords_dance_without_physical(self):
        """Swords Dance flagged when no physical attack exists."""
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["swordsdance", "seismictoss", "recover", "toxic"],
        )

        oddities = detect_odd_move(battle, "swordsdance", ability_state=None)

        self.assertIn("waste_turn:setup_no_stat_attack", oddities)

    def test_toxic_exempt_when_no_offensive_answer(self):
        """Fix: Toxic should not be suppressed when all attacks are type-immune."""
        battle = _mk_battle(
            active_hp=652,
            active_max_hp=652,
            move_names=["softboiled", "calmmind", "seismictoss", "toxic"],
        )
        battle.user.active.name = "blissey"
        battle.user.active.types = ["normal"]
        battle.opponent.active = SimpleNamespace(
            name="gholdengo",
            hp=265,
            max_hp=336,
            moves=[_mk_move("nastyplot"), _mk_move("shadowball")],
            boosts={constants.SPECIAL_ATTACK: 2},
            types=["steel", "ghost"],
            stats={
                constants.ATTACK: 60,
                constants.DEFENSE: 91,
                constants.SPECIAL_ATTACK: 133,
                constants.SPECIAL_DEFENSE: 91,
                constants.SPEED: 84,
                constants.HITPOINTS: 336,
            },
        )
        ability_state = _mk_ability_state(boost=2)
        # Seismic Toss is type-immune (Fighting vs Ghost) → weight 0
        # Calm Mind is useless (no real SpA move) → very low
        # Toxic is the ONLY progress move
        policy = {
            "softboiled": 0.026,
            "calmmind": 0.020,
            "seismictoss": 0.0,
            "toxic": 0.50,
            "switch corviknight": 0.005,
        }

        adjusted = apply_threat_switch_bias(policy, battle, ability_state)

        # Toxic must NOT be suppressed below its initial weight
        self.assertGreater(adjusted["toxic"], 0.10,
                           f"Toxic ({adjusted['toxic']:.4f}) should remain high "
                           f"when no offensive answer exists")
        # Seismic Toss must stay at 0
        self.assertEqual(adjusted["seismictoss"], 0.0)

    def test_oddity_flags_status_into_poison_heal(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["toxic", "earthquake"],
        )
        ability_state = OpponentAbilityState(
            opponent_hp_percent=1.0,
            our_hp_percent=1.0,
            opponent_alive_count=6,
            has_poison_heal=True,
        )

        oddities = detect_odd_move(battle, "toxic", ability_state)

        self.assertIn("waste_turn:status_into_poison_heal", oddities)

    def test_purifying_salt_penalizes_status_and_ghost_damage(self):
        battle = _mk_battle(
            active_hp=300,
            active_max_hp=334,
            move_names=["toxic", "earthquake", "shadowball"],
        )
        battle.opponent.active = SimpleNamespace(
            name="garganacl",
            moves=[],
            types=["rock"],
            boosts={},
            stats={},
        )
        ability_state = OpponentAbilityState(
            opponent_hp_percent=1.0,
            our_hp_percent=1.0,
            opponent_alive_count=6,
            has_purifying_salt=True,
        )
        policy = {
            "toxic": 1.0,
            "earthquake": 1.0,
            "shadowball": 1.0,
        }

        adjusted = apply_ability_penalties(policy, ability_state, battle=battle)

        self.assertLess(adjusted["toxic"], 0.2)
        self.assertLess(adjusted["shadowball"], adjusted["earthquake"])


class TestTeamStrategyThreatSuppression(unittest.TestCase):
    def test_does_not_reinflate_passive_lines_vs_boosted_threat(self):
        active = SimpleNamespace(
            name="gholdengo",
            hp=194,
            max_hp=378,
            moves=[_mk_move("recover"), _mk_move("nastyplot"), _mk_move("hex"), _mk_move("thunderwave")],
            types=["steel", "ghost"],
        )
        user = SimpleNamespace(
            active=active,
            reserve=[],
            side_conditions=defaultdict(int),
        )
        opponent_active = SimpleNamespace(
            name="gliscor",
            hp=240,
            max_hp=312,
            boosts={constants.ATTACK: 6, constants.SPECIAL_ATTACK: -2},
            moves=[_mk_move("acrobatics")],
            types=["ground", "flying"],
            stats={},
        )
        opponent = SimpleNamespace(
            active=opponent_active,
            reserve=[],
            side_conditions=defaultdict(int),
            account_name=None,
            name=None,
        )
        battle = SimpleNamespace(
            user=user,
            opponent=opponent,
            turn=15,
            force_switch=False,
        )
        team_plan = SimpleNamespace(
            hazard_setters=set(),
            wincons={"gholdengo"},
        )
        policy = {
            "recover": 0.14,
            "nastyplot": 0.07,
            "stealthrock": 0.16,
            "switch blissey": 0.05,
        }

        adjusted = apply_team_strategy_bias(policy, battle, team_plan, Playstyle.FAT)

        self.assertLess(adjusted["recover"], policy["recover"])
        self.assertLess(adjusted["nastyplot"], policy["nastyplot"])
        self.assertLess(adjusted["stealthrock"], policy["stealthrock"])


class TestPivotAndSwitchSafety(unittest.TestCase):
    def test_chilly_reception_is_treated_as_pivot_in_negative_momentum(self):
        battle = _mk_battle(active_hp=280, active_max_hp=334, move_names=["chillyreception", "futuresight"])
        ability_state = _mk_ability_state(boost=0)
        ability_state.momentum_level = "negative"

        policy = {
            "chillyreception": 1.0,
            "futuresight": 1.0,
        }

        adjusted = apply_heuristic_bias(policy, battle, ability_state)

        self.assertGreater(adjusted["chillyreception"], adjusted["futuresight"])

    def test_switch_penalty_heavily_discourages_frozen_target(self):
        active = SimpleNamespace(
            name="corviknight",
            base_name="corviknight",
            hp=340,
            max_hp=399,
            moves=[_mk_move("uturn"), _mk_move("roost")],
            types=["flying", "steel"],
        )
        frozen_tinglu = SimpleNamespace(
            name="tinglu",
            base_name="tinglu",
            hp=280,
            max_hp=514,
            status=constants.FROZEN,
            rest_turns=0,
            moves=[_mk_move("earthquake"), _mk_move("sleeptalk")],
            ability="vesselofruin",
            item="leftovers",
            types=["dark", "ground"],
        )
        healthy_dondozo = SimpleNamespace(
            name="dondozo",
            base_name="dondozo",
            hp=503,
            max_hp=503,
            status=None,
            rest_turns=0,
            moves=[_mk_move("waterfall"), _mk_move("rest")],
            ability="unaware",
            item="leftovers",
            types=["water"],
        )
        user = SimpleNamespace(
            active=active,
            reserve=[frozen_tinglu, healthy_dondozo],
            last_selected_move=SimpleNamespace(move=""),
            last_used_move=SimpleNamespace(move=""),
            side_conditions=defaultdict(int),
        )
        opponent_active = SimpleNamespace(
            name="garchomp",
            base_name="garchomp",
            hp=300,
            max_hp=357,
            boosts={constants.ATTACK: 2, constants.SPECIAL_ATTACK: 0},
            moves=[_mk_move("earthquake")],
            types=["dragon", "ground"],
        )
        opponent = SimpleNamespace(
            active=opponent_active,
            reserve=[],
            side_conditions=defaultdict(int),
        )
        battle = SimpleNamespace(user=user, opponent=opponent, force_switch=False, turn=10)
        ability_state = SimpleNamespace(
            our_hazard_layers=0,
            our_sr_up=False,
            our_spikes_layers=0,
            opponent_has_defiant_pokemon=False,
            opponent_has_offensive_boost=True,
        )
        policy = {
            "switch tinglu": 0.70,
            "switch dondozo": 0.70,
            "uturn": 0.30,
        }

        adjusted = apply_switch_penalties(policy, battle, ability_state, playstyle=Playstyle.FAT)

        self.assertLess(adjusted["switch tinglu"], adjusted["switch dondozo"])

    def test_switch_penalty_avoids_steel_into_revealed_salt_cure(self):
        active = SimpleNamespace(
            name="pecharunt",
            base_name="pecharunt",
            hp=280,
            max_hp=380,
            moves=[_mk_move("partingshot"), _mk_move("shadowball")],
            types=["poison", "ghost"],
        )
        corviknight = SimpleNamespace(
            name="corviknight",
            base_name="corviknight",
            hp=320,
            max_hp=399,
            status=None,
            rest_turns=0,
            moves=[_mk_move("uturn"), _mk_move("roost")],
            ability="pressure",
            item="leftovers",
            types=["flying", "steel"],
        )
        walkingwake = SimpleNamespace(
            name="walkingwake",
            base_name="walkingwake",
            hp=340,
            max_hp=399,
            status=None,
            rest_turns=0,
            moves=[_mk_move("dracometeor"), _mk_move("hydrosteam")],
            ability="protosynthesis",
            item="choice specs",
            types=["water", "dragon"],
        )
        blissey = SimpleNamespace(
            name="blissey",
            base_name="blissey",
            hp=590,
            max_hp=652,
            status=None,
            rest_turns=0,
            moves=[_mk_move("softboiled"), _mk_move("seismictoss")],
            ability="naturalcure",
            item="heavy-duty boots",
            types=["normal"],
        )
        user = SimpleNamespace(
            active=active,
            reserve=[corviknight, walkingwake, blissey],
            last_selected_move=SimpleNamespace(move=""),
            last_used_move=SimpleNamespace(move=""),
            side_conditions=defaultdict(int),
        )
        opponent_active = SimpleNamespace(
            name="garganacl",
            base_name="garganacl",
            hp=320,
            max_hp=404,
            boosts={constants.ATTACK: 0, constants.SPECIAL_ATTACK: 0},
            moves=[_mk_move("saltcure"), _mk_move("bodypress")],
            types=["rock"],
        )
        opponent = SimpleNamespace(
            active=opponent_active,
            reserve=[],
            side_conditions=defaultdict(int),
        )
        battle = SimpleNamespace(user=user, opponent=opponent, force_switch=False, turn=19)
        ability_state = SimpleNamespace(
            our_hazard_layers=0,
            our_sr_up=False,
            our_spikes_layers=0,
            opponent_has_defiant_pokemon=False,
            opponent_has_offensive_boost=False,
            opponent_has_salt_cure=True,
            our_active_is_salt_cured=False,
        )
        policy = {
            "switch corviknight": 0.90,
            "switch walkingwake": 0.90,
            "switch blissey": 0.85,
            "partingshot": 0.25,
        }

        adjusted = apply_switch_penalties(policy, battle, ability_state, playstyle=Playstyle.FAT)

        self.assertLess(adjusted["switch corviknight"], adjusted["switch blissey"])
        self.assertLess(adjusted["switch walkingwake"], adjusted["switch blissey"])

    def test_boosted_stab_weak_switch_is_not_protected_by_fat_softening(self):
        active = SimpleNamespace(
            name="corviknight",
            base_name="corviknight",
            hp=320,
            max_hp=399,
            moves=[_mk_move("uturn"), _mk_move("roost")],
            types=["flying", "steel"],
        )
        slowking = SimpleNamespace(
            name="slowkinggalar",
            base_name="slowkinggalar",
            hp=394,
            max_hp=394,
            status=None,
            rest_turns=0,
            moves=[_mk_move("futuresight"), _mk_move("icebeam"), _mk_move("chillyreception")],
            ability="regenerator",
            item="leftovers",
            types=["poison", "psychic"],
        )
        dondozo = SimpleNamespace(
            name="dondozo",
            base_name="dondozo",
            hp=503,
            max_hp=503,
            status=None,
            rest_turns=0,
            moves=[_mk_move("waterfall"), _mk_move("rest")],
            ability="unaware",
            item="leftovers",
            types=["water"],
        )
        user = SimpleNamespace(
            active=active,
            reserve=[slowking, dondozo],
            last_selected_move=SimpleNamespace(move=""),
            last_used_move=SimpleNamespace(move=""),
            side_conditions=defaultdict(int),
        )
        opponent_active = SimpleNamespace(
            name="garchomp",
            base_name="garchomp",
            hp=320,
            max_hp=357,
            boosts={constants.ATTACK: 4, constants.SPECIAL_ATTACK: 0},
            moves=[_mk_move("earthquake"), _mk_move("scaleshot")],
            types=["dragon", "ground"],
        )
        opponent = SimpleNamespace(
            active=opponent_active,
            reserve=[],
            side_conditions=defaultdict(int),
        )
        battle = SimpleNamespace(user=user, opponent=opponent, force_switch=False, turn=14)
        ability_state = SimpleNamespace(
            our_hazard_layers=0,
            our_sr_up=False,
            our_spikes_layers=0,
            opponent_has_defiant_pokemon=False,
            opponent_has_offensive_boost=True,
        )
        policy = {
            "switch slowkinggalar": 0.90,
            "switch dondozo": 0.45,
            "uturn": 0.20,
        }

        adjusted = apply_switch_penalties(policy, battle, ability_state, playstyle=Playstyle.FAT)

        self.assertLess(adjusted["switch slowkinggalar"], adjusted["switch dondozo"])


if __name__ == "__main__":
    unittest.main()
