import logging
import random
import time
import atexit
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from enum import Enum

import constants
from constants import (
    BattleType,
    # Unaware
    OFFENSIVE_STAT_BOOST_MOVES,
    POKEMON_COMMONLY_UNAWARE,
    UNAWARE_BOOST_PENALTY,
    # Status-boosted abilities (Guts, Marvel Scale, etc.)
    POKEMON_STATUS_BACKFIRES,
    STATUS_INFLICTING_MOVES,
    PURE_STATUS_MOVES,
    # Poison Heal
    POKEMON_COMMONLY_POISON_HEAL,
    TOXIC_POISON_MOVES,
    POISON,
    TOXIC,
    BURN,
    PARALYZED,
    # Type-absorbing abilities
    POKEMON_COMMONLY_WATER_IMMUNE,
    WATER_TYPE_MOVES,
    POKEMON_COMMONLY_ELECTRIC_IMMUNE,
    ELECTRIC_TYPE_MOVES,
    POKEMON_COMMONLY_FLASH_FIRE,
    FIRE_TYPE_MOVES,
    POKEMON_COMMONLY_LEVITATE,
    GROUND_TYPE_MOVES,
    # Magic Bounce
    POKEMON_COMMONLY_MAGIC_BOUNCE,
    MAGIC_BOUNCE_REFLECTED_MOVES,
    # Good as Gold
    POKEMON_COMMONLY_GOOD_AS_GOLD,
    # Competitive/Defiant
    POKEMON_STAT_DROP_BACKFIRES,
    STAT_LOWERING_MOVES,
    # Penalty values
    ABILITY_PENALTY_SEVERE,
    ABILITY_PENALTY_MEDIUM,
    ABILITY_PENALTY_LIGHT,
    ABILITY_BOOST_LIGHT,
    ABILITY_BOOST_MEDIUM,
    # Mold Breaker
    MOLD_BREAKER_ABILITIES,
    MOLD_BREAKER_BYPASSED_ABILITIES,
    # Focus Sash
    POKEMON_COMMONLY_FOCUS_SASH,
    MULTI_HIT_MOVES,
    PRIORITY_MOVES,
    # Setup vs Phazers
    PHAZING_MOVES,
    SETUP_MOVES,
    # Substitute
    STATUS_ONLY_MOVES,
    INFILTRATOR_BYPASS,
    # Contact Moves
    CONTACT_MOVES,
    POKEMON_COMMONLY_IRON_BARBS,
    POKEMON_COMMONLY_ROUGH_SKIN,
    POKEMON_COMMONLY_ROCKY_HELMET,
    # NEW: Dynamic move sets from moves.json
    SOUND_MOVES,
    POWDER_MOVES,
    BULLET_MOVES,
    WIND_MOVES,
    GRASS_TYPE_MOVES,
    DARK_TYPE_MOVES,
    SELF_STAT_DROP_MOVES,
    ALL_STATUS_MOVES,
    # NEW: Dynamic Pokemon sets from pokedex.json
    POKEMON_COMMONLY_CONTRARY,
    POKEMON_COMMONLY_SAP_SIPPER,
    POKEMON_COMMONLY_STURDY,
    POKEMON_COMMONLY_DISGUISE,
    POKEMON_COMMONLY_SOUNDPROOF,
    POKEMON_COMMONLY_BULLETPROOF,
    POKEMON_COMMONLY_OVERCOAT,
    POKEMON_COMMONLY_EARTH_EATER,
    POKEMON_COMMONLY_JUSTIFIED,
    POKEMON_COMMONLY_STEAM_ENGINE,
    POKEMON_COMMONLY_WIND_RIDER,
    POKEMON_COMMONLY_WELL_BAKED_BODY,
    POKEMON_COMMONLY_STAT_DROP_IMMUNE,
    POKEMON_COMMONLY_MIRROR_ARMOR,
    POKEMON_COMMONLY_FLUFFY,
    POKEMON_COMMONLY_DRY_SKIN,
    POKEMON_WITH_PRANKSTER,
    POKEMON_COMMONLY_AIR_BALLOON,
    POKEMON_COMMONLY_QUEENLY_MAJESTY,
    POKEMON_COMMONLY_DAZZLING,
    POKEMON_COMMONLY_ARMOR_TAIL,
    # NEW: Weather/Terrain constants
    WEATHER_RAIN,
    WEATHER_SUN,
    WEATHER_EXTREME_RAIN,
    WEATHER_EXTREME_SUN,
    TERRAIN_PSYCHIC,
    DAMAGING_GROUND_MOVES,
    UNGROUNDED_ABILITIES,
    UNGROUNDED_ITEMS,
    UNGROUNDED_TYPES,
    POKEMON_COMMONLY_SUPREME_OVERLORD,
    # PHASE 1.1: Positive Boosts Expansion
    PROTECT_MOVES,
    BOOST_CHOICE_LOCKED_RESIST,
    BOOST_CHOICE_LOCKED_IMMUNE,
    BOOST_PROTECT_PUNISH,
    BOOST_OPPONENT_STATUSED,
    BOOST_LOW_HP_PRIORITY,
    PRE_ORB_STATUS_THREAT_MIN_PROB,
    BOOST_PRE_ORB_PROTECT,
    PENALTY_PRE_ORB_NONPROTECT,
    # PHASE 1.2: Trick Room Awareness
    SPEED_BOOSTING_MOVES,
    TRICK_ROOM_MOVES,
    PENALTY_SPEED_BOOST_IN_TR,
    BOOST_SETUP_SLOW_IN_TR,
    # PHASE 1.3: Screens Awareness
    SCREEN_BREAKING_MOVES,
    SCREEN_SETTING_MOVES,
    BOOST_SETUP_VS_SCREENS,
    BOOST_SCREEN_BREAKER,
    # PHASE 1.4: Weather/Terrain Synergies
    POKEMON_WITH_SWIFT_SWIM,
    POKEMON_WITH_SAND_RUSH,
    POKEMON_WITH_SLUSH_RUSH,
    POKEMON_WITH_CHLOROPHYLL,
    SWIFT_SWIM_ABILITIES,
    SAND_RUSH_ABILITIES,
    SLUSH_RUSH_ABILITIES,
    CHLOROPHYLL_ABILITIES,
    WEATHER_SAND,
    WEATHER_SNOW,
    BOOST_TERRAIN_MOVE,
    BOOST_WEATHER_SPEED_ADVANTAGE,
    TERRAIN_ELECTRIC_BOOSTS,
    TERRAIN_GRASSY_BOOSTS,
    TERRAIN_PSYCHIC_BOOSTS,
    GRASSY_TERRAIN_WEAKENED,
    # PHASE 2.1: Switch Evaluation
    POKEMON_WITH_INTIMIDATE_COMMON,
    INTIMIDATE_ABILITIES,
    PENALTY_SWITCH_INTO_HAZARDS_PER_LAYER,
    PENALTY_SWITCH_INTIMIDATE_VS_DEFIANT,
    PENALTY_SWITCH_LOW_HP,
    PENALTY_SWITCH_WEAK_TO_OPPONENT,
    BOOST_SWITCH_RESISTS_STAB,
    BOOST_SWITCH_HAS_RECOVERY,
    BOOST_SWITCH_COUNTERS,
    BOOST_SWITCH_UNAWARE_VS_SETUP,
    POKEMON_RECOVERY_MOVES,
    # PHASE 2.2: Entry Hazard Calculus
    HAZARD_REMOVAL_MOVES,
    HAZARD_SETTING_MOVES,
    POKEMON_SR_4X_WEAK,
    POKEMON_COMMONLY_HDB,
    BOOST_SET_HAZARDS_NO_HAZARDS,
    BOOST_SET_HAZARDS_SR_WEAK_OPP,
    PENALTY_SET_HAZARDS_ALREADY_UP,
    BOOST_REMOVE_HAZARDS_HEAVY,
    PENALTY_REMOVE_HAZARDS_NONE,
    PENALTY_HAZARDS_VS_REMOVAL,
    # PHASE 2.3: Tera Prediction
    DEFENSIVE_TERA_TYPES,
    OFFENSIVE_TERA_TYPES,
    BOOST_COVERAGE_VS_LIKELY_TERA,
    BOOST_STAY_FOR_TERA,
    # PHASE 3.1: Win Condition Awareness
    PENALTY_RISKY_WITH_WINCON,
    BOOST_SAFE_WITH_WINCON,
    PENALTY_SACK_ONLY_CHECK,
    BOOST_REVENGE_KILL_THREAT,
    RISKY_MOVES,
    SAFE_MOVES,
    # PHASE 3.2: PP Tracking
    BOOST_STALL_NO_RECOVERY_PP,
    BOOST_DEFENSIVE_LOW_PP,
    # PHASE 3.3: Momentum Tracking
    MOMENTUM_STRONG_POSITIVE,
    MOMENTUM_POSITIVE,
    MOMENTUM_NEGATIVE,
    MOMENTUM_STRONG_NEGATIVE,
    BOOST_AGGRESSIVE_STRONG_MOMENTUM,
    BOOST_PRESSURE_MOMENTUM,
    BOOST_PIVOT_NEGATIVE_MOMENTUM,
    BOOST_HIGHRISK_DESPERATE,
    AGGRESSIVE_MOVES,
    # PHASE 4.1: Endgame Solver
    ENDGAME_MAX_POKEMON,
)
from fp.battle import Battle
from fp.decision_trace import build_trace_base
from config import FoulPlayConfig
from .standard_battles import prepare_battles
from .random_battles import prepare_random_battles

from poke_engine import State as PokeEngineState, monte_carlo_tree_search, MctsResult

from fp.search.poke_engine_helpers import battle_to_poke_engine_state
from fp.playstyle_config import PlaystyleConfig, Playstyle, HAZARD_MOVES
from fp.helpers import normalize_name, type_effectiveness_modifier
from data.pkmn_sets import ITEM_STRING, EFFECTIVENESS, SmogonSets, TeamDatasets
from fp.team_analysis import analyze_team, TeamAnalysis, REMOVAL_MOVES, SCREEN_MOVES
from fp.playstyle_config import RECOVERY_MOVES, PIVOT_MOVES
from constants_pkg.strategy import SETUP_MOVES, PRIORITY_MOVES
from data import all_move_json
from fp.movepool_tracker import get_threat_category, ThreatCategory
from fp.search.move_validators import filter_blocked_moves
from fp.opponent_model import OPPONENT_MODEL

logger = logging.getLogger(__name__)


# =============================================================================
# GLOBAL PROCESS POOL (CRITICAL FIX FOR ZOMBIE PROCESS LEAK)
# =============================================================================
# PROBLEM: Creating a new ProcessPoolExecutor for every move decision causes
# worker processes to accumulate and leak memory over time.
# SOLUTION: Use a single global executor that's reused across all battles.
# =============================================================================

_global_executor = None
_executor_lock = None  # Will be initialized when needed (avoid threading import at module level)


def _get_executor():
    """Get or create the global ProcessPoolExecutor instance."""
    global _global_executor, _executor_lock
    
    if _executor_lock is None:
        import threading
        _executor_lock = threading.Lock()
    
    with _executor_lock:
        if _global_executor is None:
            logger.info(f"Initializing global ProcessPoolExecutor with {FoulPlayConfig.parallelism} workers")
            _global_executor = ProcessPoolExecutor(max_workers=FoulPlayConfig.parallelism)
            # Register cleanup on exit
            atexit.register(_shutdown_executor)
    
    return _global_executor


def _shutdown_executor():
    """Shutdown the global executor on program exit."""
    global _global_executor
    if _global_executor is not None:
        logger.info("Shutting down global ProcessPoolExecutor...")
        _global_executor.shutdown(wait=True, cancel_futures=True)
        _global_executor = None
        logger.info("Global ProcessPoolExecutor shut down cleanly")


# =============================================================================
# DECISION PROFILES (variance control)
# =============================================================================


class DecisionProfile(Enum):
    DEFAULT = "default"
    LOW = "low"
    HIGH = "high"


def _resolve_playstyle(team_plan: TeamAnalysis | None = None) -> Playstyle:
    """Resolve playstyle from config or team analysis (auto mode)."""
    try:
        if FoulPlayConfig.playstyle == "auto":
            if team_plan is not None:
                return team_plan.playstyle
            return PlaystyleConfig.get_team_playstyle(FoulPlayConfig.team_name or "")
        return Playstyle(FoulPlayConfig.playstyle)
    except Exception:
        return Playstyle.BALANCE


def _select_decision_profile(battle: Battle, playstyle: Playstyle) -> DecisionProfile:
    """Use low-variance profile by default in gen9ou; HO gets high variance."""
    if battle.pokemon_format != "gen9ou":
        return DecisionProfile.DEFAULT
    if playstyle == Playstyle.HYPER_OFFENSE:
        return DecisionProfile.HIGH
    return DecisionProfile.LOW


# =============================================================================
# ABILITY DETECTION
# =============================================================================


@dataclass
class OpponentAbilityState:
    """Tracks what abilities the opponent's active Pokemon has or likely has."""

    # === EXISTING FIELDS ===
    has_unaware: bool = False
    has_guts_like: bool = False  # Guts, Marvel Scale, Quick Feet
    has_poison_heal: bool = False
    has_water_immunity: bool = False  # Water Absorb, Storm Drain, Dry Skin
    has_electric_immunity: bool = False  # Volt Absorb, Lightning Rod, Motor Drive
    has_flash_fire: bool = False
    has_levitate: bool = False
    has_magic_bounce: bool = False
    has_good_as_gold: bool = False  # Blocks ALL status moves
    has_competitive_defiant: bool = False  # Competitive or Defiant
    has_focus_sash: bool = False  # Focus Sash item
    has_phazing: bool = False  # Has revealed a phazing move
    has_substitute: bool = False  # Currently behind a Substitute
    has_contact_punish: bool = False  # Iron Barbs, Rough Skin, or Rocky Helmet
    has_status: bool = False  # Already has a status condition
    is_choice_locked: bool = False  # Opponent is choice-locked into a move
    choice_locked_move: str = ""  # The move they're locked into
    pokemon_name: str = ""
    ability_known: bool = False
    ability_name: str = ""
    at_full_hp: bool = True  # Whether opponent is at full HP (for Sash)

    # === NEW PHASE 2: HIGH PRIORITY ABILITIES ===
    has_contrary: bool = False  # Stat changes reversed (our drops = their boosts!)
    has_sap_sipper: bool = False  # Immune to Grass, +1 Atk
    has_sturdy: bool = False  # Survives any hit at full HP with 1 HP
    has_disguise: bool = False  # Mimikyu's first-hit immunity
    disguise_broken: bool = False  # Whether Disguise has been busted

    # === NEW PHASE 3: MOVE CATEGORY IMMUNITIES ===
    has_soundproof: bool = False  # Immune to sound-based moves
    has_bulletproof: bool = False  # Immune to ball/bullet moves
    has_overcoat: bool = False  # Immune to powder moves + weather
    is_grass_type: bool = False  # Natural powder immunity

    # === NEW PHASE 4-5: TYPE/STAT INTERACTIONS ===
    has_earth_eater: bool = False  # Immune to Ground, heals instead
    has_justified: bool = False  # Dark moves give +1 Atk
    has_steam_engine: bool = False  # Fire/Water give +6 Spe!
    has_wind_rider: bool = False  # Wind moves give +1 Atk
    has_well_baked_body: bool = False  # Fire moves give +2 Def

    # === NEW PHASE 6: STAT IMMUNITY/REFLECTION ===
    has_clear_body: bool = False  # Immune to stat drops (Clear Body/White Smoke/Full Metal Body)
    has_mirror_armor: bool = False  # Reflects stat drops back
    has_supreme_overlord: bool = False  # Atk/SpA boosts per fainted ally

    # === NEW PHASE 7: SPECIAL INTERACTIONS ===
    our_has_prankster: bool = False  # OUR Pokemon has Prankster
    our_has_mold_breaker: bool = False  # OUR Pokemon has Mold Breaker/Teravolt/Turboblaze
    opponent_is_dark_type: bool = False  # Opponent is Dark type (Prankster fails)
    has_air_balloon: bool = False  # Has Air Balloon (Ground immunity until popped)
    has_fluffy: bool = False  # Takes 2x Fire damage (opportunity!)
    has_dry_skin_fire_weak: bool = False  # Takes 1.25x Fire damage (opportunity!)
    has_queenly_majesty: bool = False  # Blocks priority moves
    has_dazzling: bool = False  # Blocks priority moves
    has_armor_tail: bool = False  # Blocks priority moves
    has_priority_block: bool = False  # Any priority-blocking ability

    # === NEW PHASE 8: WEATHER/TERRAIN CONTEXT ===
    weather: str = ""  # Current weather condition
    terrain: str = ""  # Current terrain condition
    opponent_is_grounded: bool = True  # Whether opponent is affected by terrain/ground moves

    # === PHASE 1.1: POSITIVE BOOSTS EXPANSION ===
    opponent_used_protect: bool = False  # Opponent used Protect last turn
    opponent_hp_percent: float = 1.0  # Opponent's HP percentage (for low HP priority)
    opponent_used_setup: bool = False  # Opponent used a setup move last turn
    opponent_used_damaging_move: bool = False  # Opponent used a damaging move last turn

    # === PHASE 1.2: TRICK ROOM AWARENESS ===
    trick_room_active: bool = False  # Trick Room is active
    trick_room_turns: int = 0  # Turns remaining on Trick Room
    our_is_slow: bool = False  # Our active Pokemon is slow (base speed < 60)
    our_speed_stat: int = 0  # Our current speed stat

    # === OUR BOOST STATE (ANTI-REDUNDANT SETUP) ===
    our_attack_boost: int = 0  # Replay: gen9ou-2532366515 turn 55 (Swords Dance at +4)
    our_spa_boost: int = 0  # Replay: gen9ou-2532366515 turn 55 (Swords Dance at +4)

    # === PHASE 1.3: SCREENS AWARENESS ===
    opponent_has_reflect: bool = False  # Opponent has Reflect up
    opponent_has_light_screen: bool = False  # Opponent has Light Screen up
    opponent_has_aurora_veil: bool = False  # Opponent has Aurora Veil up

    # === PHASE 1.4: WEATHER/TERRAIN SYNERGIES ===
    we_have_weather_speed: bool = False  # We have Swift Swim/Sand Rush/etc in matching weather
    terrain_type: str = ""  # electricterrain, grassyterrain, psychicterrain, mistyterrain
    we_are_grounded: bool = True  # Whether we are grounded (for terrain effects)

    # === PHASE 2.1: SWITCH EVALUATION ===
    our_hazard_layers: int = 0  # Total effective hazard layers on our side
    our_sr_up: bool = False  # Stealth Rock on our side
    our_spikes_layers: int = 0  # Spikes layers on our side
    opponent_has_defiant_pokemon: bool = False  # Opponent team has Defiant/Competitive

    # === PHASE 2.2: ENTRY HAZARD CALCULUS ===
    opponent_sr_up: bool = False  # Stealth Rock on opponent's side
    opponent_spikes_layers: int = 0  # Spikes layers on opponent's side
    opponent_has_sr_weak: bool = False  # Opponent has SR-weak Pokemon in back
    opponent_has_hazard_removal: bool = False  # Opponent has revealed Defog/Spin
    opponent_alive_count: int = 6  # Number of opponent Pokemon still alive

    # === PHASE 2.3: TERA PREDICTION ===
    opponent_has_terastallized: bool = False  # Opponent has already used Tera
    we_have_terastallized: bool = False  # We have already used Tera
    opponent_tera_type: str = ""  # Opponent's Tera type if known
    our_tera_type: str = ""  # Our active Pokemon's Tera type

    # === PHASE 3.1: WIN CONDITION AWARENESS ===
    our_active_is_wincon: bool = False  # Our active is a win condition
    opponent_active_is_threat: bool = False  # Opponent active is a major threat
    ko_line_available: bool = False  # We have a simple KO line this turn
    ko_line_turns: int = 0  # 1 or 2 when a KO line is detected
    ko_line_move: str = ""  # Suggested KO move

    # === PHASE 3.2: PP TRACKING ===
    opponent_recovery_pp_exhausted: bool = False  # Opponent recovery PP likely exhausted
    opponent_low_stab_pp: bool = False  # Opponent STAB PP low

    # === HEALING WASTE AVOIDANCE ===
    our_hp_percent: float = 1.0  # Our active HP percentage

    # === PHASE 3.3: MOMENTUM TRACKING ===
    momentum: float = 0.0  # Positive = we have momentum
    momentum_level: str = "neutral"  # strong_positive, positive, neutral, negative, strong_negative
    our_alive_count: int = 6  # Number of our Pokemon still alive


