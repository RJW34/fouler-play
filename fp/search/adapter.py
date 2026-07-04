"""
Thin fork-owned adapter between the upstream foul-play decision engine
(fp/search/main.py -- kept at upstream state) and the fouler-play ops
harness (fp/run_battle.py, replay_analysis evidence machinery, battle_stats).

This module is the ONLY fork-owned code in the decision path. The decision
policy itself (opponent-set sampling, MCTS, visit-weighted selection) is
upstream's, untouched. The adapter adds exactly:

1. CLOCK-BUDGET CLAMP (ported minimally from fork commit a7bd115c
   "bind per-decision budget to the side clock"): bound the total MCTS wall
   time of one decision to a slice of the remaining side clock so search can
   never walk the bank into an inactivity forfeit. The outer backstop in
   run_battle.async_pick_move (fork commit 363a934e, side_clock-6 timeout)
   remains the last line of defense; the countdown-consumption fix
   (fork commit bb9ba617, ported into fp/battle_modifier.py) keeps
   battle.time_remaining truthful in the danger zone. Those three are the
   proven unattended-ladder safeguards named by the 2026-07-04 divergence
   audit.
2. DECISION-TRACE capture for replay_analysis: per-sample MCTS policy
   summary + aggregate considered choices, returned alongside the move so
   run_battle can write it via fp.decision_trace.write_decision_trace.
3. Config hooks the ops harness already exposes: MAX_MCTS_BATTLES caps the
   sampled-battle count (hidden-information robustness knob in .env).

NOT ported (deliberately): the matchup-memory A/B bias hook point. The bias
was A/B-measured harmful (ON 44.4% n=925 vs OFF 47.5% n=961) and retired on
2026-07-04, so the new engine surface ships without the hook.
"""

import logging
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy

from constants import BattleType
from config import FoulPlayConfig
from fp.battle import Battle
from fp.decision_trace import build_trace_base

from fp.search.main import (
    get_result_from_mcts,
    select_move_from_mcts_results,
    search_time_num_battles_randombattles,
    search_time_num_battles_standard_battle,
)
from fp.search.standard_battles import prepare_battles
from fp.search.random_battles import prepare_random_battles
from fp.search.poke_engine_helpers import battle_to_poke_engine_state

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Fork a7bd115c constants (env-overridable, same knobs the fork exposed).
MAX_DECISION_TIME_SECONDS = _env_float("MAX_DECISION_TIME_SECONDS", 6.0)
MAX_DECISION_TIME_PRESSURE_SECONDS = _env_float(
    "MAX_DECISION_TIME_PRESSURE_SECONDS", 3.0
)
MIN_DECISION_TIME_SECONDS = _env_float("MIN_DECISION_TIME_SECONDS", 1.0)
DECISION_EXPECTED_TURNS_LEFT = int(_env_float("DECISION_EXPECTED_TURNS_LEFT", 18))
DECISION_CLOCK_RESERVE_SECONDS = _env_float("DECISION_CLOCK_RESERVE_SECONDS", 12.0)
DECISION_UNKNOWN_CLOCK_ASSUMED_SECONDS = _env_float(
    "DECISION_UNKNOWN_CLOCK_ASSUMED_SECONDS", 90.0
)
# Never starve a sample below this many ms (but never raise above the
# operator-configured search_time_ms, so eval-gate configs stay authoritative).
MIN_SEARCH_TIME_MS_FLOOR = int(_env_float("MIN_SEARCH_TIME_MS_FLOOR", 120))


