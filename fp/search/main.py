import logging
import random
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
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
    PURE_STATUS_MOVES,
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
    # Good as Gold
    POKEMON_COMMONLY_GOOD_AS_GOLD,
    # Competitive/Defiant
    POKEMON_STAT_DROP_BACKFIRES,
    STAT_LOWERING_MOVES,
    # Penalty values
    ABILITY_PENALTY_SEVERE,
    ABILITY_PENALTY_MEDIUM,
    ABILITY_PENALTY_LIGHT,
    # Mold Breaker
    MOLD_BREAKER_ABILITIES,
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
    has_good_as_gold: bool = False  # Blocks ALL status moves
    has_competitive_defiant: bool = False  # Competitive or Defiant
    has_focus_sash: bool = False  # Focus Sash item
    has_phazing: bool = False  # Has revealed a phazing move
    has_substitute: bool = False  # Currently behind a Substitute
    has_contact_punish: bool = False  # Iron Barbs, Rough Skin, or Rocky Helmet
    has_status: bool = False  # Already has a status condition
    pokemon_name: str = ""
    ability_known: bool = False
    ability_name: str = ""
    at_full_hp: bool = True  # Whether opponent is at full HP (for Sash)


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

    # Check if our Pokemon has Mold Breaker (ignores defensive abilities)
    our_has_mold_breaker = False
    if battle.user.active is not None:
        our_ability = battle.user.active.ability
        if our_ability and our_ability in MOLD_BREAKER_ABILITIES:
            our_has_mold_breaker = True

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

    # Competitive/Defiant
    state.has_competitive_defiant = _check_ability_or_pokemon(
        ability,
        name,
        base_name,
        {"competitive", "defiant"},
        POKEMON_STAT_DROP_BACKFIRES,
    )

    # Focus Sash detection
    item = getattr(opponent, "item", None)
    if item == "focussash":
        state.has_focus_sash = True
    elif item in (None, "unknownitem", "") and name in POKEMON_COMMONLY_FOCUS_SASH:
        state.has_focus_sash = True

    # Check if opponent is at full HP (Sash only works at full HP)
    state.at_full_hp = opponent.hp == opponent.max_hp

    # Phazing detection - check if opponent has revealed phazing moves
    for move in opponent.moves:
        move_name = move.name if hasattr(move, "name") else str(move)
        if move_name in PHAZING_MOVES:
            state.has_phazing = True
            break

    # Substitute detection
    volatile_statuses = getattr(opponent, "volatile_statuses", [])
    if "substitute" in volatile_statuses:
        state.has_substitute = True

    # Contact punishment detection (Iron Barbs, Rough Skin, Rocky Helmet)
    # Check known ability
    if ability and ability in {"ironbarbs", "roughskin"}:
        state.has_contact_punish = True
    # Check known item
    elif item and item == "rockyhelmet":
        state.has_contact_punish = True
    # Infer from common Pokemon
    elif (
        name in POKEMON_COMMONLY_IRON_BARBS
        or name in POKEMON_COMMONLY_ROUGH_SKIN
        or name in POKEMON_COMMONLY_ROCKY_HELMET
    ):
        state.has_contact_punish = True

    # Status detection - check if opponent already has a status condition
    opponent_status = getattr(opponent, "status", None)
    if opponent_status is not None:
        state.has_status = True

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
    # Quick exit if no problematic abilities/states detected
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
            ability_state.has_focus_sash and ability_state.at_full_hp,
            ability_state.has_phazing,
            ability_state.has_substitute,
            ability_state.has_contact_punish,
            ability_state.has_status,
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

        # Good as Gold: NEVER use status moves (they're completely blocked)
        if ability_state.has_good_as_gold and move_name in MAGIC_BOUNCE_REFLECTED_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Good as Gold (status moves blocked)"

        # Competitive/Defiant: penalize stat-lowering moves
        if (
            ability_state.has_competitive_defiant
            and move_name in STAT_LOWERING_MOVES
        ):
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Competitive/Defiant (stat drops boost them)"

        # Focus Sash at full HP: boost multi-hit and priority moves
        # We do this by penalizing non-multi-hit, non-priority moves slightly
        if ability_state.has_focus_sash and ability_state.at_full_hp:
            if move_name in MULTI_HIT_MOVES:
                # Boost multi-hit moves (they break sash + deal damage)
                penalty = min(penalty, 1.0)  # no penalty
                # Actually boost by not penalizing - other moves get penalized
            elif move_name in PRIORITY_MOVES:
                # Priority is useful but doesn't break sash alone
                penalty = min(penalty, 1.0)
            elif move_name in SETUP_MOVES:
                # Setup is fine against sash users (they're usually frail)
                penalty = min(penalty, 1.0)
            # Don't penalize regular attacks - sash is annoying but not game-losing

        # Setup vs Phazers: penalize setup moves if opponent has phazing
        if ability_state.has_phazing and move_name in SETUP_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Phazer detected (setup boosts will be wasted)"

        # Substitute: penalize status-only moves that fail against sub
        if ability_state.has_substitute and move_name in STATUS_ONLY_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Substitute up (status moves fail)"

        # Contact punishment: penalize contact moves vs Iron Barbs/Rough Skin/Rocky Helmet
        if ability_state.has_contact_punish and move_name in CONTACT_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_LIGHT)
            reason = f"{ability_state.pokemon_name} has contact punishment (Iron Barbs/Rough Skin/Rocky Helmet)"

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
MAX_DECISION_TIME_SECONDS = 30
# When in time pressure (<60s remaining), use a much tighter budget
MAX_DECISION_TIME_PRESSURE_SECONDS = 8


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
        for move in battle.user.active.moves:
            if hasattr(move, "disabled") and move.disabled:
                continue
            if hasattr(move, "current_pp") and move.current_pp <= 0:
                continue
            move_name = move.name if hasattr(move, "name") else str(move)
            logger.warning(f"Timeout fallback: selecting {move_name}")
            return move_name

    # If no moves available, try switching
    for pkmn in battle.user.reserve:
        if pkmn.hp > 0:
            logger.warning(f"Timeout fallback: switching to {pkmn.name}")
            return f"switch {pkmn.name}"

    logger.error("Timeout fallback: no moves or switches available!")
    return "splash"


