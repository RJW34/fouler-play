"""Tests for the win-condition plan execution bias (STRATEGIST -> EXECUTOR).

apply_win_condition_plan_bias() makes the policy pursue the per-battle Gameplan
(stored on battle.gameplan at team preview) faithfully to the FAT/STALL
archetype: preserve the win-condition Pokemon, clear its checks before deploying
it, and set up only once the path is clear. All Pokemon facts are grounded via
the type chart + move data, never LLM knowledge.
"""
from dataclasses import dataclass

from constants import BattleType
from fp.battle import Battle, Pokemon, Move
from fp.playstyle_config import Playstyle
from fp.team_analysis import analyze_team
from fp.search.main import apply_win_condition_plan_bias


@dataclass
class _GP:
    win_condition: str
    opponent_win_condition: str = ""


def _mk(name, moves):
    p = Pokemon(name, 100)
    p.moves = [Move(m) for m in moves]
    return p


def _team_dict():
    return [
        {"species": "Dragonite", "moves": ["Dragon Dance", "Earthquake", "Roost", "Extreme Speed"],
         "item": "heavydutyboots", "ability": "multiscale", "evs": {"atk": 252, "hp": 4, "spe": 252}},
        {"species": "Corviknight", "moves": ["Brave Bird", "Defog", "Roost", "U-turn"],
         "item": "leftovers", "ability": "pressure", "evs": {"hp": 252, "def": 252}},
        {"species": "Tyranitar", "moves": ["Stealth Rock", "Stone Edge", "Crunch", "Earthquake"],
         "item": "leftovers", "ability": "sandstream", "evs": {"hp": 252, "spd": 252}},
        {"species": "Toxapex", "moves": ["Scald", "Recover", "Toxic", "Haze"],
         "item": "blacksludge", "ability": "regenerator", "evs": {"hp": 252, "def": 252, "spd": 4}},
    ]


def _battle():
    b = Battle("battle-gen9ou-wincon-test")
    b.pokemon_format = "gen9ou"
    b.battle_type = BattleType.STANDARD_BATTLE
    b.turn = 6
    b.force_switch = False

    dnite = _mk("dragonite", ["dragondance", "earthquake", "roost", "extremespeed"])
    corv = _mk("corviknight", ["bravebird", "defog", "roost", "uturn"])
    ttar = _mk("tyranitar", ["stealthrock", "stoneedge", "crunch", "earthquake"])
    toxa = _mk("toxapex", ["scald", "recover", "toxic", "haze"])
    b.user.active = corv
    b.user.reserve = [dnite, ttar, toxa]
    b.user.team_dict = _team_dict()

    # Skarmory (Steel/Flying) resists Dragonite's Dragon STAB -> a wincon check.
    skarm = _mk("skarmory", ["bodypress", "spikes", "roost", "whirlwind"])
    gholdengo = _mk("gholdengo", ["makeitrain", "shadowball", "nastyplot", "recover"])
    b.opponent.active = skarm
    b.opponent.reserve = [gholdengo]
    b.gameplan = _GP(win_condition="Set up Dragonite after weakening checks")
    return b


def test_holds_wincon_while_its_check_is_alive():
    b = _battle()
    tp = analyze_team(b.user.team_dict)
    base = {
        "bravebird": 1.0, "defog": 1.0, "roost": 1.0, "uturn": 1.0,
        "switch dragonite": 1.0, "switch tyranitar": 1.0,
    }
    out = apply_win_condition_plan_bias(dict(base), b, tp, Playstyle.FAT)
    # Deploying the wincon into its own live check is discouraged.
    assert out["switch dragonite"] < base["switch dragonite"]
    # Pressuring the check (attacking Skarmory) is encouraged.
    assert out["bravebird"] > base["bravebird"]


def test_preserves_healthy_wincon_and_sets_up_when_path_clear():
    b = _battle()
    tp = analyze_team(b.user.team_dict)
    # Promote Dragonite to active; clear its check.
    b.user.active = next(p for p in b.user.reserve if p.name == "dragonite")
    b.user.reserve = [p for p in b.user.reserve if p.name != "dragonite"]
    b.opponent.active.hp = 0
    b.opponent.reserve = []
    base = {"dragondance": 1.0, "earthquake": 1.0, "roost": 1.0,
            "extremespeed": 1.0, "switch corviknight": 1.0}
    out = apply_win_condition_plan_bias(dict(base), b, tp, Playstyle.FAT)
    assert out["switch corviknight"] < base["switch corviknight"]   # don't sac the wincon
    assert out["dragondance"] >= base["dragondance"]                # ok to set up now


def test_no_op_for_hyper_offense_and_non_gen9ou():
    b = _battle()
    tp = analyze_team(b.user.team_dict)
    base = {"bravebird": 1.0, "switch dragonite": 1.0}
    # Hyper offense does not run the slow win-condition plan.
    assert apply_win_condition_plan_bias(dict(base), b, tp, Playstyle.HYPER_OFFENSE) == base
    # Wrong format -> untouched.
    b.pokemon_format = "gen9randombattle"
    assert apply_win_condition_plan_bias(dict(base), b, tp, Playstyle.FAT) == base