def _compute_decision_budget_seconds(battle: Battle) -> tuple[float, dict]:
    """Wall-clock seconds this ONE decision may spend, bound to the side clock.

    budget = clamp( (remaining_side_clock - reserve) / expected_remaining_turns,
                    MIN_DECISION_TIME_SECONDS, MAX_DECISION_TIME_SECONDS )

    Ported from fork commit a7bd115c (minimal form: budget math + pressure
    tiers only; the fork's multi-stage deadline plumbing is unnecessary here
    because the upstream pipeline has a single MCTS stage).
    """
    clock = battle.time_remaining
    clock_known = clock is not None
    if not clock_known:
        clock = DECISION_UNKNOWN_CLOCK_ASSUMED_SECONDS

    clock = float(clock)
    turn_num = battle.turn if isinstance(battle.turn, int) and battle.turn > 0 else 0
    est_turns_left = max(2, DECISION_EXPECTED_TURNS_LEFT - turn_num // 2)

    usable = clock - DECISION_CLOCK_RESERVE_SECONDS
    if usable <= 0:
        budget = MIN_DECISION_TIME_SECONDS
    else:
        budget = usable / est_turns_left

    budget = max(MIN_DECISION_TIME_SECONDS, min(MAX_DECISION_TIME_SECONDS, budget))

    if clock_known:
        if clock <= 20:
            budget = min(budget, MIN_DECISION_TIME_SECONDS)
        elif clock <= 35:
            budget = min(budget, 1.5)
        elif clock <= 60:
            budget = min(budget, MAX_DECISION_TIME_PRESSURE_SECONDS)

    meta = {
        "clock_known": clock_known,
        "remaining_clock_s": round(clock, 1),
        "est_turns_left": est_turns_left,
        "budget_s": round(budget, 2),
    }
    return budget, meta


def _clamp_search_time_to_side_clock(
    battle: Battle, num_battles: int, search_time_per_battle_ms: int
) -> tuple[int, dict]:
    """Clamp the per-sample MCTS ms so the whole decision fits the clock budget.

    Samples run in parallel batches of FoulPlayConfig.parallelism, so total
    search wall time ~= ceil(num_battles / parallelism) * per_sample_ms.
    """
    budget_s, meta = _compute_decision_budget_seconds(battle)
    batches = max(1, math.ceil(num_battles / max(1, FoulPlayConfig.parallelism)))
    per_sample_budget_ms = int((budget_s * 1000.0) / batches)

    clamped = min(int(search_time_per_battle_ms), per_sample_budget_ms)
    clamped = max(clamped, min(MIN_SEARCH_TIME_MS_FLOOR, int(search_time_per_battle_ms)))

    meta.update(
        {
            "configured_per_sample_ms": int(search_time_per_battle_ms),
            "batches": batches,
            "clamped_per_sample_ms": clamped,
            "clamp_applied": clamped < int(search_time_per_battle_ms),
        }
    )
    if meta["clamp_applied"]:
        logger.info(
            "Clock clamp: per-sample search %sms -> %sms (clock=%ss, budget=%ss, %s batches)",
            int(search_time_per_battle_ms),
            clamped,
            meta["remaining_clock_s"],
            meta["budget_s"],
            batches,
        )
    return clamped, meta


def _aggregate_policy(mcts_results) -> list[dict]:
    """Same aggregation math as upstream select_move_from_mcts_results, captured
    for the decision trace (upstream only logs it)."""
    final_policy = {}
    for mcts_result, sample_chance, _index in mcts_results:
        for s1_option in mcts_result.side_one:
            final_policy[s1_option.move_choice] = final_policy.get(
                s1_option.move_choice, 0
            ) + (sample_chance * (s1_option.visits / mcts_result.total_visits))
    ranked = sorted(final_policy.items(), key=lambda x: x[1], reverse=True)
    return [
        {"choice": choice, "weight": round(weight, 4)} for choice, weight in ranked[:8]
    ]


def find_best_move_with_trace(battle: Battle) -> tuple[str, dict]:
    """Upstream find_best_move flow + clock clamp + trace capture.

    Mirrors fp/search/main.py:find_best_move (upstream) exactly, except:
    - per-sample search time is clamped to the side-clock budget
    - MAX_MCTS_BATTLES (ops env) caps the sampled-battle count
    - returns (choice, trace) for the ops harness
    """
    started = time.monotonic()
    battle = deepcopy(battle)
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

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

    max_mcts_battles = getattr(FoulPlayConfig, "max_mcts_battles", None)
    if max_mcts_battles:
        battles = list(battles)[: int(max_mcts_battles)]

    battles = list(battles)
    search_time_per_battle, clock_meta = _clamp_search_time_to_side_clock(
        battle, len(battles), search_time_per_battle
    )

    logger.info("Searching for a move using MCTS...")
    logger.info(
        "Sampling {} battles at {}ms each".format(len(battles), search_time_per_battle)
    )
    with ProcessPoolExecutor(max_workers=FoulPlayConfig.parallelism) as executor:
        futures = []
        for index, (b, chance) in enumerate(battles):
            fut = executor.submit(
                get_result_from_mcts,
                battle_to_poke_engine_state(b).to_string(),
                search_time_per_battle,
                index,
                FoulPlayConfig.search_threads,
            )
            futures.append((fut, chance, index))

    mcts_results = [(fut.result(), chance, index) for (fut, chance, index) in futures]
    choice = select_move_from_mcts_results(mcts_results)
    logger.info("Choice: {}".format(choice))

    trace = build_trace_base(battle, reason="mcts")
    trace["choice"] = choice
    trace["engine"] = {
        "provenance": "upstream-foul-play",
        "battles_sampled": len(battles),
        "search_time_per_battle_ms": search_time_per_battle,
        "search_threads": FoulPlayConfig.search_threads,
        "parallelism": FoulPlayConfig.parallelism,
        "total_visits": sum(r.total_visits for r, _c, _i in mcts_results),
        "elapsed_s": round(time.monotonic() - started, 3),
        "clock": clock_meta,
        "policy": _aggregate_policy(mcts_results),
        "per_sample": [
            {
                "index": index,
                "sample_chance": round(chance, 4),
                "top_choice": max(
                    result.side_one, key=lambda x: x.visits
                ).move_choice,
                "visits": result.total_visits,
            }
            for result, chance, index in mcts_results
        ],
    }
    return choice, trace