def _check_ability_or_pokemon(
    ability: str | None,
    pokemon_name: str,
    base_name: str | None,
    ability_names: set[str],
    pokemon_set: set[str],
) -> bool:
    """
    Check if a Pokemon has a specific ability, either known or inferred.

    Args:
        ability: The known ability (or None if unknown)
        pokemon_name: The Pokemon's name
        base_name: The Pokemon's base name (for forme variations)
        ability_names: Set of ability names to check against
        pokemon_set: Set of Pokemon that commonly have these abilities
    """
    if ability:
        return ability in ability_names

    # If ability is unknown, check if Pokemon commonly has it
    return pokemon_name in pokemon_set or (base_name and base_name in pokemon_set)


def calculate_momentum(battle: Battle) -> tuple[float, str]:
    """
    Calculate momentum score for the battle.
    Returns (momentum_score, momentum_level).
    Positive = we have advantage, Negative = opponent has advantage.
    """
    momentum = 0.0

    # HP advantage (sum of HP percentages)
    our_pokemon = [battle.user.active] + battle.user.reserve if battle.user.active else battle.user.reserve
    opp_pokemon = [battle.opponent.active] + battle.opponent.reserve if battle.opponent.active else battle.opponent.reserve

    our_hp = sum(p.hp / max(p.max_hp, 1) for p in our_pokemon if p and p.hp > 0)
    opp_hp = sum(p.hp / max(p.max_hp, 1) for p in opp_pokemon if p and p.hp > 0)
    momentum += (our_hp - opp_hp) * 0.5

    # Pokemon count advantage
    our_alive = sum(1 for p in our_pokemon if p and p.hp > 0)
    opp_alive = sum(1 for p in opp_pokemon if p and p.hp > 0)
    momentum += (our_alive - opp_alive) * 1.5

    # Hazard advantage (hazards on opponent = good for us)
    our_hazards = (
        (2 if battle.user.side_conditions.get(constants.STEALTH_ROCK, 0) > 0 else 0)
        + battle.user.side_conditions.get(constants.SPIKES, 0)
    )
    opp_hazards = (
        (2 if battle.opponent.side_conditions.get(constants.STEALTH_ROCK, 0) > 0 else 0)
        + battle.opponent.side_conditions.get(constants.SPIKES, 0)
    )
    momentum += (opp_hazards - our_hazards) * 0.5

    # Categorize momentum level
    if momentum >= MOMENTUM_STRONG_POSITIVE:
        level = "strong_positive"
    elif momentum >= MOMENTUM_POSITIVE:
        level = "positive"
    elif momentum <= MOMENTUM_STRONG_NEGATIVE:
        level = "strong_negative"
    elif momentum <= MOMENTUM_NEGATIVE:
        level = "negative"
    else:
        level = "neutral"

    return momentum, level


def is_likely_wincon(pokemon, battle: Battle) -> bool:
    """Check if a Pokemon is likely a win condition based on its characteristics."""
    if pokemon is None or pokemon.hp <= 0:
        return False

    score = 0

    # Has setup move
    pokemon_moves = [m.name if hasattr(m, "name") else str(m) for m in getattr(pokemon, "moves", [])]
    for move in pokemon_moves:
        if move in SETUP_MOVES:
            score += 3
            break

    # High speed (potential sweeper)
    speed = pokemon.stats.get(constants.SPEED, 50) if hasattr(pokemon, "stats") else 50
    if speed > 100:
        score += 2

    # High offensive stats
    atk = pokemon.stats.get(constants.ATTACK, 50) if hasattr(pokemon, "stats") else 50
    spa = pokemon.stats.get(constants.SPECIAL_ATTACK, 50) if hasattr(pokemon, "stats") else 50
    if atk > 120 or spa > 120:
        score += 1

    # Already has boosts
    boosts = getattr(pokemon, "boosts", {}) or {}
    if boosts.get(constants.ATTACK, 0) > 0 or boosts.get(constants.SPECIAL_ATTACK, 0) > 0:
        score += 2

    return score >= 4


def detect_odd_move(
    battle: Battle,
    move_choice: str,
    ability_state: OpponentAbilityState | None = None,
) -> list[str]:
    """Return a list of odd/wasted-turn heuristics triggered by the move."""
    if not move_choice:
        return []
    move_name = move_choice.split(":")[-1] if ":" in move_choice else move_choice
    oddities = []

    if ability_state is not None:
        # Hazard removal with no hazards
        if move_name in HAZARD_REMOVAL_MOVES and ability_state.our_hazard_layers <= 0:
            oddities.append("waste_turn:remove_hazards_no_hazards")
            last_move = getattr(battle.user.last_selected_move, "move", "")
            if normalize_name(last_move) == normalize_name(move_name):
                oddities.append("waste_turn:repeat_hazard_removal")

        # Full-HP recovery
        if move_name in RECOVERY_MOVES and ability_state.our_hp_percent >= 0.95:
            oddities.append("waste_turn:full_hp_recovery")

        # Status moves into known immunities/blocks
        if move_name in ALL_STATUS_MOVES:
            if ability_state.has_good_as_gold:
                oddities.append("waste_turn:status_blocked_good_as_gold")
            if ability_state.has_magic_bounce and move_name in MAGIC_BOUNCE_REFLECTED_MOVES:
                oddities.append("waste_turn:status_reflected_magic_bounce")

    # Future Sight/Doom Desire already pending on opponent side
    if move_name in {constants.FUTURE_SIGHT, "doomdesire"}:
        try:
            if battle.opponent.future_sight[0] > 0:
                oddities.append("waste_turn:future_sight_already_pending")
        except Exception:
            pass

    # Repeating a non-damaging move on consecutive turns
    move_data = all_move_json.get(move_name, {})
    if move_data.get(constants.CATEGORY) == constants.STATUS:
        last_move = getattr(battle.user.last_selected_move, "move", "")
        if normalize_name(last_move) == normalize_name(move_name):
            oddities.append("waste_turn:repeat_status_move")

    return oddities


def _log_oddities(choice: str, oddities: list[str]):
    if oddities:
        logger.warning(f"Odd move detected: {choice} -> {', '.join(oddities)}")


def find_ko_line(battle: Battle) -> dict | None:
    """Detect a simple 1-2 turn KO line for the current active matchup."""
    if battle is None or battle.user.active is None or battle.opponent.active is None:
        return None

    our = battle.user.active
    opp = battle.opponent.active
    move_names = [
        m.name if hasattr(m, "name") else str(m) for m in getattr(our, "moves", [])
    ]
    if not move_names:
        return None

    try:
        from fp.search.endgame import estimate_damage
    except Exception:
        return None

    try:
        our_speed = battle.get_effective_speed(battle.user)
        opp_speed = battle.get_effective_speed(battle.opponent)
        outspeed = our_speed > opp_speed
    except Exception:
        outspeed = False

    best_one_shot = None
    for move_name in move_names:
        damage = estimate_damage(our, opp, move_name)
        if damage >= 1.0 and (outspeed or move_name in PRIORITY_MOVES):
            if best_one_shot is None or damage > best_one_shot["damage"]:
                best_one_shot = {
                    "turns": 1,
                    "move": move_name,
                    "damage": round(damage, 3),
                    "outspeed": outspeed,
                }
    if best_one_shot:
        return best_one_shot

    best_two_shot = None
    if outspeed:
        for move_name in move_names:
            damage = estimate_damage(our, opp, move_name)
            if damage >= 0.5:
                if best_two_shot is None or damage > best_two_shot["damage"]:
                    best_two_shot = {
                        "turns": 2,
                        "move": move_name,
                        "damage": round(damage, 3),
                        "outspeed": outspeed,
                    }
    return best_two_shot


