import logging
import random
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field

from constants import (
    BattleType,
    # Unaware
    OFFENSIVE_STAT_BOOST_MOVES,
    POKEMON_COMMONLY_UNAWARE,
    UNAWARE_BOOST_PENALTY,
    # Status-boosted abilities (Guts, Marvel Scale, etc.)
    POKEMON_STATUS_BACKFIRES,
    STATUS_INFLICTING_MOVES,
    # Poison Heal
    POKEMON_COMMONLY_POISON_HEAL,
    TOXIC_POISON_MOVES,
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
    # Competitive/Defiant
    POKEMON_STAT_DROP_BACKFIRES,
    STAT_LOWERING_MOVES,
    # Penalty values
    ABILITY_PENALTY_SEVERE,
    ABILITY_PENALTY_MEDIUM,
)
from fp.battle import Battle
from config import FoulPlayConfig
from .standard_battles import prepare_battles
from .random_battles import prepare_random_battles

from poke_engine import State as PokeEngineState, monte_carlo_tree_search, MctsResult

from fp.search.poke_engine_helpers import battle_to_poke_engine_state

logger = logging.getLogger(__name__)


# =============================================================================
# ABILITY DETECTION
# =============================================================================


@dataclass
class OpponentAbilityState:
    """Tracks what abilities the opponent's active Pokemon has or likely has."""

    has_unaware: bool = False
    has_guts_like: bool = False  # Guts, Marvel Scale, Quick Feet
    has_poison_heal: bool = False
    has_water_immunity: bool = False  # Water Absorb, Storm Drain, Dry Skin
    has_electric_immunity: bool = False  # Volt Absorb, Lightning Rod, Motor Drive
    has_flash_fire: bool = False
    has_levitate: bool = False
    has_magic_bounce: bool = False
    has_competitive_defiant: bool = False  # Competitive or Defiant
    pokemon_name: str = ""
    ability_known: bool = False
    ability_name: str = ""


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

    state.pokemon_name = name
    state.ability_known = bool(ability)
    state.ability_name = ability or "unknown"

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

    # Magic Bounce
    state.has_magic_bounce = _check_ability_or_pokemon(
        ability, name, base_name, {"magicbounce"}, POKEMON_COMMONLY_MAGIC_BOUNCE
    )

    # Competitive/Defiant
    state.has_competitive_defiant = _check_ability_or_pokemon(
        ability,
        name,
        base_name,
        {"competitive", "defiant"},
        POKEMON_STAT_DROP_BACKFIRES,
    )

    return state


# =============================================================================
# PENALTY APPLICATION
# =============================================================================


def apply_ability_penalties(
    final_policy: dict[str, float], ability_state: OpponentAbilityState
) -> dict[str, float]:
    """
    Apply penalties to moves based on opponent's abilities.

    This function handles all ability-based move penalties in one pass.
    """
    # Quick exit if no problematic abilities detected
    if not any(
        [
            ability_state.has_unaware,
            ability_state.has_guts_like,
            ability_state.has_poison_heal,
            ability_state.has_water_immunity,
            ability_state.has_electric_immunity,
            ability_state.has_flash_fire,
            ability_state.has_levitate,
            ability_state.has_magic_bounce,
            ability_state.has_competitive_defiant,
        ]
    ):
        return final_policy

    penalized_policy = {}
    penalties_applied = []

    for move, weight in final_policy.items():
        # Extract the move name from the move choice format
        # Move choices can be "move:swordsdance" or just "swordsdance"
        move_name = move.split(":")[-1] if ":" in move else move

        penalty = 1.0  # No penalty by default
        reason = None

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

        # Flash Fire: penalize fire moves
        if ability_state.has_flash_fire and move_name in FIRE_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Flash Fire (fire boosts their fire moves)"

        # Levitate: penalize ground moves
        if ability_state.has_levitate and move_name in GROUND_TYPE_MOVES:
            # Don't penalize hazards, just damaging ground moves
            if move_name not in {"stealthrock", "spikes", "toxicspikes", "stickyweb"}:
                penalty = min(penalty, ABILITY_PENALTY_SEVERE)
                reason = "Levitate (ground immunity)"

        # Magic Bounce: penalize status moves that get reflected
        if ability_state.has_magic_bounce and move_name in MAGIC_BOUNCE_REFLECTED_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Magic Bounce (moves reflect back)"

        # Competitive/Defiant: penalize stat-lowering moves
        if (
            ability_state.has_competitive_defiant
            and move_name in STAT_LOWERING_MOVES
        ):
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Competitive/Defiant (stat drops boost them)"

        # Apply the penalty
        new_weight = weight * penalty
        penalized_policy[move] = new_weight

        if penalty < 1.0:
            penalties_applied.append((move, weight, new_weight, reason))

    # Log all penalties applied
    for move, old_weight, new_weight, reason in penalties_applied:
        logger.info(
            f"Ability penalty ({reason}) on {move}: {old_weight:.3f} -> {new_weight:.3f}"
        )

    return penalized_policy