def find_best_move(battle: Battle) -> str:
    start_time = time.time()
    battle = deepcopy(battle)
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
    if ability_state.has_focus_sash and ability_state.at_full_hp:
        detected_abilities.append("Focus Sash (full HP)")
    if ability_state.has_phazing:
        detected_abilities.append("Phazer")
    if ability_state.has_substitute:
        detected_abilities.append("Substitute")
    if ability_state.has_contact_punish:
        detected_abilities.append("Contact Punishment (Iron Barbs/Rough Skin/Rocky Helmet)")
    if ability_state.has_status:
        detected_abilities.append("Already statused (pure status moves will fail)")

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

    # Adjust search time if we're running low on time budget
    elapsed = time.time() - start_time
    remaining_budget = time_budget - elapsed - 2.0  # 2s safety margin
    if remaining_budget <= 0:
        logger.warning("No time left for MCTS, using fallback")
        return _get_fallback_move(battle)

    # Cap per-battle search time to fit within budget
    max_per_battle_ms = int((remaining_budget * 1000) / max(num_battles / FoulPlayConfig.parallelism, 1))
    if max_per_battle_ms < search_time_per_battle:
        logger.info(
            f"Reducing search time from {search_time_per_battle}ms to "
            f"{max_per_battle_ms}ms to fit time budget"
        )
        search_time_per_battle = max(max_per_battle_ms, 10)  # minimum 10ms

    logger.info("Searching for a move using MCTS...")
    logger.info(
        "Sampling {} battles at {}ms each".format(num_battles, search_time_per_battle)
    )

    # Calculate timeout for the executor (remaining budget in seconds)
    executor_timeout = time_budget - (time.time() - start_time) - 1.0

    try:
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

        # Collect results with timeout
        mcts_results = []
        for fut, chance, index in futures:
            try:
                remaining = max(executor_timeout - (time.time() - start_time), 0.5)
                result = fut.result(timeout=remaining)
                mcts_results.append((result, chance, index))
            except (FuturesTimeoutError, TimeoutError):
                logger.warning(f"MCTS battle {index} timed out, skipping")
            except Exception as e:
                logger.warning(f"MCTS battle {index} failed: {e}")

        if not mcts_results:
            logger.warning("All MCTS searches failed/timed out, using fallback")
            return _get_fallback_move(battle)

        choice = select_move_from_mcts_results(mcts_results, ability_state=ability_state)
    except Exception as e:
        logger.error(f"MCTS search failed entirely: {e}")
        return _get_fallback_move(battle)

    elapsed_total = time.time() - start_time
    logger.info(f"Choice: {choice} (decided in {elapsed_total:.1f}s)")
    return choice