def detect_opponent_abilities(battle: Battle) -> OpponentAbilityState:
    """
    Analyze the opponent's active Pokemon to detect relevant abilities.

    Returns an OpponentAbilityState with flags for each ability category.
    """
    state = OpponentAbilityState()

    if battle.opponent.active is None:
        return state

    opponent = battle.opponent.active
    ability = opponent.ability
    name = opponent.name
    base_name = getattr(opponent, "base_name", None)
    item = getattr(opponent, "item", None)

    state.pokemon_name = name
    state.ability_known = bool(ability)
    state.ability_name = ability or "unknown"

    # Check if our Pokemon has Mold Breaker (ignores defensive abilities)
    our_has_mold_breaker = False
    our_has_prankster = False
    if battle.user.active is not None:
        our_ability = battle.user.active.ability
        if our_ability:
            if our_ability in MOLD_BREAKER_ABILITIES:
                our_has_mold_breaker = True
            if our_ability == "prankster":
                our_has_prankster = True

    state.our_has_mold_breaker = our_has_mold_breaker
    state.our_has_prankster = our_has_prankster

    # Track our current offensive boosts (anti-redundant setup)
    if battle.user.active is not None:
        boosts = getattr(battle.user.active, "boosts", {}) or {}
        state.our_attack_boost = int(boosts.get(constants.ATTACK, 0) or 0)
        state.our_spa_boost = int(boosts.get(constants.SPECIAL_ATTACK, 0) or 0)

    # === WEATHER AND TERRAIN DETECTION ===
    state.weather = getattr(battle, "weather", "") or ""
    state.terrain = getattr(battle, "field", "") or ""

    # === OPPONENT TYPE DETECTION ===
    opponent_types = getattr(opponent, "types", []) or []
    state.opponent_is_dark_type = "dark" in opponent_types
    state.is_grass_type = "grass" in opponent_types

    # === GROUNDED CHECK ===
    # Pokemon is grounded unless: Flying type, Levitate, Air Balloon, or Magnet Rise
    state.opponent_is_grounded = True
    if "flying" in opponent_types:
        state.opponent_is_grounded = False
    elif ability and ability in UNGROUNDED_ABILITIES:
        state.opponent_is_grounded = False
    elif item and item in UNGROUNDED_ITEMS:
        state.opponent_is_grounded = False
    # Check volatile statuses for Magnet Rise, Telekinesis
    volatile_statuses = getattr(opponent, "volatile_statuses", []) or []
    if "magnetrise" in volatile_statuses or "telekinesis" in volatile_statuses:
        state.opponent_is_grounded = False

    # === EXISTING ABILITY DETECTION ===

    # Unaware
    state.has_unaware = _check_ability_or_pokemon(
        ability, name, base_name, {"unaware"}, POKEMON_COMMONLY_UNAWARE
    )

    # Guts-like abilities (status boosts them)
    state.has_guts_like = _check_ability_or_pokemon(
        ability,
        name,
        base_name,
        {"guts", "marvelscale", "quickfeet"},
        POKEMON_STATUS_BACKFIRES,
    )

    # Poison Heal
    state.has_poison_heal = _check_ability_or_pokemon(
        ability, name, base_name, {"poisonheal"}, POKEMON_COMMONLY_POISON_HEAL
    )

    # Type immunities - Mold Breaker bypasses these
    if not our_has_mold_breaker:
        # Water immunity
        state.has_water_immunity = _check_ability_or_pokemon(
            ability,
            name,
            base_name,
            {"waterabsorb", "stormdrain", "dryskin"},
            POKEMON_COMMONLY_WATER_IMMUNE,
        )

        # Electric immunity
        state.has_electric_immunity = _check_ability_or_pokemon(
            ability,
            name,
            base_name,
            {"voltabsorb", "lightningrod", "motordrive"},
            POKEMON_COMMONLY_ELECTRIC_IMMUNE,
        )

        # Flash Fire
        state.has_flash_fire = _check_ability_or_pokemon(
            ability, name, base_name, {"flashfire"}, POKEMON_COMMONLY_FLASH_FIRE
        )

        # Levitate
        state.has_levitate = _check_ability_or_pokemon(
            ability, name, base_name, {"levitate"}, POKEMON_COMMONLY_LEVITATE
        )

        # NEW: Sap Sipper (Grass immunity + Atk boost)
        state.has_sap_sipper = _check_ability_or_pokemon(
            ability, name, base_name, {"sapsipper"}, POKEMON_COMMONLY_SAP_SIPPER
        )

        # NEW: Earth Eater (Ground immunity + heal)
        state.has_earth_eater = _check_ability_or_pokemon(
            ability, name, base_name, {"eartheater"}, POKEMON_COMMONLY_EARTH_EATER
        )

        # NEW: Soundproof (Sound move immunity)
        state.has_soundproof = _check_ability_or_pokemon(
            ability, name, base_name, {"soundproof"}, POKEMON_COMMONLY_SOUNDPROOF
        )

        # NEW: Bulletproof (Bullet/ball move immunity)
        state.has_bulletproof = _check_ability_or_pokemon(
            ability, name, base_name, {"bulletproof"}, POKEMON_COMMONLY_BULLETPROOF
        )

        # NEW: Overcoat (Powder move immunity)
        state.has_overcoat = _check_ability_or_pokemon(
            ability, name, base_name, {"overcoat"}, POKEMON_COMMONLY_OVERCOAT
        )


        # NEW: Sturdy (Sash-like effect at full HP)
        state.has_sturdy = _check_ability_or_pokemon(
            ability, name, base_name, {"sturdy"}, POKEMON_COMMONLY_STURDY
        )

        # NEW: Fluffy (2x Fire damage - opportunity!)
        state.has_fluffy = _check_ability_or_pokemon(
            ability, name, base_name, {"fluffy"}, POKEMON_COMMONLY_FLUFFY
        )

        # NEW: Dry Skin fire weakness (1.25x Fire - opportunity!)
        # Note: Dry Skin also gives water immunity, handled above
        if _check_ability_or_pokemon(
            ability, name, base_name, {"dryskin"}, POKEMON_COMMONLY_DRY_SKIN
        ):
            state.has_dry_skin_fire_weak = True

        # NEW: Wind Rider (Wind move immunity + Atk boost)
        state.has_wind_rider = _check_ability_or_pokemon(
            ability, name, base_name, {"windrider"}, POKEMON_COMMONLY_WIND_RIDER
        )

        # NEW: Well-Baked Body (Fire immunity + Def boost)
        state.has_well_baked_body = _check_ability_or_pokemon(
            ability, name, base_name, {"wellbakedbody"}, POKEMON_COMMONLY_WELL_BAKED_BODY
        )
    else:
        logger.info(
            f"Our Pokemon has Mold Breaker - ignoring type-immunity abilities"
        )

    # Magic Bounce - NOT affected by Mold Breaker for reflected moves
    state.has_magic_bounce = _check_ability_or_pokemon(
        ability, name, base_name, {"magicbounce"}, POKEMON_COMMONLY_MAGIC_BOUNCE
    )

    # Good as Gold - Blocks ALL status moves (not affected by Mold Breaker)
    state.has_good_as_gold = _check_ability_or_pokemon(
        ability, name, base_name, {"goodasgold"}, POKEMON_COMMONLY_GOOD_AS_GOLD
    )

    # Priority-blocking abilities (not bypassed by Mold Breaker)
    state.has_queenly_majesty = _check_ability_or_pokemon(
        ability, name, base_name, {"queenlymajesty"}, POKEMON_COMMONLY_QUEENLY_MAJESTY
    )
    state.has_dazzling = _check_ability_or_pokemon(
        ability, name, base_name, {"dazzling"}, POKEMON_COMMONLY_DAZZLING
    )
    state.has_armor_tail = _check_ability_or_pokemon(
        ability, name, base_name, {"armortail"}, POKEMON_COMMONLY_ARMOR_TAIL
    )
    state.has_priority_block = (
        state.has_queenly_majesty or state.has_dazzling or state.has_armor_tail
    )

    # Competitive/Defiant
    state.has_competitive_defiant = _check_ability_or_pokemon(
        ability,
        name,
        base_name,
        {"competitive", "defiant"},
        POKEMON_STAT_DROP_BACKFIRES,
    )

    # === NEW ABILITY DETECTION (NOT BYPASSED BY MOLD BREAKER) ===

    # Contrary - stat changes reversed (affects their own stats, not our attack)
    state.has_contrary = _check_ability_or_pokemon(
        ability, name, base_name, {"contrary"}, POKEMON_COMMONLY_CONTRARY
    )

    # Disguise - Mimikyu's first-hit immunity (form change, not damage prevention)
    state.has_disguise = _check_ability_or_pokemon(
        ability, name, base_name, {"disguise"}, POKEMON_COMMONLY_DISGUISE
    )
    # Check if Disguise has been busted (Mimikyu-Busted forme)
    if state.has_disguise:
        if "busted" in name.lower() or "disguisebroken" in volatile_statuses:
            state.disguise_broken = True

    # Mirror Armor - reflects stat drops (different mechanic than damage prevention)
    state.has_mirror_armor = _check_ability_or_pokemon(
        ability, name, base_name, {"mirrorarmor"}, POKEMON_COMMONLY_MIRROR_ARMOR
    )

    # Supreme Overlord - Kingambit (gets stronger as allies faint)
    state.has_supreme_overlord = _check_ability_or_pokemon(
        ability, name, base_name, {"supremeoverlord"}, POKEMON_COMMONLY_SUPREME_OVERLORD
    )

    # Clear Body / White Smoke / Full Metal Body - stat drop immunity
    state.has_clear_body = _check_ability_or_pokemon(
        ability, name, base_name,
        {"clearbody", "whitesmoke", "fullmetalbody"},
        POKEMON_COMMONLY_STAT_DROP_IMMUNE
    )

    # Justified - Dark moves boost their Attack
    state.has_justified = _check_ability_or_pokemon(
        ability, name, base_name, {"justified"}, POKEMON_COMMONLY_JUSTIFIED
    )

    # Steam Engine - Fire/Water moves give +6 Speed!
    state.has_steam_engine = _check_ability_or_pokemon(
        ability, name, base_name, {"steamengine"}, POKEMON_COMMONLY_STEAM_ENGINE
    )

    # === FOCUS SASH DETECTION ===
    if item == "focussash":
        state.has_focus_sash = True
    elif item in (None, "unknownitem", "") and name in POKEMON_COMMONLY_FOCUS_SASH:
        state.has_focus_sash = True

    # Check if opponent is at full HP (Sash/Sturdy only works at full HP)
    state.at_full_hp = opponent.hp == opponent.max_hp

    # === AIR BALLOON DETECTION ===
    if item == "airballoon":
        state.has_air_balloon = True
    elif item in (None, "unknownitem", "") and name in POKEMON_COMMONLY_AIR_BALLOON:
        state.has_air_balloon = True

    # === PHAZING DETECTION ===
    for move in opponent.moves:
        move_name = move.name if hasattr(move, "name") else str(move)
        if move_name in PHAZING_MOVES:
            state.has_phazing = True
            break

    # === SUBSTITUTE DETECTION ===
    if "substitute" in volatile_statuses:
        state.has_substitute = True

    # === CONTACT PUNISHMENT DETECTION ===
    if ability and ability in {"ironbarbs", "roughskin"}:
        state.has_contact_punish = True
    elif item and item == "rockyhelmet":
        state.has_contact_punish = True
    elif (
        name in POKEMON_COMMONLY_IRON_BARBS
        or name in POKEMON_COMMONLY_ROUGH_SKIN
        or name in POKEMON_COMMONLY_ROCKY_HELMET
    ):
        state.has_contact_punish = True

    # === STATUS DETECTION ===
    opponent_status = getattr(opponent, "status", None)
    if opponent_status is not None:
        state.has_status = True

    # === CHOICE-LOCK DETECTION ===
    opp_battler = battle.opponent
    opp_item = getattr(opponent, "item", None)
    if opp_item in ("choiceband", "choicescarf", "choicespecs"):
        last_move = opp_battler.last_used_move
        if (
            last_move
            and last_move.pokemon_name == opponent.name
            and last_move.move
            and last_move.move not in ("trick", "switcheroo")
        ):
            state.is_choice_locked = True
            state.choice_locked_move = last_move.move

    # =========================================================================
    # PHASE 1.1: POSITIVE BOOSTS EXPANSION
    # =========================================================================

    # Detect if opponent used Protect last turn
    opp_last_move = opp_battler.last_used_move
    if opp_last_move and opp_last_move.move:
        if opp_last_move.move in PROTECT_MOVES:
            state.opponent_used_protect = True
        if opp_last_move.move in SETUP_MOVES:
            state.opponent_used_setup = True
        move_key = normalize_name(opp_last_move.move)
        move_data = all_move_json.get(move_key, {})
        move_category = move_data.get(constants.CATEGORY)
        if move_category and move_category != constants.STATUS:
            state.opponent_used_damaging_move = True

    # Track opponent HP percentage
    if opponent.max_hp > 0:
        state.opponent_hp_percent = opponent.hp / opponent.max_hp

    # Track our HP percentage (for avoiding wasteful recovery)
    if battle.user.active is not None and battle.user.active.max_hp > 0:
        state.our_hp_percent = battle.user.active.hp / battle.user.active.max_hp

    # =========================================================================
    # PHASE 1.2: TRICK ROOM AWARENESS
    # =========================================================================

    # Trick Room detection
    trick_room = getattr(battle, "trick_room", False)
    if trick_room:
        state.trick_room_active = True
        state.trick_room_turns = getattr(battle, "trick_room_turns_remaining", 0)

    # Our speed detection (for slow Pokemon under TR)
    if battle.user.active is not None:
        our_pokemon = battle.user.active
        our_speed = getattr(our_pokemon, "speed", 0) or our_pokemon.stats.get(constants.SPEED, 0) if hasattr(our_pokemon, "stats") else 0
        if our_speed == 0:
            # Try base stats
            our_speed = getattr(our_pokemon, "base_speed", 50)
        state.our_speed_stat = our_speed
        # Consider "slow" if base speed < 60 (typical for TR abusers)
        state.our_is_slow = our_speed < 60

    # =========================================================================
    # PHASE 1.3: SCREENS AWARENESS
    # =========================================================================

    # Check opponent's side conditions for screens
    opp_side = getattr(battle.opponent, "side_conditions", {})
    if opp_side:
        state.opponent_has_reflect = opp_side.get(constants.REFLECT, 0) > 0
        state.opponent_has_light_screen = opp_side.get(constants.LIGHT_SCREEN, 0) > 0
        state.opponent_has_aurora_veil = opp_side.get(constants.AURORA_VEIL, 0) > 0

    # =========================================================================
    # PHASE 1.4: WEATHER/TERRAIN SYNERGIES
    # =========================================================================

    # Check if we have a weather-speed ability in matching weather
    if battle.user.active is not None:
        our_pokemon = battle.user.active
        our_ability = getattr(our_pokemon, "ability", None)
        our_name = our_pokemon.name

        # Swift Swim in Rain
        if state.weather in WEATHER_RAIN or state.weather == "raindance":
            if our_ability in SWIFT_SWIM_ABILITIES or our_name in POKEMON_WITH_SWIFT_SWIM:
                state.we_have_weather_speed = True

        # Sand Rush in Sand
        if state.weather in WEATHER_SAND or state.weather == "sandstorm":
            if our_ability in SAND_RUSH_ABILITIES or our_name in POKEMON_WITH_SAND_RUSH:
                state.we_have_weather_speed = True

        # Slush Rush in Snow/Hail
        if state.weather in WEATHER_SNOW or state.weather in ("snow", "hail"):
            if our_ability in SLUSH_RUSH_ABILITIES or our_name in POKEMON_WITH_SLUSH_RUSH:
                state.we_have_weather_speed = True

        # Chlorophyll in Sun
        if state.weather in WEATHER_SUN or state.weather == "sunnyday":
            if our_ability in CHLOROPHYLL_ABILITIES or our_name in POKEMON_WITH_CHLOROPHYLL:
                state.we_have_weather_speed = True

        # Check if we're grounded
        our_types = getattr(our_pokemon, "types", []) or []
        state.we_are_grounded = True
        if "flying" in our_types:
            state.we_are_grounded = False
        elif our_ability and our_ability in UNGROUNDED_ABILITIES:
            state.we_are_grounded = False
        elif hasattr(our_pokemon, "item") and our_pokemon.item in UNGROUNDED_ITEMS:
            state.we_are_grounded = False

    # Store terrain type for move boosts
    state.terrain_type = state.terrain

    # =========================================================================
    # PHASE 2.1: SWITCH EVALUATION
    # =========================================================================

    # Count hazard layers on our side
    our_side = getattr(battle.user, "side_conditions", {})
    if our_side:
        state.our_sr_up = our_side.get(constants.STEALTH_ROCK, 0) > 0
        state.our_spikes_layers = our_side.get(constants.SPIKES, 0)
        # Effective hazard layers (SR counts as ~2 layers worth of damage)
        state.our_hazard_layers = (2 if state.our_sr_up else 0) + state.our_spikes_layers

    # Check if opponent team has Defiant/Competitive (for Intimidate awareness)
    for opp_pokemon in battle.opponent.reserve + ([battle.opponent.active] if battle.opponent.active else []):
        if opp_pokemon is None or opp_pokemon.hp <= 0:
            continue
        opp_ability = getattr(opp_pokemon, "ability", None)
        opp_name = opp_pokemon.name
        if opp_ability in {"defiant", "competitive"}:
            state.opponent_has_defiant_pokemon = True
            break
        if opp_name in POKEMON_STAT_DROP_BACKFIRES:
            state.opponent_has_defiant_pokemon = True
            break

    # =========================================================================
    # PHASE 2.2: ENTRY HAZARD CALCULUS
    # =========================================================================

    # Check opponent's side conditions
    if opp_side:
        state.opponent_sr_up = opp_side.get(constants.STEALTH_ROCK, 0) > 0
        state.opponent_spikes_layers = opp_side.get(constants.SPIKES, 0)

    # Count opponent's alive Pokemon
    alive_count = 0
    has_sr_weak = False
    has_hazard_removal = False

    for opp_pokemon in battle.opponent.reserve + ([battle.opponent.active] if battle.opponent.active else []):
        if opp_pokemon is None:
            continue
        if opp_pokemon.hp > 0:
            alive_count += 1

        # Check for SR-weak Pokemon (not wearing HDB)
        opp_name = opp_pokemon.name
        opp_item = getattr(opp_pokemon, "item", None)
        if opp_name in POKEMON_SR_4X_WEAK:
            if opp_item != "heavydutyboots" and opp_name not in POKEMON_COMMONLY_HDB:
                has_sr_weak = True

        # Check for hazard removal moves
        for move in getattr(opp_pokemon, "moves", []):
            move_name = move.name if hasattr(move, "name") else str(move)
            if move_name in HAZARD_REMOVAL_MOVES:
                has_hazard_removal = True

    state.opponent_alive_count = alive_count
    state.opponent_has_sr_weak = has_sr_weak
    state.opponent_has_hazard_removal = has_hazard_removal

    # =========================================================================
    # PHASE 2.3: TERA PREDICTION
    # =========================================================================

    # Check if opponent has Terastallized
    if opponent:
        opp_tera = getattr(opponent, "terastallized", False)
        state.opponent_has_terastallized = bool(opp_tera)
        if opp_tera:
            state.opponent_tera_type = getattr(opponent, "tera_type", "") or ""

    # Check if we have Terastallized
    if battle.user.active is not None:
        our_tera = getattr(battle.user.active, "terastallized", False)
        state.we_have_terastallized = bool(our_tera)
        state.our_tera_type = getattr(battle.user.active, "tera_type", "") or ""

    # Check whole battle for Tera usage
    if not state.opponent_has_terastallized:
        # Check if any opponent Pokemon shows terastallized
        for opp_pokemon in battle.opponent.reserve:
            if opp_pokemon and getattr(opp_pokemon, "terastallized", False):
                state.opponent_has_terastallized = True
                break

    # =========================================================================
    # PHASE 3.1: WIN CONDITION AWARENESS
    # =========================================================================

    # Check if our active Pokemon is a win condition
    if battle.user.active is not None:
        state.our_active_is_wincon = is_likely_wincon(battle.user.active, battle)

    # Check if opponent's active is a major threat (has setup boosts or high power)
    if opponent:
        opp_boosts = getattr(opponent, "boosts", {}) or {}
        atk_boost = opp_boosts.get(constants.ATTACK, 0)
        spa_boost = opp_boosts.get(constants.SPECIAL_ATTACK, 0)
        spe_boost = opp_boosts.get(constants.SPEED, 0)
        atk_stat = opponent.stats.get(constants.ATTACK, 0) if hasattr(opponent, "stats") else 0
        spa_stat = opponent.stats.get(constants.SPECIAL_ATTACK, 0) if hasattr(opponent, "stats") else 0
        spe_stat = opponent.stats.get(constants.SPEED, 0) if hasattr(opponent, "stats") else 0

        has_attack_boost = atk_boost >= 2 or spa_boost >= 2
        has_attack_boost |= (atk_boost >= 1 and atk_stat >= 100)
        has_attack_boost |= (spa_boost >= 1 and spa_stat >= 100)
        has_speed_threat = spe_boost >= 1 and spe_stat >= 90

        if has_attack_boost or has_speed_threat:
            state.opponent_active_is_threat = True
        elif is_likely_wincon(opponent, battle):
            state.opponent_active_is_threat = True

    # =========================================================================
    # PHASE 3.2: PP TRACKING
    # =========================================================================

    # Estimate opponent recovery PP status
    recovery_moves = []
    for move in getattr(opponent, "moves", []):
        move_name = move.name if hasattr(move, "name") else str(move)
        if move_name in POKEMON_RECOVERY_MOVES:
            recovery_moves.append(move_name)

    if recovery_moves:
        exhausted = True
        for move_name in recovery_moves:
            min_pp, max_pp = opponent.estimate_pp_remaining(move_name)
            if max_pp is None:
                # Unknown PP data - be conservative
                exhausted = False
                break
            if max_pp > 0:
                exhausted = False
                break
        state.opponent_recovery_pp_exhausted = exhausted

    # Estimate if opponent's STAB PP is low
    stab_moves = []
    for move in getattr(opponent, "moves", []):
        move_name = move.name if hasattr(move, "name") else str(move)
        move_data = all_move_json.get(move_name)
        if move_data is None and move_name.startswith(constants.HIDDEN_POWER):
            move_data = all_move_json.get(constants.HIDDEN_POWER)
        if move_data is None:
            continue
        move_type = move_data.get(constants.TYPE)
        if move_type and move_type in opponent_types:
            stab_moves.append(move_name)

    if stab_moves:
        low_stab = True
        for move_name in stab_moves:
            _min_pp, max_pp = opponent.estimate_pp_remaining(move_name)
            if max_pp is None:
                low_stab = False
                break
            if max_pp > 2:
                low_stab = False
                break
        state.opponent_low_stab_pp = low_stab

    # =========================================================================
    # PHASE 3.3: MOMENTUM TRACKING
    # =========================================================================

    state.momentum, state.momentum_level = calculate_momentum(battle)

    # Count our alive Pokemon
    our_pokemon = [battle.user.active] + battle.user.reserve if battle.user.active else battle.user.reserve
    state.our_alive_count = sum(1 for p in our_pokemon if p and p.hp > 0)

    return state


# =============================================================================
# PENALTY APPLICATION
# =============================================================================