def select_move_from_mcts_results(
    mcts_results: list[(MctsResult, float, int)],
    ability_state: OpponentAbilityState | None = None,
) -> str:
    final_policy = {}
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
            final_policy[s1_option.move_choice] = final_policy.get(
                s1_option.move_choice, 0
            ) + (sample_chance * (s1_option.visits / mcts_result.total_visits))

    # Apply ability-based penalties before sorting
    if ability_state:
        final_policy = apply_ability_penalties(final_policy, ability_state)

    final_policy = sorted(final_policy.items(), key=lambda x: x[1], reverse=True)

    # Consider all moves that are close to the best move
    highest_percentage = final_policy[0][1]
    final_policy = [i for i in final_policy if i[1] >= highest_percentage * 0.75]
    logger.info("Considered Choices:")
    for i, policy in enumerate(final_policy):
        logger.info(f"\t{round(policy[1] * 100, 3)}%: {policy[0]}")

    choice = random.choices(final_policy, weights=[p[1] for p in final_policy])[0]
    return choice[0]


def get_result_from_mcts(state: str, search_time_ms: int, index: int) -> MctsResult:
    logger.debug("Calling with {} state: {}".format(index, state))
    poke_engine_state = PokeEngineState.from_string(state)

    res = monte_carlo_tree_search(poke_engine_state, search_time_ms)
    logger.info("Iterations {}: {}".format(index, res.total_visits))
    return res


def search_time_num_battles_randombattles(battle):
    revealed_pkmn = len(battle.opponent.reserve)
    if battle.opponent.active is not None:
        revealed_pkmn += 1

    opponent_active_num_moves = len(battle.opponent.active.moves)
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60

    # it is still quite early in the battle and the pkmn in front of us
    # hasn't revealed any moves: search a lot of battles shallowly
    if (
        revealed_pkmn <= 3
        and battle.opponent.active.hp > 0
        and opponent_active_num_moves == 0
    ):
        num_battles_multiplier = 2 if in_time_pressure else 4
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms // 2
        )

    else:
        num_battles_multiplier = 1 if in_time_pressure else 2
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms
        )


def search_time_num_battles_standard_battle(battle):
    opponent_active_num_moves = len(battle.opponent.active.moves)
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60

    if (
        battle.team_preview
        or (battle.opponent.active.hp > 0 and opponent_active_num_moves == 0)
        or opponent_active_num_moves < 3
    ):
        num_battles_multiplier = 1 if in_time_pressure else 2
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms
        )
    else:
        return FoulPlayConfig.parallelism, FoulPlayConfig.search_time_ms


def find_best_move(battle: Battle) -> str:
    battle = deepcopy(battle)
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    # Detect opponent's abilities before we start sampling
    # (sampling may change the ability, so check the original battle state)
    ability_state = detect_opponent_abilities(battle)

    # Log detected abilities that will affect move selection
    detected_abilities = []
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
    if ability_state.has_competitive_defiant:
        detected_abilities.append("Competitive/Defiant")

    if detected_abilities:
        logger.info(
            f"Opponent's {ability_state.pokemon_name} "
            f"(ability: {ability_state.ability_name}) - "
            f"detected: {', '.join(detected_abilities)}"
        )

    if battle.battle_type == BattleType.RANDOM_BATTLE:
        num_battles, search_time_per_battle = search_time_num_battles_randombattles(
            battle
        )
        battles = prepare_random_battles(battle, num_battles)
    elif battle.battle_type == BattleType.BATTLE_FACTORY:
        num_battles, search_time_per_battle = search_time_num_battles_standard_battle(
            battle
        )
        battles = prepare_random_battles(battle, num_battles)
    elif battle.battle_type == BattleType.STANDARD_BATTLE:
        num_battles, search_time_per_battle = search_time_num_battles_standard_battle(
            battle
        )
        battles = prepare_battles(battle, num_battles)
    else:
        raise ValueError("Unsupported battle type: {}".format(battle.battle_type))

    logger.info("Searching for a move using MCTS...")
    logger.info(
        "Sampling {} battles at {}ms each".format(num_battles, search_time_per_battle)
    )
    with ProcessPoolExecutor(max_workers=FoulPlayConfig.parallelism) as executor:
        futures = []
        for index, (b, chance) in enumerate(battles):
            fut = executor.submit(
                get_result_from_mcts,
                battle_to_poke_engine_state(b).to_string(),
                search_time_per_battle,
                index,
            )
            futures.append((fut, chance, index))

    mcts_results = [(fut.result(), chance, index) for (fut, chance, index) in futures]
    choice = select_move_from_mcts_results(mcts_results, ability_state=ability_state)
    logger.info("Choice: {}".format(choice))
    return choice