def apply_ko_line_bias(
    policy: dict[str, float],
    ability_state: OpponentAbilityState,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """If a KO line exists, strongly prefer damaging actions and the KO move."""
    if not ability_state.ko_line_available:
        return policy

    adjusted = {}
    ko_move = ability_state.ko_line_move
    turns = ability_state.ko_line_turns
    for move, weight in policy.items():
        move_name = move.split(":")[-1] if ":" in move else move
        move_data = all_move_json.get(move_name, {})
        cat = move_data.get(constants.CATEGORY)

        new_weight = weight
        if move_name == ko_move:
            new_weight *= 1.2
            if trace_events is not None:
                trace_events.append(
                    {
                        "type": "boost",
                        "source": "ko_line",
                        "move": move,
                        "reason": "ko_line_move",
                        "before": weight,
                        "after": new_weight,
                    }
                )
        elif turns == 1:
            if move_name in HAZARD_REMOVAL_MOVES or move_name in HAZARD_SETTING_MOVES:
                new_weight *= 0.05
            elif move_name in RECOVERY_MOVES:
                new_weight *= 0.1
            elif cat == constants.STATUS:
                new_weight *= 0.3
        elif turns == 2:
            if move_name in HAZARD_REMOVAL_MOVES or move_name in HAZARD_SETTING_MOVES:
                new_weight *= 0.2
            elif move_name in RECOVERY_MOVES:
                new_weight *= 0.4

        adjusted[move] = new_weight
    return adjusted


def apply_heuristic_bias(
    policy: dict[str, float],
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """Inject a small heuristic score into the MCTS policy."""
    if battle is None or ability_state is None:
        return policy

    adjusted = {}
    for move, weight in policy.items():
        move_name = move.split(":")[-1] if ":" in move else move
        move_data = all_move_json.get(move_name, {})
        cat = move_data.get(constants.CATEGORY)
        heuristic = 0.0

        # Finish low HP targets
        if cat in {constants.PHYSICAL, constants.SPECIAL} and ability_state.opponent_hp_percent <= 0.35:
            heuristic += 0.2

        # Recover when truly low
        if move_name in RECOVERY_MOVES and ability_state.our_hp_percent <= 0.4:
            heuristic += 0.2

        # Hazard tempo when opponent has many Pokemon
        if move_name in HAZARD_SETTING_MOVES and ability_state.opponent_alive_count >= 4:
            heuristic += 0.1

        # Pivot when momentum is negative
        if move_name in {"uturn", "voltswitch", "flipturn", "partingshot"} and ability_state.momentum_level in {
            "negative",
            "strong_negative",
        }:
            heuristic += 0.1

        multiplier = 1.0 + (heuristic * 0.05)
        new_weight = weight * multiplier
        adjusted[move] = new_weight

        if trace_events is not None and abs(multiplier - 1.0) > 1e-6:
            trace_events.append(
                {
                    "type": "heuristic",
                    "source": "heuristic_bias",
                    "move": move,
                    "reason": "heuristic_adjustment",
                    "before": weight,
                    "after": new_weight,
                    "score": round(heuristic, 3),
                }
            )

    return adjusted


def apply_ability_penalties(
    final_policy: dict[str, float],
    ability_state: OpponentAbilityState,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """
    Apply penalties to moves based on opponent's abilities.

    This function handles all ability-based move penalties in one pass.
    """
    # Quick exit if no problematic abilities/states detected
    if not any(
        [
            # Existing checks
            ability_state.has_unaware,
            ability_state.has_guts_like,
            ability_state.has_poison_heal,
            ability_state.has_water_immunity,
            ability_state.has_electric_immunity,
            ability_state.has_flash_fire,
            ability_state.has_levitate,
            ability_state.has_magic_bounce,
            ability_state.has_competitive_defiant,
            ability_state.has_focus_sash and ability_state.at_full_hp,
            ability_state.has_phazing,
            ability_state.has_substitute,
            ability_state.has_contact_punish,
            ability_state.has_status,
            ability_state.is_choice_locked,
            # NEW checks
            ability_state.has_contrary,
            ability_state.has_sap_sipper,
            ability_state.has_sturdy and ability_state.at_full_hp,
            ability_state.has_disguise and not ability_state.disguise_broken,
            ability_state.has_soundproof,
            ability_state.has_bulletproof,
            ability_state.has_overcoat,
            ability_state.is_grass_type,  # Powder immunity
            ability_state.has_earth_eater,
            ability_state.has_justified,
            ability_state.has_steam_engine,
            ability_state.has_wind_rider,
            ability_state.has_well_baked_body,
            ability_state.has_clear_body,
            ability_state.has_mirror_armor,
            ability_state.has_supreme_overlord,
            ability_state.our_has_prankster and ability_state.opponent_is_dark_type,
            ability_state.has_air_balloon,
            ability_state.has_fluffy,
            ability_state.has_dry_skin_fire_weak,
            # Weather/Terrain
            ability_state.weather in WEATHER_EXTREME_RAIN,
            ability_state.weather in WEATHER_EXTREME_SUN,
            ability_state.weather in WEATHER_RAIN,
            ability_state.weather in WEATHER_SUN,
            ability_state.terrain in TERRAIN_PSYCHIC,
              # PHASE 1.1: Positive Boosts
              ability_state.opponent_used_protect,
              ability_state.opponent_used_setup,
              ability_state.opponent_hp_percent < 0.25,
              ability_state.opponent_used_damaging_move and ability_state.our_hp_percent <= 0.5,
            # PHASE 1.2: Trick Room
            ability_state.trick_room_active,
            # PHASE 1.3: Screens
            ability_state.opponent_has_reflect,
            ability_state.opponent_has_light_screen,
            ability_state.opponent_has_aurora_veil,
            # PHASE 1.4: Weather/Terrain Synergies
            ability_state.we_have_weather_speed,
            ability_state.terrain_type != "",
            # PHASE 2.2: Entry Hazard Calculus
            ability_state.opponent_alive_count >= 4,  # SR valuable
            ability_state.opponent_has_sr_weak,
            ability_state.opponent_sr_up,  # Don't double-set
            ability_state.our_hazard_layers >= 2,  # Need removal
            ability_state.our_hazard_layers == 0,  # Avoid useless hazard removal
            ability_state.opponent_has_hazard_removal,
            # PHASE 2.3: Tera Prediction
            not ability_state.opponent_has_terastallized,
            # PHASE 3.1: Win Condition Awareness
            ability_state.our_active_is_wincon,
            ability_state.opponent_active_is_threat,
            # PHASE 3.2: PP Tracking
            ability_state.opponent_recovery_pp_exhausted,
            ability_state.opponent_low_stab_pp,
            # Healing waste avoidance
            ability_state.our_hp_percent >= 0.95,
            # PHASE 3.3: Momentum Tracking
            ability_state.momentum_level != "neutral",
        ]
    ):
        return final_policy

    penalized_policy = {}
    penalties_applied = []
    boosts_applied = []

    recovery_moves_norm = {normalize_name(m) for m in RECOVERY_MOVES}

    for move, weight in final_policy.items():
        # Extract the move name from the move choice format
        # Move choices can be "move:swordsdance" or just "swordsdance"
        move_name = move.split(":")[-1] if ":" in move else move

        penalty = 1.0  # No penalty by default
        reason = None

        # =====================================================================
        # EXISTING PENALTIES
        # =====================================================================

        # Already has status: NEVER use pure status moves (they will fail)
        if ability_state.has_status and move_name in PURE_STATUS_MOVES:
            penalty = min(penalty, 0.01)  # Near-zero: move literally fails
            reason = "Opponent already has a status condition (move will fail)"

        # Unaware: penalize offensive stat boosting moves
        if ability_state.has_unaware and move_name in OFFENSIVE_STAT_BOOST_MOVES:
            penalty = min(penalty, UNAWARE_BOOST_PENALTY)
            reason = "Unaware (stat boosts ignored)"

        # Guts/Marvel Scale/Quick Feet: penalize status moves
        if ability_state.has_guts_like and move_name in STATUS_INFLICTING_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Guts/Marvel Scale (status boosts them)"

        # Poison Heal: penalize Toxic/poison moves specifically
        if ability_state.has_poison_heal and move_name in TOXIC_POISON_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Poison Heal (poison heals them)"

        # Water immunity: penalize water moves
        if ability_state.has_water_immunity and move_name in WATER_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Water Absorb/Storm Drain (water heals/boosts)"

        # Electric immunity: penalize electric moves
        if ability_state.has_electric_immunity and move_name in ELECTRIC_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Volt Absorb/Lightning Rod (electric heals/boosts)"

        # Flash Fire: penalize fire moves (unless we have Mold Breaker)
        if ability_state.has_flash_fire and move_name in FIRE_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Flash Fire (fire boosts their fire moves)"

        # Levitate: penalize ground moves
        if ability_state.has_levitate and move_name in DAMAGING_GROUND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Levitate (ground immunity)"

        # Magic Bounce: penalize status moves that get reflected
        if ability_state.has_magic_bounce and move_name in MAGIC_BOUNCE_REFLECTED_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Magic Bounce (moves reflect back)"

        # Good as Gold: NEVER use status moves (they're completely blocked)
        if ability_state.has_good_as_gold and move_name in ALL_STATUS_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Good as Gold (status moves blocked)"

        # Competitive/Defiant: penalize stat-lowering moves
        if (
            ability_state.has_competitive_defiant
            and move_name in STAT_LOWERING_MOVES
        ):
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Competitive/Defiant (stat drops boost them)"

        # Focus Sash at full HP: boost multi-hit moves
        if ability_state.has_focus_sash and ability_state.at_full_hp:
            if move_name in MULTI_HIT_MOVES:
                penalty = max(penalty, ABILITY_BOOST_LIGHT)
                reason = "Focus Sash (multi-hit breaks it)"

        # Setup vs Phazers: penalize setup moves if opponent has phazing
        if ability_state.has_phazing and move_name in SETUP_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Phazer detected (setup boosts will be wasted)"

        # Redundant offensive setup: penalize boosting when already heavily boosted
        # Replay: gen9ou-2532366515 turn 55 (Gliscor used Swords Dance at +4 vs low-HP Kingambit)
        if move_name in OFFENSIVE_STAT_BOOST_MOVES:
            max_off_boost = max(ability_state.our_attack_boost, ability_state.our_spa_boost)
            if max_off_boost >= 6:
                penalty = min(penalty, ABILITY_PENALTY_SEVERE)
                reason = "Offensive boost already maxed (+6)"
            elif max_off_boost >= 4:
                penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
                reason = "Offensive boost already high (+4)"

        # Low HP vs recent damage: avoid setup/hazards when a KO line is likely.
        # Replay: gen9ou-2532408383 turn 25 (Blissey used Stealth Rock into Fiery Dance KO)
        # Replay: gen9ou-2532408309-1jnoidrrww9a72mn55er7s1nk8u4q2ipw turn 26
        # (Blissey used Calm Mind into Garganacl Earthquake KO)
        if (
            ability_state.opponent_used_damaging_move
            and ability_state.our_hp_percent <= 0.5
            and (move_name in SETUP_MOVES or move_name in HAZARD_SETTING_MOVES)
        ):
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Low HP vs recent damage (avoid setup/hazards)"

        # Substitute: penalize status-only moves that fail against sub
        if ability_state.has_substitute and move_name in STATUS_ONLY_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Substitute up (status moves fail)"

        # Choice-lock exploitation: boost setup moves when opponent is locked
        if ability_state.is_choice_locked and move_name in SETUP_MOVES:
            if not ability_state.has_phazing:
                if penalty >= 1.0:
                    penalty = ABILITY_BOOST_LIGHT
                    reason = f"Choice-locked into {ability_state.choice_locked_move} (free setup opportunity)"

        # Contact punishment: penalize contact moves
        if ability_state.has_contact_punish and move_name in CONTACT_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_LIGHT)
            reason = f"Contact punishment (Iron Barbs/Rough Skin/Rocky Helmet)"

        # =====================================================================
        # NEW PHASE 2: HIGH PRIORITY ABILITIES
        # =====================================================================

        # Contrary: OUR stat-lowering moves become THEIR boosts!
        # This is different from Competitive/Defiant - it's even worse
        if ability_state.has_contrary and move_name in STAT_LOWERING_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Contrary (our stat drops become their boosts!)"

        # Sap Sipper: Grass moves immune + boost their Attack
        if ability_state.has_sap_sipper and move_name in GRASS_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Sap Sipper (Grass immune, boosts their Atk)"

        # Sturdy at full HP: boost multi-hit moves (like Focus Sash)
        if ability_state.has_sturdy and ability_state.at_full_hp:
            if move_name in MULTI_HIT_MOVES:
                penalty = max(penalty, ABILITY_BOOST_LIGHT)
                reason = "Sturdy (multi-hit breaks it)"

        # Disguise not broken: boost multi-hit moves
        if ability_state.has_disguise and not ability_state.disguise_broken:
            if move_name in MULTI_HIT_MOVES:
                penalty = max(penalty, ABILITY_BOOST_LIGHT)
                reason = "Disguise (multi-hit breaks + damages)"

        # =====================================================================
        # NEW PHASE 3: MOVE CATEGORY IMMUNITIES
        # =====================================================================

        # Soundproof: Sound-based moves are immune
        if ability_state.has_soundproof and move_name in SOUND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Soundproof (sound moves immune)"

        # Bulletproof: Ball/Bullet moves are immune
        if ability_state.has_bulletproof and move_name in BULLET_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Bulletproof (bullet moves immune)"

        # Overcoat OR Grass type: Powder moves are immune
        if (ability_state.has_overcoat or ability_state.is_grass_type) and move_name in POWDER_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            if ability_state.is_grass_type:
                reason = "Grass type (powder moves immune)"
            else:
                reason = "Overcoat (powder moves immune)"

        # =====================================================================
        # NEW PHASE 4-5: TYPE/STAT INTERACTIONS
        # =====================================================================

        # Earth Eater: Ground moves heal them
        if ability_state.has_earth_eater and move_name in DAMAGING_GROUND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Earth Eater (Ground heals them)"

        # Justified: Dark moves give +1 Atk
        if ability_state.has_justified and move_name in DARK_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Justified (Dark gives +1 Atk)"

        # Steam Engine: Fire/Water moves give +6 Speed!
        if ability_state.has_steam_engine:
            if move_name in FIRE_TYPE_MOVES or move_name in WATER_TYPE_MOVES:
                penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
                reason = "Steam Engine (Fire/Water gives +6 Spe!)"

        # Wind Rider: Wind moves give +1 Atk instead of damage
        if ability_state.has_wind_rider and move_name in WIND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Wind Rider (wind moves boost their Atk)"

        # Well-Baked Body: Fire moves give +2 Def instead of damage
        if ability_state.has_well_baked_body and move_name in FIRE_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Well-Baked Body (Fire immune, boosts their Def)"

        # =====================================================================
        # NEW PHASE 6: STAT DROP IMMUNITY/REFLECTION
        # =====================================================================

        # Clear Body / White Smoke / Full Metal Body: stat drops don't work
        if ability_state.has_clear_body and move_name in STAT_LOWERING_MOVES:
            # Only penalize moves that ONLY lower stats (like Charm, Scary Face)
            # Damaging moves with secondary stat drops are still useful
            if move_name in STATUS_ONLY_MOVES or move_name in {
                "tickle", "charm", "featherdance", "faketears", "metalsound",
                "scaryface", "stringshot", "tearfullook", "captivate", "confide",
                "nobleroar", "partingshot", "memento"
            }:
                penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
                reason = "Clear Body (stat drops blocked)"

        # Mirror Armor: stat drops reflect back to us!
        if ability_state.has_mirror_armor and move_name in STAT_LOWERING_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Mirror Armor (stat drops reflect back!)"

        # Supreme Overlord: Be careful of Sucker Punch? 
        # For now, we mainly want to avoid giving it free turns if it's the last mon.
        if ability_state.has_supreme_overlord:
             # Just a marker for now, hard to penalize specific moves without knowing move pool
             # But if we use status moves, we risk attacks.
             pass

        # =====================================================================
        # NEW PHASE 7: SPECIAL INTERACTIONS
        # =====================================================================

        # Prankster vs Dark type: status moves fail completely
        if (
            ability_state.our_has_prankster
            and ability_state.opponent_is_dark_type
            and move_name in ALL_STATUS_MOVES
        ):
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Dark type immune to Prankster status moves"

        # Air Balloon: Ground moves fail (until balloon pops)
        if ability_state.has_air_balloon and move_name in DAMAGING_GROUND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Air Balloon (Ground immune until popped)"

        # Fluffy: Fire moves deal 2x damage - OPPORTUNITY!
        if ability_state.has_fluffy and move_name in FIRE_TYPE_MOVES:
            # Don't boost if they also have Flash Fire (shouldn't happen but safety)
            if not ability_state.has_flash_fire:
                penalty = max(penalty, ABILITY_BOOST_MEDIUM)
                reason = "Fluffy (Fire deals 2x - opportunity!)"

        # Dry Skin: Fire moves deal 1.25x damage - opportunity
        if ability_state.has_dry_skin_fire_weak and move_name in FIRE_TYPE_MOVES:
            # Don't boost if they also have Flash Fire
            if not ability_state.has_flash_fire:
                penalty = max(penalty, ABILITY_BOOST_LIGHT)
                reason = "Dry Skin (Fire deals 1.25x - opportunity)"

        # =====================================================================
        # NEW PHASE 8: WEATHER/TERRAIN
        # =====================================================================

        # Primordial Sea: Fire moves fail completely
        if ability_state.weather in WEATHER_EXTREME_RAIN and move_name in FIRE_TYPE_MOVES:
            penalty = min(penalty, 0.01)  # Near-zero: move fails
            reason = "Primordial Sea (Fire moves fail)"

        # Desolate Land: Water moves fail completely
        if ability_state.weather in WEATHER_EXTREME_SUN and move_name in WATER_TYPE_MOVES:
            penalty = min(penalty, 0.01)  # Near-zero: move fails
            reason = "Desolate Land (Water moves fail)"

        # Rain Dance: Fire -50%, Water already handled by MCTS but slight boost
        if ability_state.weather in WEATHER_RAIN and ability_state.weather not in WEATHER_EXTREME_RAIN:
            if move_name in FIRE_TYPE_MOVES:
                penalty = min(penalty, ABILITY_PENALTY_LIGHT)
                reason = "Rain (Fire -50%)"

        # Sunny Day: Water -50%, Fire already handled by MCTS but slight boost
        if ability_state.weather in WEATHER_SUN and ability_state.weather not in WEATHER_EXTREME_SUN:
            if move_name in WATER_TYPE_MOVES:
                penalty = min(penalty, ABILITY_PENALTY_LIGHT)
                reason = "Sun (Water -50%)"

        # Psychic Terrain: Priority moves blocked on grounded opponents
        if (
            ability_state.terrain in TERRAIN_PSYCHIC
            and ability_state.opponent_is_grounded
            and move_name in PRIORITY_MOVES
        ):
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Psychic Terrain (priority blocked on grounded)"

        # =====================================================================
        # PHASE 1.1: POSITIVE BOOSTS EXPANSION
        # =====================================================================

        # Boost setup when opponent used Protect last turn (free turn)
        if ability_state.opponent_used_protect and move_name in SETUP_MOVES:
            if penalty >= 1.0:
                penalty = BOOST_PROTECT_PUNISH
                reason = "Opponent used Protect (free setup turn)"

        # Anti-setup punishment: boost aggressive replies after opponent sets up
        if ability_state.opponent_used_setup:
            move_data = all_move_json.get(move_name, {})
            move_category = move_data.get(constants.CATEGORY, "")
            base_power = move_data.get(constants.BASE_POWER, 0)
            if move_name in AGGRESSIVE_MOVES or move_name in PRIORITY_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_PRESSURE_MOMENTUM)
                    reason = "Punish setup (aggressive response)"
            elif move_category in {constants.PHYSICAL, constants.SPECIAL} and base_power >= 80:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_PRESSURE_MOMENTUM)
                    reason = "Punish setup (hard hit)"

        # Boost setup when opponent is already statused (they're weakened)
        if ability_state.has_status and move_name in SETUP_MOVES:
            if penalty >= 1.0:
                penalty = max(penalty, BOOST_OPPONENT_STATUSED)
                reason = "Opponent is statused (weakened, good setup opportunity)"

        # Boost priority moves when opponent is at low HP (<25%)
        if ability_state.opponent_hp_percent < 0.25 and move_name in PRIORITY_MOVES:
            if penalty >= 1.0:
                penalty = max(penalty, BOOST_LOW_HP_PRIORITY)
                reason = f"Opponent at {int(ability_state.opponent_hp_percent * 100)}% HP (priority to finish)"

        # Enhanced choice-lock exploitation (check type matchup)
        # This extends the existing choice-lock detection with resist/immune checks
        if ability_state.is_choice_locked and move_name in SETUP_MOVES:
            if not ability_state.has_phazing:
                # Note: Full type immunity/resistance check would require battle context
                # For now, we apply the base boost; future: check actual matchup
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_CHOICE_LOCKED_RESIST)
                    reason = f"Choice-locked into {ability_state.choice_locked_move} (setup opportunity)"

        # =====================================================================
        # PHASE 1.2: TRICK ROOM AWARENESS
        # =====================================================================

        if ability_state.trick_room_active:
            # Penalize speed-boosting moves under Trick Room (they hurt us)
            if move_name in SPEED_BOOSTING_MOVES:
                penalty = min(penalty, PENALTY_SPEED_BOOST_IN_TR)
                reason = "Trick Room active (speed boosts are bad)"

            # Boost setup moves for slow Pokemon under Trick Room
            if ability_state.our_is_slow and move_name in SETUP_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_SETUP_SLOW_IN_TR)
                    reason = "Slow Pokemon under Trick Room (great setup opportunity)"

        # =====================================================================
        # PHASE 1.3: SCREENS AWARENESS
        # =====================================================================

        has_any_screen = (
            ability_state.opponent_has_reflect
            or ability_state.opponent_has_light_screen
            or ability_state.opponent_has_aurora_veil
        )

        if has_any_screen:
            # Boost setup moves when screens reduce damage to us
            if move_name in SETUP_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_SETUP_VS_SCREENS)
                    reason = "Screens active (safe to set up)"

            # Boost screen-breaking moves
            if move_name in SCREEN_BREAKING_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_SCREEN_BREAKER)
                    reason = "Screens active (screen breaker valuable)"

        # =====================================================================
        # PHASE 1.4: WEATHER/TERRAIN SYNERGIES
        # =====================================================================

        # Boost attacks when we have weather-based speed advantage
        if ability_state.we_have_weather_speed:
            # Get move data to check if it's a damaging move
            move_data = all_move_json.get(move_name, {})
            move_category = move_data.get(constants.CATEGORY, "")
            if move_category in {constants.PHYSICAL, constants.SPECIAL}:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_WEATHER_SPEED_ADVANTAGE)
                    reason = "Weather speed advantage (we outspeed)"

        # Terrain-boosted moves (for grounded Pokemon)
        if ability_state.we_are_grounded and ability_state.terrain_type:
            move_data = all_move_json.get(move_name, {})
            move_type = move_data.get(constants.TYPE, "")

            # Electric Terrain boosts Electric moves
            if ability_state.terrain_type == "electricterrain" and move_type == "electric":
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_TERRAIN_MOVE)
                    reason = "Electric Terrain (Electric moves boosted)"

            # Grassy Terrain boosts Grass moves
            if ability_state.terrain_type == "grassyterrain" and move_type == "grass":
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_TERRAIN_MOVE)
                    reason = "Grassy Terrain (Grass moves boosted)"

            # Psychic Terrain boosts Psychic moves
            if ability_state.terrain_type == "psychicterrain" and move_type == "psychic":
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_TERRAIN_MOVE)
                    reason = "Psychic Terrain (Psychic moves boosted)"

        # Grassy Terrain weakens Earthquake/Bulldoze (opponent benefits)
        if ability_state.terrain_type == "grassyterrain" and ability_state.opponent_is_grounded:
            if move_name in GRASSY_TERRAIN_WEAKENED:
                penalty = min(penalty, ABILITY_PENALTY_LIGHT)
                reason = "Grassy Terrain (Ground moves weakened)"

        # =====================================================================
        # PHASE 2.2: ENTRY HAZARD CALCULUS
        # =====================================================================

        # Boost Stealth Rock when not up and opponent has multiple Pokemon
        if move_name == "stealthrock":
            if not ability_state.opponent_sr_up:
                if ability_state.opponent_alive_count >= 4:
                    if penalty >= 1.0:
                        penalty = max(penalty, BOOST_SET_HAZARDS_NO_HAZARDS)
                        reason = f"No SR up, {ability_state.opponent_alive_count} opponent Pokemon alive"
                # Extra boost if opponent has SR-weak Pokemon
                if ability_state.opponent_has_sr_weak:
                    if penalty >= 1.0:
                        penalty = max(penalty, BOOST_SET_HAZARDS_SR_WEAK_OPP)
                        reason = "Opponent has SR-weak Pokemon"
            else:
                # Heavily penalize setting SR when already up
                penalty = min(penalty, PENALTY_SET_HAZARDS_ALREADY_UP)
                reason = "Stealth Rock already up"

        # Boost/penalize Spikes
        if move_name == "spikes":
            if ability_state.opponent_spikes_layers < 3:
                if ability_state.opponent_alive_count >= 3:
                    if penalty >= 1.0:
                        penalty = max(penalty, BOOST_SET_HAZARDS_NO_HAZARDS * 0.9)  # Slightly less than SR
                        reason = f"Spikes valuable ({ability_state.opponent_spikes_layers}/3 layers)"
            else:
                penalty = min(penalty, PENALTY_SET_HAZARDS_ALREADY_UP)
                reason = "Max Spikes layers already up"

        # Penalize hazard moves if opponent has revealed hazard removal
        if move_name in HAZARD_SETTING_MOVES and ability_state.opponent_has_hazard_removal:
            if penalty >= 0.5:  # Don't double-penalize
                penalty = min(penalty, PENALTY_HAZARDS_VS_REMOVAL)
                reason = "Opponent has hazard removal"

        # Boost Defog/Rapid Spin when we have heavy hazards
        if move_name in HAZARD_REMOVAL_MOVES:
            if ability_state.our_hazard_layers == 0:
                penalty = min(penalty, PENALTY_REMOVE_HAZARDS_NONE)
                reason = "No hazards to remove"
            elif ability_state.our_hazard_layers >= 2:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_REMOVE_HAZARDS_HEAVY)
                    reason = f"Heavy hazards on our side ({ability_state.our_hazard_layers} layers)"

        # =====================================================================
        # PHASE 2.3: TERA PREDICTION
        # =====================================================================

        # If opponent hasn't Terastallized yet, boost coverage moves
        # (they might Tera to change type matchup)
        if not ability_state.opponent_has_terastallized:
            move_data = all_move_json.get(move_name, {})
            move_category = move_data.get(constants.CATEGORY, "")
            move_type = move_data.get(constants.TYPE, "")

            # Boost coverage moves (non-STAB attacking moves)
            # This is a heuristic - if we have coverage, it might hit their Tera type
            if move_category in {constants.PHYSICAL, constants.SPECIAL}:
                # Check if it's likely a coverage move (not same type as our Pokemon)
                # We can't easily check our types here, so we boost neutral coverage slightly
                if move_type in OFFENSIVE_TERA_TYPES:
                    # Small boost to coverage that hits common Tera types
                    if penalty >= 1.0 and penalty < 1.2:
                        penalty = max(penalty, BOOST_COVERAGE_VS_LIKELY_TERA)
                        reason = "Coverage vs potential Tera"

        # =====================================================================
        # PHASE 3.1: WIN CONDITION AWARENESS
        # =====================================================================

        # If our active is a win condition, be more conservative
        if ability_state.our_active_is_wincon:
            # Penalize risky moves (self-destruct, etc.)
            if move_name in RISKY_MOVES:
                penalty = min(penalty, PENALTY_RISKY_WITH_WINCON)
                reason = "Win condition (avoid risky plays)"

            # Boost safe moves (pivots, protect)
            if move_name in SAFE_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_SAFE_WITH_WINCON)
                    reason = "Win condition (prefer safe plays)"

        # If opponent's active is a boosted threat, boost revenge killing
        if ability_state.opponent_active_is_threat:
            move_data = all_move_json.get(move_name, {})
            move_category = move_data.get(constants.CATEGORY, "")
            # Boost priority moves and strong attacks
            if move_name in PRIORITY_MOVES or move_category in {constants.PHYSICAL, constants.SPECIAL}:
                base_power = move_data.get(constants.BASE_POWER, 0)
                if move_name in PRIORITY_MOVES or base_power >= 80:
                    if penalty >= 1.0:
                        penalty = max(penalty, BOOST_REVENGE_KILL_THREAT)
                        reason = "Opponent threat active (revenge kill)"

        # =====================================================================
        # PHASE 3.2: PP TRACKING
        # =====================================================================

        # If opponent's recovery PP is exhausted, favor safe/stall plays
        if ability_state.opponent_recovery_pp_exhausted and move_name in SAFE_MOVES:
            if penalty >= 1.0:
                penalty = max(penalty, BOOST_STALL_NO_RECOVERY_PP)
                reason = "Opponent recovery PP exhausted (stall)"

        # If opponent's STAB PP is low, favor defensive positioning
        if ability_state.opponent_low_stab_pp and move_name in SAFE_MOVES:
            if penalty >= 1.0:
                penalty = max(penalty, BOOST_DEFENSIVE_LOW_PP)
                reason = "Opponent STAB low PP (defensive)"

        # Avoid wasting recovery at full HP, especially into a boosted threat
        if move_name in recovery_moves_norm and ability_state.our_hp_percent >= 0.95:
            penalty = min(penalty, 0.25)
            reason = "Full HP (avoid wasted recovery)"
            if ability_state.opponent_active_is_threat:
                penalty = min(penalty, 0.1)
                reason = "Full HP vs boosted threat (don't waste turn)"

        # =====================================================================
        # PHASE 3.3: MOMENTUM TRACKING
        # =====================================================================

        # Strong positive momentum: boost aggressive/setup moves
        if ability_state.momentum_level == "strong_positive":
            if move_name in AGGRESSIVE_MOVES or move_name in SETUP_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_AGGRESSIVE_STRONG_MOMENTUM)
                    reason = "Strong momentum (push advantage)"

        # Positive momentum: boost pressure moves
        elif ability_state.momentum_level == "positive":
            move_data = all_move_json.get(move_name, {})
            if move_data.get(constants.CATEGORY) in {constants.PHYSICAL, constants.SPECIAL}:
                if penalty >= 1.0 and penalty < 1.2:
                    penalty = max(penalty, BOOST_PRESSURE_MOMENTUM)
                    reason = "Positive momentum (maintain pressure)"

        # Negative momentum: boost pivots and safe switches
        elif ability_state.momentum_level == "negative":
            if move_name in SAFE_MOVES or move_name in {"uturn", "voltswitch", "flipturn", "partingshot"}:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_PIVOT_NEGATIVE_MOMENTUM)
                    reason = "Negative momentum (regroup with pivot)"

        # Strong negative momentum: boost high-risk/high-reward plays
        elif ability_state.momentum_level == "strong_negative":
            # When desperate, risky plays become more acceptable
            if move_name in SETUP_MOVES or move_name in RISKY_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_HIGHRISK_DESPERATE)
                    reason = "Desperate (high-risk play)"

        # =====================================================================
        # APPLY PENALTY
        # =====================================================================

        new_weight = weight * penalty
        penalized_policy[move] = new_weight

        if penalty < 1.0:
            penalties_applied.append((move, weight, new_weight, reason))
        elif penalty > 1.0:
            boosts_applied.append((move, weight, new_weight, reason))

    # Log penalties/boosts with reduced noise by grouping by reason
    def _log_grouped(entries, label):
        grouped = {}
        for move, old_weight, new_weight, reason in entries:
            grouped.setdefault(reason, []).append((move, old_weight, new_weight))

        for reason, items in grouped.items():
            if len(items) <= 3:
                for move, old_weight, new_weight in items:
                    logger.info(
                        f"{label} ({reason}) on {move}: {old_weight:.3f} -> {new_weight:.3f}"
                    )
            else:
                preview = ", ".join(
                    f"{m} {ow:.3f}->{nw:.3f}" for m, ow, nw in items[:3]
                )
                logger.info(
                    f"{label} ({reason}) on {len(items)} moves: {preview} (+{len(items)-3} more)"
                )

    _log_grouped(penalties_applied, "Ability penalty")
    _log_grouped(boosts_applied, "Ability BOOST")

    if trace_events is not None:
        for move, old_weight, new_weight, reason in penalties_applied:
            trace_events.append(
                {
                    "type": "penalty",
                    "source": "ability",
                    "move": move,
                    "reason": reason,
                    "before": old_weight,
                    "after": new_weight,
                }
            )
        for move, old_weight, new_weight, reason in boosts_applied:
            trace_events.append(
                {
                    "type": "boost",
                    "source": "ability",
                    "move": move,
                    "reason": reason,
                    "before": old_weight,
                    "after": new_weight,
                }
            )

    return penalized_policy


def apply_switch_penalties(
    policy: dict[str, float],
    battle: Battle,
    ability_state: OpponentAbilityState,
    playstyle: Playstyle | None = None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """
    Apply penalties and boosts to switch decisions based on:
    - Hazard damage on switch-in
    - Intimidate vs Defiant/Competitive
    - Type matchups
    - HP of switch target
    - Recovery moves available
    - Playstyle modifiers (FAT/STALL teams have reduced penalties)
    """
    if battle is None or battle.user.active is None:
        return policy

    # Get playstyle-specific switch penalty multiplier
    switch_penalty_mult = 1.0
    if playstyle is not None:
        cfg = PlaystyleConfig.get_config(playstyle)
        switch_penalty_mult = cfg.get("switch_penalty_multiplier", 1.0)

    adjusted_policy = {}
    penalties_applied = []
    boosts_applied = []

    opponent = battle.opponent.active
    opponent_types = getattr(opponent, "types", []) if opponent else []

    # Build a map of our reserve Pokemon for quick lookup
    reserve_map = {}
    for pkmn in battle.user.reserve:
        if pkmn and pkmn.hp > 0:
            reserve_map[pkmn.name] = pkmn
            # Also map normalized name
            reserve_map[normalize_name(pkmn.name)] = pkmn

    for move, weight in policy.items():
        if weight <= 0:
            adjusted_policy[move] = weight
            continue

        # Only process switch moves
        if not move.startswith("switch "):
            adjusted_policy[move] = weight
            continue

        # Extract switch target name
        switch_target_name = move.split("switch ", 1)[1].strip()
        target_pkmn = reserve_map.get(switch_target_name) or reserve_map.get(normalize_name(switch_target_name))

        if target_pkmn is None:
            adjusted_policy[move] = weight
            continue

        multiplier = 1.0
        reasons = []
        target_moves = getattr(target_pkmn, "moves", []) or []
        target_move_names = [
            m.name if hasattr(m, "name") else str(m) for m in target_moves
        ]

        # === HAZARD DAMAGE PENALTY ===
        if ability_state.our_hazard_layers > 0:
            # Calculate approximate hazard damage
            hazard_damage = 0.0
            target_types = getattr(target_pkmn, "types", []) or []
            target_item = getattr(target_pkmn, "item", None)

            # Skip if Heavy-Duty Boots
            if target_item != "heavydutyboots":
                # Stealth Rock damage
                if ability_state.our_sr_up:
                    sr_mult = type_effectiveness_modifier("rock", target_types)
                    hazard_damage += 0.125 * sr_mult  # 6.25% to 50%

                # Spikes damage (only if grounded)
                is_grounded = "flying" not in target_types
                if hasattr(target_pkmn, "ability") and target_pkmn.ability in UNGROUNDED_ABILITIES:
                    is_grounded = False
                if is_grounded and ability_state.our_spikes_layers > 0:
                    spikes_damage = [0, 0.125, 0.167, 0.25][min(ability_state.our_spikes_layers, 3)]
                    hazard_damage += spikes_damage

                # Apply penalty based on hazard damage
                if hazard_damage > 0:
                    # Scale penalty: 12.5% damage = ~8% penalty, 50% damage = ~32% penalty
                    hp_ratio = target_pkmn.hp / max(target_pkmn.max_hp, 1)
                    effective_damage = min(hazard_damage, hp_ratio)  # Can't take more than current HP
                    damage_penalty = PENALTY_SWITCH_INTO_HAZARDS_PER_LAYER ** (effective_damage * 8)
                    multiplier *= damage_penalty
                    reasons.append(f"hazards ({int(hazard_damage * 100)}% damage)")

        # === INTIMIDATE VS DEFIANT/COMPETITIVE PENALTY ===
        if ability_state.opponent_has_defiant_pokemon:
            # Check if switch target has Intimidate
            target_ability = getattr(target_pkmn, "ability", None)
            if target_ability in INTIMIDATE_ABILITIES or target_pkmn.name in POKEMON_WITH_INTIMIDATE_COMMON:
                multiplier *= PENALTY_SWITCH_INTIMIDATE_VS_DEFIANT
                reasons.append("Intimidate vs Defiant/Competitive")

        # === LOW HP PENALTY ===
        hp_ratio = target_pkmn.hp / max(target_pkmn.max_hp, 1)
        if hp_ratio < 0.25:
            multiplier *= PENALTY_SWITCH_LOW_HP
            reasons.append(f"low HP ({int(hp_ratio * 100)}%)")

        # === TYPE WEAKNESS PENALTY ===
        if opponent and opponent_types:
            target_types = getattr(target_pkmn, "types", []) or []
            if target_types:
                # Check if target is weak to opponent's STAB
                for opp_type in opponent_types:
                    effectiveness = type_effectiveness_modifier(opp_type, target_types)
                    if effectiveness >= 2.0:
                        multiplier *= PENALTY_SWITCH_WEAK_TO_OPPONENT
                        reasons.append(f"weak to {opp_type}")
                        break

        # === TYPE RESISTANCE BOOST ===
        if opponent and opponent_types:
            target_types = getattr(target_pkmn, "types", []) or []
            if target_types:
                # Check if target resists opponent's STAB
                resists_both = True
                for opp_type in opponent_types:
                    effectiveness = type_effectiveness_modifier(opp_type, target_types)
                    if effectiveness >= 1.0:
                        resists_both = False
                        break
                if resists_both and len(opponent_types) > 0:
                    multiplier *= BOOST_SWITCH_RESISTS_STAB
                    reasons.append("resists opponent STAB")

        # === UNAWARE VS SETUP BOOST ===
        if opponent is not None:
            opp_boosts = getattr(opponent, "boosts", {}) or {}
            has_offensive_boost = (
                opp_boosts.get(constants.ATTACK, 0) > 0
                or opp_boosts.get(constants.SPECIAL_ATTACK, 0) > 0
            )
            if has_offensive_boost:
                target_ability = getattr(target_pkmn, "ability", None)
                target_ability_norm = normalize_name(target_ability) if target_ability else None
                target_name_norm = normalize_name(target_pkmn.name)
                target_base_norm = normalize_name(getattr(target_pkmn, "base_name", "") or target_pkmn.name)
                if (
                    target_ability_norm == "unaware"
                    or target_name_norm in POKEMON_COMMONLY_UNAWARE
                    or target_base_norm in POKEMON_COMMONLY_UNAWARE
                ):
                    multiplier *= BOOST_SWITCH_UNAWARE_VS_SETUP
                    reasons.append("Unaware vs boosted attacker")
                if any(m in PHAZING_MOVES or m == "haze" for m in target_move_names):
                    multiplier *= BOOST_SWITCH_COUNTERS
                    reasons.append("Haze/Phaze vs boosted attacker")
                if any(m in PRIORITY_MOVES for m in target_move_names):
                    multiplier *= BOOST_SWITCH_COUNTERS
                    reasons.append("priority check vs boosted attacker")

        # === RECOVERY MOVE BOOST ===
        has_recovery = False
        for move_name in target_move_names:
            if move_name in POKEMON_RECOVERY_MOVES:
                has_recovery = True
                break
        if has_recovery:
            multiplier *= BOOST_SWITCH_HAS_RECOVERY
            reasons.append("has recovery")

        # === POISON HEAL STATUS VULNERABILITY ===
        # Poison Heal mons (Gliscor, Breloom) are ruined if burned before Toxic Orb activates
        # Status-backfire mons (Guts, Marvel Scale) WANT to be statused
        if opponent is not None:
            target_ability = getattr(target_pkmn, "ability", None)
            target_status = getattr(target_pkmn, "status", None)
            target_name_norm = normalize_name(target_pkmn.name)
            target_base_norm = normalize_name(getattr(target_pkmn, "base_name", "") or target_pkmn.name)
            
            # Check if target is a Poison Heal mon
            is_poison_heal = (
                (target_ability and normalize_name(target_ability) == "poisonheal")
                or target_name_norm in POKEMON_COMMONLY_POISON_HEAL
                or target_base_norm in POKEMON_COMMONLY_POISON_HEAL
            )
            
            # Check if target is a status-backfire mon (Guts, Marvel Scale, Quick Feet)
            is_status_backfire = (
                target_name_norm in POKEMON_STATUS_BACKFIRES
                or target_base_norm in POKEMON_STATUS_BACKFIRES
            )
            
            # Get opponent's known moves
            opponent_moves = getattr(opponent, "moves", [])
            opponent_move_names = [
                m.name if hasattr(m, "name") else str(m) for m in opponent_moves
            ]
            
            # Check for status-inflicting moves
            has_burn_move = any(
                move_name in STATUS_INFLICTING_MOVES and move_name in {"willowisp", "scald", "searingshot", "inferno", "sacredfire", "burningjealousy"}
                for move_name in opponent_move_names
            )
            has_paralyze_move = any(
                move_name in STATUS_INFLICTING_MOVES and move_name in {"thunderwave", "stunspore", "glare", "nuzzle", "zapcannon", "bodyslam"}
                for move_name in opponent_move_names
            )
            has_status_move = has_burn_move or has_paralyze_move
            
            # PENALTY: Poison Heal mon switching into burn move (if not already poisoned)
            if is_poison_heal and has_burn_move and target_status not in (POISON, TOXIC):
                # This is catastrophic - burning a Poison Heal mon ruins it permanently
                multiplier *= PENALTY_SWITCH_WEAK_TO_OPPONENT  # Use existing severe penalty constant
                reasons.append("Poison Heal vs burn move")
            
            # BOOST: Status-backfire mon (Guts/Marvel Scale) switching into status move
            # These mons WANT to be statused (if not already statused)
            if is_status_backfire and has_status_move and target_status is None:
                multiplier *= BOOST_SWITCH_COUNTERS  # Use existing counter boost constant
                reasons.append("Guts/Marvel Scale vs status move")

        # === PLAYSTYLE ADJUSTMENT ===
        # For penalties (multiplier < 1.0), apply playstyle modulation
        # FAT/STALL teams get less harsh penalties (0.6x = 60% of base penalty)
        # HYPER_OFFENSE teams get harsher penalties (1.4x = 140% of base penalty)
        if multiplier < 1.0 and switch_penalty_mult != 1.0:
            # Convert penalty to "distance from 1.0"
            penalty_magnitude = 1.0 - multiplier
            # Adjust penalty magnitude by playstyle
            adjusted_penalty_magnitude = penalty_magnitude * switch_penalty_mult
            # Convert back to multiplier
            multiplier = 1.0 - adjusted_penalty_magnitude
            if switch_penalty_mult != 1.0:
                reasons.append(f"playstyle {switch_penalty_mult:.1f}x")

        # Apply multiplier
        new_weight = weight * multiplier
        adjusted_policy[move] = new_weight

        if multiplier < 1.0:
            penalties_applied.append((move, weight, new_weight, ", ".join(reasons)))
        elif multiplier > 1.0:
            boosts_applied.append((move, weight, new_weight, ", ".join(reasons)))

    # Log switch adjustments
    for move, old_w, new_w, reason in penalties_applied:
        logger.info(f"Switch penalty ({reason}): {move} {old_w:.3f} -> {new_w:.3f}")
    for move, old_w, new_w, reason in boosts_applied:
        logger.info(f"Switch BOOST ({reason}): {move} {old_w:.3f} -> {new_w:.3f}")

    if trace_events is not None:
        for move, old_w, new_w, reason in penalties_applied:
            trace_events.append(
                {
                    "type": "penalty",
                    "source": "switch",
                    "move": move,
                    "reason": reason,
                    "before": old_w,
                    "after": new_w,
                }
            )
        for move, old_w, new_w, reason in boosts_applied:
            trace_events.append(
                {
                    "type": "boost",
                    "source": "switch",
                    "move": move,
                    "reason": reason,
                    "before": old_w,
                    "after": new_w,
                }
            )

    return adjusted_policy


def apply_threat_switch_bias(
    policy: dict[str, float],
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """If opponent is a boosted threat, push switching above non-damaging moves."""
    if battle is None or ability_state is None or not ability_state.opponent_active_is_threat:
        return policy

    best_switch_weight = 0.0
    for move, weight in policy.items():
        if move.startswith("switch "):
            best_switch_weight = max(best_switch_weight, weight)

    if best_switch_weight <= 0:
        return policy

    adjusted = {}
    for move, weight in policy.items():
        move_name = move.split(":")[-1] if ":" in move else move
        move_data = all_move_json.get(move_name, {})
        cat = move_data.get(constants.CATEGORY)
        new_weight = weight
        if not move.startswith("switch ") and cat == constants.STATUS:
            new_weight = min(weight, best_switch_weight * 0.7)
            if trace_events is not None:
                trace_events.append(
                    {
                        "type": "penalty",
                        "source": "threat_switch",
                        "move": move,
                        "reason": "boosted_threat_prefer_switch",
                        "before": weight,
                        "after": new_weight,
                    }
                )
        adjusted[move] = new_weight
    return adjusted


def _get_item_probability(pkmn, item_name: str) -> float:
    """Estimate probability that pkmn is holding item_name."""
    if pkmn is None:
        return 0.0

    normalized_item = normalize_name(item_name)
    known_item = normalize_name(pkmn.item) if pkmn.item else ""

    # If item is known, return certainty
    if pkmn.item not in (None, "", "unknownitem", "unknown", "none", constants.UNKNOWN_ITEM):
        return 1.0 if known_item == normalized_item else 0.0

    # If item is explicitly impossible, return 0
    if hasattr(pkmn, "impossible_items") and normalized_item in pkmn.impossible_items:
        return 0.0

    try:
        raw = SmogonSets.get_raw_pkmn_sets_from_pkmn_name(pkmn.name, pkmn.base_name)
    except Exception:
        return 0.0

    for item, prob in raw.get(ITEM_STRING, []):
        if normalize_name(item) == normalized_item:
            return prob
    return 0.0


def _get_status_threat_probability(pkmn) -> float:
    """Estimate probability that opponent has a status-inflicting move."""
    if pkmn is None:
        return 0.0

    # If a status move is revealed, treat as certain.
    revealed = getattr(pkmn, "moves", []) or []
    for mv in revealed:
        name = mv.name if hasattr(mv, "name") else str(mv)
        if normalize_name(name) in STATUS_INFLICTING_MOVES:
            return 1.0

    # Fall back to move-set distributions from TeamDatasets (replay moves).
    try:
        movesets = (
            TeamDatasets.raw_pkmn_moves.get(pkmn.name)
            or TeamDatasets.raw_pkmn_moves.get(getattr(pkmn, "base_name", pkmn.name))
        )
    except Exception:
        movesets = None

    if not movesets:
        return 0.0

    total = sum(ms.count for ms in movesets)
    if total <= 0:
        return 0.0

    status_count = 0
    for moveset in movesets:
        if any(normalize_name(mv) in STATUS_INFLICTING_MOVES for mv in moveset.moves):
            status_count += moveset.count

    return status_count / total


def apply_pre_orb_status_safety(
    policy: dict[str, float],
    battle: Battle | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """Protect Toxic Orb activation for Poison Heal users (pre-poison)."""
    if battle is None or battle.user.active is None or battle.opponent.active is None:
        return policy

    our = battle.user.active
    if our.hp <= 0:
        return policy
    if our.status in (POISON, TOXIC):
        return policy
    if our.status:
        # Already statused by something else; orb protection is moot.
        return policy

    our_name = normalize_name(our.name)
    ability_norm = normalize_name(our.ability or "")
    if ability_norm and ability_norm != "poisonheal":
        return policy
    if not ability_norm and our_name not in POKEMON_COMMONLY_POISON_HEAL:
        return policy

    item_norm = normalize_name(our.item or "")
    if item_norm and item_norm != "toxicorb":
        return policy
    orb_prob = 1.0 if item_norm == "toxicorb" else _get_item_probability(our, "toxicorb")
    if orb_prob <= 0:
        return policy

    threat_prob = _get_status_threat_probability(battle.opponent.active)
    if threat_prob < PRE_ORB_STATUS_THREAT_MIN_PROB:
        return policy

    threat = min(threat_prob, 1.0)
    protect_boost = 1.0 + (BOOST_PRE_ORB_PROTECT - 1.0) * threat
    nonprotect_penalty = 1.0 - (1.0 - PENALTY_PRE_ORB_NONPROTECT) * threat

    adjusted = dict(policy)
    changed = False
    for move, weight in policy.items():
        if weight <= 0:
            adjusted[move] = weight
            continue
        move_name = move.split(":")[-1] if ":" in move else move
        if move_name.endswith("-tera"):
            move_name = move_name[:-5]
        move_norm = normalize_name(move_name)

        if move_norm in PROTECT_MOVES:
            new_weight = weight * protect_boost
            adjusted[move] = new_weight
            changed = True
            if trace_events is not None:
                trace_events.append(
                    {
                        "type": "boost",
                        "source": "pre_orb_status",
                        "move": move,
                        "reason": f"pre_orb_status_safety(threat={threat_prob:.2f})",
                        "before": weight,
                        "after": new_weight,
                    }
                )
            continue

        if move.startswith("switch "):
            adjusted[move] = weight
            continue

        new_weight = weight * nonprotect_penalty
        adjusted[move] = new_weight
        changed = True
        if trace_events is not None:
            trace_events.append(
                {
                    "type": "penalty",
                    "source": "pre_orb_status",
                    "move": move,
                    "reason": f"pre_orb_status_safety(threat={threat_prob:.2f})",
                    "before": weight,
                    "after": new_weight,
                }
            )

    if changed:
        logger.info(
            f"Pre-orb status safety: threat={threat_prob:.2f}, "
            f"protect_boost={protect_boost:.2f}, nonprotect_penalty={nonprotect_penalty:.2f}"
        )

    return adjusted


def _get_switch_likelihood(our_active, target) -> float:
    """Estimate likelihood that target switches into our_active."""
    if our_active is None or target is None:
        return 0.0
    try:
        raw = SmogonSets.get_raw_pkmn_sets_from_pkmn_name(
            our_active.name, getattr(our_active, "base_name", our_active.name)
        )
    except Exception:
        return 0.0

    matchups = raw.get(EFFECTIVENESS, {})
    if not matchups:
        return 0.0
    return float(
        matchups.get(target.name, matchups.get(getattr(target, "base_name", ""), 0.0))
    )


def _get_hazard_pressure(battle: Battle) -> float:
    """Return a simple hazard-pressure signal on opponent side."""
    if battle is None:
        return 0.0
    hazards_present = any(
        [
            battle.opponent.side_conditions[constants.STEALTH_ROCK] > 0,
            battle.opponent.side_conditions[constants.SPIKES] > 0,
            battle.opponent.side_conditions[constants.TOXIC_SPIKES] > 0,
            battle.opponent.side_conditions[constants.STICKY_WEB] > 0,
        ]
    )
    if hazards_present:
        return 1.0

    if battle.user.active is not None:
        hazard_moves_norm = {normalize_name(m) for m in HAZARD_MOVES}
        if any(normalize_name(m.name) in hazard_moves_norm for m in battle.user.active.moves):
            return 0.5
    return 0.0


def apply_preemptive_item_punish(
    policy: dict[str, float],
    battle: Battle | None,
    playstyle: Playstyle,
    decision_profile: DecisionProfile,
) -> dict[str, float]:
    """
    Preemptive Knock Off bonus for high-value switch-ins (gen9ou only).
    Focuses on:
      - Gliscor Toxic Orb denial (pre-poison)
      - Heavy-Duty Boots removal when hazards matter
    """
    if battle is None or battle.pokemon_format != "gen9ou":
        return policy
    if battle.battle_type != BattleType.STANDARD_BATTLE:
        return policy
    if battle.force_switch:
        return policy
    if battle.user.active is None or battle.opponent.active is None:
        return policy
    if "knockoff" not in policy:
        return policy

    knock_weight = policy["knockoff"]
    if knock_weight <= 0:
        return policy

    best_weight = max(policy.values())
    gate_threshold = 0.90 if decision_profile == DecisionProfile.LOW else 0.85
    if knock_weight < best_weight * gate_threshold:
        return policy

    playstyle_cfg = PlaystyleConfig.get_config(playstyle)
    chip_scale = max(0.7, min(1.3, playstyle_cfg.get("chip_damage_multiplier", 1.0)))
    hazard_scale = max(0.7, min(1.3, playstyle_cfg.get("hazard_value_multiplier", 1.0)))

    preemptive_scores = []

    # 1) Gliscor Toxic Orb denial (only if not already poisoned)
    gliscor = next(
        (p for p in battle.opponent.reserve if p.name == "gliscor" and p.hp > 0),
        None,
    )
    if gliscor and gliscor.status not in (POISON, TOXIC):
        # If ability known and not poison heal, skip denial logic
        if not gliscor.ability or gliscor.ability == "poisonheal":
            toxic_orb_prob = _get_item_probability(gliscor, "toxicorb")
            switch_likelihood = _get_switch_likelihood(battle.user.active, gliscor)
            gliscor_score = toxic_orb_prob * switch_likelihood * chip_scale
            if gliscor_score > 0:
                preemptive_scores.append(("gliscor-toxic-orb", gliscor_score))

    # 2) Heavy-Duty Boots removal when hazards matter
    hazard_pressure = _get_hazard_pressure(battle)
    if hazard_pressure > 0:
        best_boots_score = 0.0
        best_boots_target = None
        for p in battle.opponent.reserve:
            if p.hp <= 0:
                continue
            boots_prob = _get_item_probability(p, "heavydutyboots")
            if boots_prob <= 0:
                continue
            switch_likelihood = _get_switch_likelihood(battle.user.active, p)
            score = boots_prob * switch_likelihood * hazard_pressure * hazard_scale
            if score > best_boots_score:
                best_boots_score = score
                best_boots_target = p.name
        if best_boots_score > 0 and best_boots_target:
            preemptive_scores.append(
                (f"boots-removal({best_boots_target})", best_boots_score)
            )

    if not preemptive_scores:
        return policy

    # Pick the strongest preemptive signal
    reason, best_score = max(preemptive_scores, key=lambda x: x[1])

    max_bonus = 0.12 if decision_profile == DecisionProfile.LOW else 0.22
    bonus = max_bonus * min(best_score, 1.0)
    if bonus <= 0:
        return policy

    policy = dict(policy)
    policy["knockoff"] = knock_weight * (1.0 + bonus)
    logger.info(
        f"Preemptive Knock Off bonus ({reason}): {knock_weight:.3f} -> "
        f"{policy['knockoff']:.3f} (bonus={bonus:.3f})"
    )
    return policy


def apply_team_strategy_bias(
    policy: dict[str, float],
    battle: Battle | None,
    team_plan: TeamAnalysis | None,
    playstyle: Playstyle,
) -> dict[str, float]:
    """Apply playstyle- and team-plan-based move biases."""
    if battle is None or team_plan is None:
        return policy
    if battle.user.active is None:
        return policy

    cfg = PlaystyleConfig.get_config(playstyle)
    hazard_mult = cfg.get("hazard_value_multiplier", 1.0)
    recovery_mult = cfg.get("recovery_value_multiplier", 1.0)
    setup_mult = cfg.get("setup_value_multiplier", 1.0)
    pivot_mult = cfg.get("pivot_value_multiplier", 1.0)
    damage_mult = cfg.get("damage_value_multiplier", 1.0)
    chip_mult = cfg.get("chip_damage_multiplier", 1.0)

    # Hazard state
    hazards_on_opp = any(
        [
            battle.opponent.side_conditions[constants.STEALTH_ROCK] > 0,
            battle.opponent.side_conditions[constants.SPIKES] > 0,
            battle.opponent.side_conditions[constants.TOXIC_SPIKES] > 0,
            battle.opponent.side_conditions[constants.STICKY_WEB] > 0,
        ]
    )
    hazards_on_us = any(
        [
            battle.user.side_conditions[constants.STEALTH_ROCK] > 0,
            battle.user.side_conditions[constants.SPIKES] > 0,
            battle.user.side_conditions[constants.TOXIC_SPIKES] > 0,
            battle.user.side_conditions[constants.STICKY_WEB] > 0,
        ]
    )

    opponent_has_gholdengo = any(
        p.name == "gholdengo"
        for p in battle.opponent.reserve + ([battle.opponent.active] if battle.opponent.active else [])
        if p is not None and p.hp > 0
    )
    our_has_gholdengo = "gholdengo" in team_plan.hazard_setters or any(
        p.name == "gholdengo" for p in battle.user.reserve + ([battle.user.active] if battle.user.active else [])
        if p is not None
    )

    hazard_moves_norm = {normalize_name(m) for m in HAZARD_MOVES}
    removal_moves_norm = {normalize_name(m) for m in REMOVAL_MOVES}
    screen_moves_norm = {normalize_name(m) for m in SCREEN_MOVES}
    recovery_moves_norm = {normalize_name(m) for m in RECOVERY_MOVES}
    setup_moves_norm = {normalize_name(m) for m in SETUP_MOVES}
    pivot_moves_norm = {normalize_name(m) for m in PIVOT_MOVES}

    active_name = battle.user.active.name if battle.user.active else ""
    active_is_wincon = active_name in team_plan.wincons
    opponent = battle.opponent.active
    switch_tendency = 0.0
    if battle.opponent is not None:
        opp_name = getattr(battle.opponent, "account_name", None) or getattr(battle.opponent, "name", None)
        if opp_name:
            switch_tendency = OPPONENT_MODEL.get_switch_tendency(opp_name)
    threat_category = None
    if opponent is not None:
        try:
            threat_category = get_threat_category(opponent.name)
        except Exception:
            threat_category = None

    adjusted = {}
    for move, weight in policy.items():
        if weight <= 0:
            adjusted[move] = weight
            continue

        new_weight = weight
        is_switch = move.startswith("switch ")
        base_move = move.split()[-1] if is_switch else move
        move_name = normalize_name(base_move)

        # Early-game hazard urgency for FAT/STALL teams
        # Human stall players set rocks in the first 1-3 turns almost always
        is_early_game = hasattr(battle, 'turn') and isinstance(battle.turn, int) and battle.turn <= 3
        if move_name == "stealthrock" and is_early_game and not hazards_on_opp:
            if playstyle in (Playstyle.FAT, Playstyle.STALL):
                # Strong boost: rocks are the foundation of chip-based strategies
                early_hazard_boost = 3.0 if battle.turn <= 1 else 2.5 if battle.turn <= 2 else 2.0
                new_weight *= early_hazard_boost
            elif playstyle in (Playstyle.BALANCE, Playstyle.BULKY_OFFENSE):
                # Moderate boost for non-HO teams
                new_weight *= 1.8

        # Apply style multipliers
        if move_name in hazard_moves_norm:
            if not hazards_on_opp or our_has_gholdengo:
                new_weight *= hazard_mult * chip_mult
            if switch_tendency > 0.5:
                new_weight *= 1.0 + min((switch_tendency - 0.5) * 0.3, 0.2)
        if move_name in screen_moves_norm:
            if playstyle == Playstyle.HYPER_OFFENSE:
                new_weight *= 1.3
        if move_name in removal_moves_norm:
            if hazards_on_us:
                new_weight *= hazard_mult
            if opponent_has_gholdengo and move_name in {"defog", "mortalspin"}:
                new_weight *= 0.5
            if opponent_has_gholdengo and move_name == "rapidspin":
                new_weight *= 0.7
        if move_name in recovery_moves_norm:
            new_weight *= recovery_mult
            # FAT/STALL teams should recover more aggressively
            # A wall below 60% HP should almost always recover
            if playstyle in (Playstyle.FAT, Playstyle.STALL):
                active = battle.user.active
                if active and active.max_hp > 0:
                    hp_ratio = active.hp / active.max_hp
                    if hp_ratio <= 0.4:
                        new_weight *= 2.5  # Strong: heal when critically low
                    elif hp_ratio <= 0.6:
                        new_weight *= 1.8  # Moderate: heal to stay healthy
        if move_name in setup_moves_norm:
            new_weight *= setup_mult
            if active_is_wincon:
                new_weight *= 1.1
        if move_name in pivot_moves_norm:
            new_weight *= pivot_mult

        # Damage move bias
        if move_name in all_move_json:
            cat = all_move_json[move_name].get(constants.CATEGORY)
            if cat in {constants.PHYSICAL, constants.SPECIAL}:
                new_weight *= damage_mult

        # Wincon preservation: discourage switching a healthy wincon without force
        if active_is_wincon and move.startswith("switch "):
            hp_ratio = battle.user.active.hp / max(battle.user.active.max_hp, 1)
            if hp_ratio >= 0.5:
                new_weight *= 0.9

        # Matchup-aware switching (even when not forced)
        if is_switch and opponent is not None:
            target_name = normalize_name(move.split("switch ")[-1])
            target = None
            for p in battle.user.reserve:
                if p.name == target_name:
                    target = p
                    break
            if target is not None:
                score = 0.0
                hp_ratio = target.hp / max(target.max_hp, 1)
                score += hp_ratio
                try:
                    worst = max(
                        type_effectiveness_modifier(t, target.types)
                        for t in opponent.types
                    )
                    score += (2.0 - min(worst, 2.0))
                except Exception:
                    pass
                try:
                    best_off = max(
                        type_effectiveness_modifier(t, opponent.types)
                        for t in target.types
                    )
                    score += best_off
                except Exception:
                    pass
                if threat_category == ThreatCategory.PHYSICAL_ONLY:
                    score += target.stats[constants.DEFENSE] / 200.0
                elif threat_category == ThreatCategory.SPECIAL_ONLY:
                    score += target.stats[constants.SPECIAL_DEFENSE] / 200.0
                else:
                    score += (
                        target.stats[constants.DEFENSE]
                        + target.stats[constants.SPECIAL_DEFENSE]
                    ) / 400.0
                # translate score into a gentle multiplier
                new_weight *= 1.0 + min(max(score - 1.5, -0.3), 0.5) * 0.1

        adjusted[move] = new_weight

    return adjusted


def select_move_from_mcts_results(
    mcts_results: list[(MctsResult, float, int)],
    ability_state: OpponentAbilityState | None = None,
    battle: Battle | None = None,
    playstyle: Playstyle | None = None,
    decision_profile: DecisionProfile = DecisionProfile.DEFAULT,
    trace: dict | None = None,
) -> str:
    final_policy = {}
    score_policy = {}  # Track average scores for quality assessment
    trace_events = []
    for mcts_result, sample_chance, index in mcts_results:
        this_policy = max(mcts_result.side_one, key=lambda x: x.visits)
        logger.info(
            "Policy {}: {} visited {}% avg_score={} sample_chance_multiplier={}".format(
                index,
                this_policy.move_choice,
                round(100 * this_policy.visits / mcts_result.total_visits, 2),
                round(this_policy.total_score / this_policy.visits, 3),
                round(sample_chance, 3),
            )
        )
        for s1_option in mcts_result.side_one:
            visit_weight = sample_chance * (s1_option.visits / mcts_result.total_visits)
            final_policy[s1_option.move_choice] = final_policy.get(
                s1_option.move_choice, 0
            ) + visit_weight

            # Accumulate weighted average scores
            if s1_option.visits > 0:
                avg_score = s1_option.total_score / s1_option.visits
                if s1_option.move_choice not in score_policy:
                    score_policy[s1_option.move_choice] = (0.0, 0.0)
                old_score, old_weight = score_policy[s1_option.move_choice]
                score_policy[s1_option.move_choice] = (
                    old_score + avg_score * visit_weight,
                    old_weight + visit_weight,
                )

    # Blend visit-based policy with score-based quality (80% visits, 20% score)
    # This helps break ties and prefer moves with higher expected value
    blended_policy = {}
    for move, visit_weight in final_policy.items():
        if move in score_policy and score_policy[move][1] > 0:
            weighted_score, total_weight = score_policy[move]
            avg_score = weighted_score / total_weight
            # Normalize score contribution: scores typically range 0-1
            # Use it as a tiebreaker multiplier
            score_bonus = max(0.0, min(avg_score, 1.0))
            blended_policy[move] = visit_weight * (0.8 + 0.2 * score_bonus)
        else:
            blended_policy[move] = visit_weight

    blended_policy = apply_heuristic_bias(
        blended_policy,
        battle,
        ability_state,
        trace_events=trace_events,
    )

    # Hard filter: remove moves that are blocked by opponent abilities
    if ability_state:
        blended_policy = filter_blocked_moves(
            blended_policy,
            ability_state,
            battle=battle,
            trace_events=trace_events,
        )

    # Apply ability-based penalties before sorting
    if ability_state:
        blended_policy = apply_ability_penalties(
            blended_policy,
            ability_state,
            trace_events=trace_events,
        )

    # Apply switch-specific penalties (Phase 2.1)
    if ability_state and battle is not None:
        blended_policy = apply_switch_penalties(
            blended_policy,
            battle,
            ability_state,
            playstyle=playstyle,
            trace_events=trace_events,
        )

    # KO-line bias: punish non-damaging moves if a KO line is available
    if ability_state:
        blended_policy = apply_ko_line_bias(
            blended_policy,
            ability_state,
            trace_events=trace_events,
        )

    blended_policy = apply_threat_switch_bias(
        blended_policy,
        battle,
        ability_state,
        trace_events=trace_events,
    )

    # Pre-orb status safety: protect Toxic Orb activation (Poison Heal)
    if battle is not None:
        blended_policy = apply_pre_orb_status_safety(
            blended_policy,
            battle,
            trace_events=trace_events,
        )

    # Preemptive item-denial bonus (gen9ou only)
    if battle is not None and playstyle is not None:
        blended_policy = apply_preemptive_item_punish(
            blended_policy,
            battle,
            playstyle,
            decision_profile,
        )

    # Team strategy / playstyle bias
    if battle is not None and playstyle is not None:
        team_plan = getattr(battle.user, "team_plan", None)
        blended_policy = apply_team_strategy_bias(
            blended_policy,
            battle,
            team_plan,
            playstyle,
        )

    sorted_policy = sorted(blended_policy.items(), key=lambda x: x[1], reverse=True)

    if trace is not None:
        raw_scores = {}
        for move, (weighted_score, total_weight) in score_policy.items():
            if total_weight > 0:
                raw_scores[move] = weighted_score / total_weight
        top_moves = []
        for move, weight in sorted_policy[:5]:
            top_moves.append(
                {
                    "move": move,
                    "blended_weight": weight,
                    "avg_score": raw_scores.get(move),
                    "visit_weight": final_policy.get(move, 0.0),
                }
            )
        trace["mcts"] = {
            "top_moves": top_moves,
            "policy_pre_penalty": final_policy,
            "policy_post_penalty": blended_policy,
            "events": trace_events,
        }

    logger.info("All Choices (post-blend, post-penalty):")
    for i, (move, weight) in enumerate(sorted_policy):
        logger.info(f"\t{round(weight * 100, 3)}%: {move}")

    # Deterministic selection when one move is clearly dominant
    if len(sorted_policy) >= 2:
        best_weight = sorted_policy[0][1]
        second_weight = sorted_policy[1][1]
        dominance_ratio = 1.5
        if decision_profile == DecisionProfile.LOW:
            dominance_ratio = 1.2
        elif decision_profile == DecisionProfile.HIGH:
            dominance_ratio = 1.6

        if second_weight > 0 and best_weight / second_weight >= dominance_ratio:
            logger.info(f"Clear best move: {sorted_policy[0][0]} ({best_weight/second_weight:.1f}x better)")
            return sorted_policy[0][0]

    # Consider moves within a threshold of the best
    highest_percentage = sorted_policy[0][1]
    considered_threshold = 0.90
    if decision_profile == DecisionProfile.LOW:
        considered_threshold = 0.95
    elif decision_profile == DecisionProfile.HIGH:
        considered_threshold = 0.85

    considered = [i for i in sorted_policy if i[1] >= highest_percentage * considered_threshold]
    logger.info(f"Considered Choices ({int(considered_threshold * 100)}% threshold):")
    for i, policy in enumerate(considered):
        logger.info(f"\t{round(policy[1] * 100, 3)}%: {policy[0]}")

    choice = random.choices(considered, weights=[p[1] for p in considered])[0]
    return choice[0]


def get_result_from_mcts(state: str, search_time_ms: int, index: int) -> MctsResult:
    logger.debug("Calling with {} state: {}".format(index, state))
    poke_engine_state = PokeEngineState.from_string(state)

    res = monte_carlo_tree_search(poke_engine_state, search_time_ms)
    logger.info("Iterations {}: {}".format(index, res.total_visits))
    return res


def _get_time_pressure_level(battle):
    """Returns time pressure level: 0=none, 1=moderate (<60s), 2=critical (<30s), 3=emergency (<15s)"""
    if battle.time_remaining is None:
        return 0
    if battle.time_remaining <= 15:
        return 3
    if battle.time_remaining <= 30:
        return 2
    if battle.time_remaining <= 60:
        return 1
    return 0


def search_time_num_battles_randombattles(battle):
    revealed_pkmn = len(battle.opponent.reserve)
    if battle.opponent.active is not None:
        revealed_pkmn += 1

    opponent_active_num_moves = len(battle.opponent.active.moves)
    pressure = _get_time_pressure_level(battle)

    # Emergency: minimal search
    if pressure >= 3:
        return FoulPlayConfig.parallelism, int(FoulPlayConfig.search_time_ms // 4)

    # Critical: reduced search
    if pressure >= 2:
        return FoulPlayConfig.parallelism, int(FoulPlayConfig.search_time_ms // 2)

    # it is still quite early in the battle and the pkmn in front of us
    # hasn't revealed any moves: search a lot of battles shallowly
    if (
        revealed_pkmn <= 3
        and battle.opponent.active.hp > 0
        and opponent_active_num_moves == 0
    ):
        num_battles_multiplier = 2 if pressure >= 1 else 4
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms // 2
        )

    else:
        num_battles_multiplier = 1 if pressure >= 1 else 2
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms
        )


def search_time_num_battles_standard_battle(battle):
    opponent_active_num_moves = len(battle.opponent.active.moves)
    pressure = _get_time_pressure_level(battle)

    # Emergency: minimal search
    if pressure >= 3:
        return FoulPlayConfig.parallelism, int(FoulPlayConfig.search_time_ms // 4)

    # Critical: reduced search
    if pressure >= 2:
        return FoulPlayConfig.parallelism, int(FoulPlayConfig.search_time_ms // 2)

    if (
        battle.team_preview
        or (battle.opponent.active.hp > 0 and opponent_active_num_moves == 0)
        or opponent_active_num_moves < 3
    ):
        num_battles_multiplier = 1 if pressure >= 1 else 2
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms
        )
    else:
        return FoulPlayConfig.parallelism, FoulPlayConfig.search_time_ms


# Maximum total time budget for a single decision (seconds)
# Pokemon Showdown gives ~150s total per game, or ~45s per turn with timer on
# We need to leave margin for network latency, state processing, etc.
MAX_DECISION_TIME_SECONDS = 20  # Reduced from 30 to avoid timeout losses
# When in time pressure (<60s remaining), use a much tighter budget
MAX_DECISION_TIME_PRESSURE_SECONDS = 6  # Reduced from 8 for more safety margin


def _get_fallback_move(battle: Battle) -> str:
    """
    Emergency fallback: pick the first available move without MCTS.
    Used when MCTS times out to avoid forfeiting the turn.
    """
    # If force_switch is active, we MUST switch - don't try moves
    if battle.force_switch:
        for pkmn in battle.user.reserve:
            if pkmn.hp > 0:
                logger.warning(f"Timeout fallback (force_switch): switching to {pkmn.name}")
                return f"switch {pkmn.name}"
        logger.error("Timeout fallback: force_switch active but no alive reserves!")
        return "switch 1"  # Last resort, server will reject but better than a move

    if battle.user.active is not None:
        best_move = None
        best_score = -1.0
        opponent = battle.opponent.active
        for move in battle.user.active.moves:
            if hasattr(move, "disabled") and move.disabled:
                continue
            if hasattr(move, "current_pp") and move.current_pp <= 0:
                continue
            move_name = move.name if hasattr(move, "name") else str(move)
            score = 0.0
            if move_name in all_move_json and opponent is not None:
                mv = all_move_json[move_name]
                if mv.get(constants.CATEGORY) in {constants.PHYSICAL, constants.SPECIAL}:
                    move_type = mv.get(constants.TYPE)
                    base_power = mv.get(constants.BASE_POWER, 60)
                    if move_type and opponent.types:
                        eff = type_effectiveness_modifier(move_type, opponent.types)
                    else:
                        eff = 1.0
                    stab = 1.0
                    if move_type and battle.user.active.has_type(move_type):
                        stab = 1.5
                    score = eff * stab * base_power
            if score > best_score:
                best_score = score
                best_move = move_name

        if best_move:
            logger.warning(f"Timeout fallback: selecting {best_move}")
            return best_move

    # If no moves available, try switching
    for pkmn in battle.user.reserve:
        if pkmn.hp > 0:
            logger.warning(f"Timeout fallback: switching to {pkmn.name}")
            return f"switch {pkmn.name}"

    logger.error("Timeout fallback: no moves or switches available!")
    return "splash"


def find_best_move(battle: Battle) -> tuple[str, dict]:
    start_time = time.time()
    if not getattr(battle, "_isolation_copy", False):
        battle = deepcopy(battle)
    trace = build_trace_base(battle)
    trace["battle_type"] = (
        battle.battle_type.name if hasattr(battle.battle_type, "name") else str(battle.battle_type)
    )
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    # Determine time budget
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60
    time_budget = (
        MAX_DECISION_TIME_PRESSURE_SECONDS
        if in_time_pressure
        else MAX_DECISION_TIME_SECONDS
    )

    if in_time_pressure:
        logger.warning(
            f"TIME PRESSURE: {battle.time_remaining}s remaining, "
            f"budget={time_budget}s"
        )

    # Detect opponent's abilities before we start sampling
    # (sampling may change the ability, so check the original battle state)
    ability_state = detect_opponent_abilities(battle)
    trace["ability_state"] = asdict(ability_state)

    # Log detected abilities that will affect move selection
    detected_abilities = []
    # Existing detections
    if ability_state.has_unaware:
        detected_abilities.append("Unaware")
    if ability_state.has_guts_like:
        detected_abilities.append("Guts/Marvel Scale/Quick Feet")
    if ability_state.has_poison_heal:
        detected_abilities.append("Poison Heal")
    if ability_state.has_water_immunity:
        detected_abilities.append("Water Absorb/Storm Drain")
    if ability_state.has_electric_immunity:
        detected_abilities.append("Volt Absorb/Lightning Rod")
    if ability_state.has_flash_fire:
        detected_abilities.append("Flash Fire")
    if ability_state.has_levitate:
        detected_abilities.append("Levitate")
    if ability_state.has_magic_bounce:
        detected_abilities.append("Magic Bounce")
    if ability_state.has_good_as_gold:
        detected_abilities.append("Good as Gold")
    if ability_state.has_competitive_defiant:
        detected_abilities.append("Competitive/Defiant")
    if ability_state.has_focus_sash and ability_state.at_full_hp:
        detected_abilities.append("Focus Sash (full HP)")
    if ability_state.has_phazing:
        detected_abilities.append("Phazer")
    if ability_state.has_substitute:
        detected_abilities.append("Substitute")
    if ability_state.has_contact_punish:
        detected_abilities.append("Contact Punishment")
    if ability_state.has_status:
        detected_abilities.append("Already statused")
    if ability_state.is_choice_locked:
        detected_abilities.append(f"Choice-locked into {ability_state.choice_locked_move}")
    # NEW: Phase 2 - High Priority
    if ability_state.has_contrary:
        detected_abilities.append("Contrary")
    if ability_state.has_sap_sipper:
        detected_abilities.append("Sap Sipper")
    if ability_state.has_sturdy and ability_state.at_full_hp:
        detected_abilities.append("Sturdy (full HP)")
    if ability_state.has_disguise and not ability_state.disguise_broken:
        detected_abilities.append("Disguise (intact)")
    # NEW: Phase 3 - Move Category Immunities
    if ability_state.has_soundproof:
        detected_abilities.append("Soundproof")
    if ability_state.has_bulletproof:
        detected_abilities.append("Bulletproof")
    if ability_state.has_overcoat:
        detected_abilities.append("Overcoat")
    if ability_state.is_grass_type:
        detected_abilities.append("Grass type (powder immune)")
    # NEW: Phase 4-5 - Type/Stat Interactions
    if ability_state.has_earth_eater:
        detected_abilities.append("Earth Eater")
    if ability_state.has_justified:
        detected_abilities.append("Justified")
    if ability_state.has_steam_engine:
        detected_abilities.append("Steam Engine")
    if ability_state.has_wind_rider:
        detected_abilities.append("Wind Rider")
    if ability_state.has_well_baked_body:
        detected_abilities.append("Well-Baked Body")
    # NEW: Phase 6 - Stat Immunity/Reflection
    if ability_state.has_clear_body:
        detected_abilities.append("Clear Body/White Smoke")
    if ability_state.has_mirror_armor:
        detected_abilities.append("Mirror Armor")
    if ability_state.has_supreme_overlord:
        detected_abilities.append("Supreme Overlord")
    # NEW: Phase 7 - Special Interactions
    if ability_state.our_has_prankster and ability_state.opponent_is_dark_type:
        detected_abilities.append("Dark type (Prankster blocked)")
    if ability_state.our_has_mold_breaker:
        detected_abilities.append("(We have Mold Breaker)")
    if ability_state.has_air_balloon:
        detected_abilities.append("Air Balloon")
    if ability_state.has_fluffy:
        detected_abilities.append("Fluffy (Fire 2x)")
    if ability_state.has_dry_skin_fire_weak:
        detected_abilities.append("Dry Skin (Fire 1.25x)")
    if ability_state.has_priority_block:
        detected_abilities.append("Priority blocked (Queenly Majesty/Dazzling/Armor Tail)")
    # NEW: Phase 8 - Weather/Terrain
    if ability_state.weather:
        detected_abilities.append(f"Weather: {ability_state.weather}")
    if ability_state.terrain:
        detected_abilities.append(f"Terrain: {ability_state.terrain}")
    # PHASE 1.1: Positive Boosts
    if ability_state.opponent_used_protect:
        detected_abilities.append("Opponent used Protect (free turn)")
    if ability_state.opponent_used_setup:
        detected_abilities.append("Opponent used setup move")
    if ability_state.opponent_used_damaging_move:
        detected_abilities.append("Opponent used damaging move last turn")
    if ability_state.opponent_hp_percent < 0.25:
        detected_abilities.append(f"Low HP ({int(ability_state.opponent_hp_percent * 100)}%)")
    # PHASE 1.2: Trick Room
    if ability_state.trick_room_active:
        detected_abilities.append(f"Trick Room active ({ability_state.trick_room_turns} turns)")
        if ability_state.our_is_slow:
            detected_abilities.append("(We're slow - TR advantage)")
    # PHASE 1.3: Screens
    screens = []
    if ability_state.opponent_has_reflect:
        screens.append("Reflect")
    if ability_state.opponent_has_light_screen:
        screens.append("Light Screen")
    if ability_state.opponent_has_aurora_veil:
        screens.append("Aurora Veil")
    if screens:
        detected_abilities.append(f"Screens: {', '.join(screens)}")
    # PHASE 1.4: Weather/Terrain Synergies
    if ability_state.we_have_weather_speed:
        detected_abilities.append("(We have weather speed boost)")
    # PHASE 2.1: Switch Evaluation
    if ability_state.our_hazard_layers > 0:
        detected_abilities.append(f"Hazards on our side ({ability_state.our_hazard_layers} layers)")
    if ability_state.opponent_has_defiant_pokemon:
        detected_abilities.append("Opponent has Defiant/Competitive Pokemon")
    # PHASE 2.2: Entry Hazard Calculus
    if ability_state.opponent_sr_up:
        detected_abilities.append("Opponent SR up")
    if ability_state.opponent_has_sr_weak:
        detected_abilities.append("Opponent has SR-weak Pokemon")
    if ability_state.opponent_has_hazard_removal:
        detected_abilities.append("Opponent has hazard removal")
    # PHASE 2.3: Tera Prediction
    if ability_state.opponent_has_terastallized:
        detected_abilities.append(f"Opponent Terastallized ({ability_state.opponent_tera_type})")
    elif not ability_state.opponent_has_terastallized:
        detected_abilities.append("Opponent can still Tera")
    # PHASE 3.1: Win Condition Awareness
    if ability_state.our_active_is_wincon:
        detected_abilities.append("(Our active is win condition)")
    if ability_state.opponent_active_is_threat:
        detected_abilities.append("Opponent active is THREAT")
    # PHASE 3.3: Momentum Tracking
    if ability_state.momentum_level != "neutral":
        detected_abilities.append(f"Momentum: {ability_state.momentum_level} ({ability_state.momentum:.1f})")

    if detected_abilities:
        logger.info(
            f"Opponent's {ability_state.pokemon_name} "
            f"(ability: {ability_state.ability_name}) - "
            f"detected: {', '.join(detected_abilities)}"
        )
    trace["detected_abilities"] = detected_abilities

    ko_line = find_ko_line(battle)
    if ko_line:
        ability_state.ko_line_available = True
        ability_state.ko_line_turns = int(ko_line.get("turns", 0))
        ability_state.ko_line_move = ko_line.get("move", "")
        trace["ko_line"] = ko_line

    # Team analysis (for playstyle + strategy bias)
    team_plan = getattr(battle.user, "team_plan", None)
    if team_plan is None and battle.user.team_dict:
        try:
            team_plan = analyze_team(battle.user.team_dict)
            battle.user.team_plan = team_plan
        except Exception as e:
            logger.warning(f"Failed to analyze team: {e}")
            team_plan = None

    # Resolve playstyle + decision profile (gen9ou only)
    playstyle = _resolve_playstyle(team_plan)
    decision_profile = _select_decision_profile(battle, playstyle)
    trace["playstyle"] = playstyle.value if hasattr(playstyle, "value") else str(playstyle)
    trace["decision_profile"] = (
        decision_profile.value if hasattr(decision_profile, "value") else str(decision_profile)
    )
    if battle.pokemon_format == "gen9ou":
        logger.info(
            f"Decision profile: {decision_profile.value} (playstyle: {playstyle.value})"
        )

    # =========================================================================
    # PHASE 4.1: ENDGAME SOLVER
    # =========================================================================
    # Try to solve simple endgames deterministically before MCTS
    try:
        from fp.search.endgame import is_endgame, solve_endgame

        if is_endgame(battle, ENDGAME_MAX_POKEMON):
            solution = solve_endgame(battle)
            if solution and solution.is_deterministic and solution.best_move:
                logger.info(
                    f"ENDGAME SOLVED: {solution.best_move} "
                    f"(outcome: {solution.expected_outcome:.1%}, {solution.explanation})"
                )
                trace["decision_mode"] = "endgame"
                trace["endgame"] = {
                    "best_move": solution.best_move,
                    "expected_outcome": solution.expected_outcome,
                    "explanation": solution.explanation,
                    "deterministic": True,
                }
                trace["choice"] = solution.best_move
                return solution.best_move, trace
            elif solution:
                logger.info(
                    f"Endgame analysis: {solution.explanation} "
                    f"(outcome: {solution.expected_outcome:.1%}, not deterministic)"
                )
    except Exception as e:
        logger.debug(f"Endgame solver error: {e}")

    if battle.battle_type == BattleType.RANDOM_BATTLE:
        num_battles, search_time_per_battle = search_time_num_battles_randombattles(
            battle
        )
        prepare_fn = prepare_random_battles
    elif battle.battle_type == BattleType.BATTLE_FACTORY:
        num_battles, search_time_per_battle = search_time_num_battles_standard_battle(
            battle
        )
        prepare_fn = prepare_random_battles
    elif battle.battle_type == BattleType.STANDARD_BATTLE:
        num_battles, search_time_per_battle = search_time_num_battles_standard_battle(
            battle
        )
        prepare_fn = prepare_battles
    else:
        raise ValueError("Unsupported battle type: {}".format(battle.battle_type))

    if FoulPlayConfig.max_mcts_battles is not None:
        desired = max(1, FoulPlayConfig.max_mcts_battles)
        if in_time_pressure and desired > num_battles:
            logger.info(
                f"Time pressure: keeping reduced MCTS samples at {num_battles} "
                f"(desired {desired})"
            )
        else:
            if num_battles != desired:
                logger.info(
                    f"Overriding MCTS samples from {num_battles} to {desired}"
                )
            num_battles = desired

    # Adaptive search time: invest more in high-stakes turns
    high_stakes = ability_state.opponent_active_is_threat or ability_state.ko_line_available
    if high_stakes and not in_time_pressure:
        boosted = int(search_time_per_battle * 1.5)
        search_time_per_battle = min(boosted, FoulPlayConfig.search_time_ms * 2)

    battles = prepare_fn(battle, num_battles)

    # Adjust search time if we're running low on time budget
    elapsed = time.time() - start_time
    remaining_budget = time_budget - elapsed - 2.0  # 2s safety margin
    if remaining_budget <= 0:
        logger.warning("No time left for MCTS, using fallback")
        fallback = _get_fallback_move(battle)
        trace["decision_mode"] = "fallback"
        trace["fallback_reason"] = "time_budget_exhausted"
        trace["choice"] = fallback
        oddities = detect_odd_move(battle, fallback, ability_state)
        trace["oddities"] = oddities
        _log_oddities(fallback, oddities)
        return fallback, trace

    # Cap per-battle search time to fit within budget
    max_per_battle_ms = int((remaining_budget * 1000) / max(num_battles / FoulPlayConfig.parallelism, 1))
    if max_per_battle_ms < search_time_per_battle:
        logger.info(
            f"Reducing search time from {search_time_per_battle}ms to "
            f"{max_per_battle_ms}ms to fit time budget"
        )
        search_time_per_battle = max(max_per_battle_ms, 10)  # minimum 10ms

    trace["search"] = {
        "num_battles": num_battles,
        "search_time_ms": search_time_per_battle,
        "parallelism": FoulPlayConfig.parallelism,
        "time_budget_s": time_budget,
    }

    logger.info("Searching for a move using MCTS...")
    logger.info(
        "Sampling {} simulated battles (MCTS) at {}ms each".format(
            num_battles, search_time_per_battle
        )
    )

    # Calculate timeout for the executor (remaining budget in seconds)
    executor_timeout = max(time_budget - (time.time() - start_time) - 1.0, 0.5)

    mcts_results = []
    try:
        # Use global executor instead of creating a new one (fixes zombie process leak)
        executor = _get_executor()
        futures = []
        for index, (b, chance) in enumerate(battles):
            fut = executor.submit(
                get_result_from_mcts,
                battle_to_poke_engine_state(b).to_string(),
                search_time_per_battle,
                index,
            )
            futures.append((fut, chance, index))

        # Collect results with timeout
        for fut, chance, index in futures:
            try:
                remaining = max(executor_timeout - (time.time() - start_time), 0.5)
                result = fut.result(timeout=remaining)
                mcts_results.append((result, chance, index))
            except (FuturesTimeoutError, TimeoutError):
                logger.warning(f"MCTS battle {index} timed out, skipping")
                fut.cancel()
            except Exception as e:
                logger.warning(f"MCTS battle {index} failed: {e}")

        if not mcts_results:
            logger.warning("All MCTS searches failed/timed out, using fallback")
            fallback = _get_fallback_move(battle)
            trace["decision_mode"] = "fallback"
            trace["fallback_reason"] = "mcts_failed"
            trace["choice"] = fallback
            oddities = detect_odd_move(battle, fallback, ability_state)
            trace["oddities"] = oddities
            _log_oddities(fallback, oddities)
            return fallback, trace

        choice = select_move_from_mcts_results(
            mcts_results,
            ability_state=ability_state,
            battle=battle,
            playstyle=playstyle,
            decision_profile=decision_profile,
            trace=trace,
        )
    except Exception as e:
        logger.error(f"MCTS search failed entirely: {e}")
        fallback = _get_fallback_move(battle)
        trace["decision_mode"] = "fallback"
        trace["fallback_reason"] = "mcts_exception"
        trace["choice"] = fallback
        oddities = detect_odd_move(battle, fallback, ability_state)
        trace["oddities"] = oddities
        _log_oddities(fallback, oddities)
        return fallback, trace

    elapsed_total = time.time() - start_time
    logger.info(f"Choice: {choice} (decided in {elapsed_total:.1f}s)")
    trace["decision_mode"] = "mcts"
    trace["choice"] = choice
    oddities = detect_odd_move(battle, choice, ability_state)
    trace["oddities"] = oddities
    _log_oddities(choice, oddities)
    trace["decision_time_s"] = round(elapsed_total, 3)
    return choice, trace
