"""Upstream-ported MCTS orchestration (foul-play main branch, fetched 2026-08-01),
adapted to this fork. Selected via FOULER_SEARCH_PATH=clean (fp/search/dispatch.py);
the legacy 8,200-line pipeline remains the default until this path is live-validated,
then gets removed.

Deltas vs upstream, each deliberate:
- Sample aggregation weights are UNIFORM (1/n): samples are already posterior draws,
  and the fork's raw-count product weights are mathematically broken (gap-map #31).
- The ProcessPoolExecutor is module-persistent: per-call pools pay Windows spawn cost
  (~1s+/proc) that would eat the per-turn budget. Any pool failure falls back to the
  sequential inline loop for that turn and the pool is rebuilt lazily.
- Child processes return plain tuples, not MctsResult (pyo3 pickling not assumed).
- Critical-clock fallback preserved (inactivity-forfeit safety); run_battle's outer
  side-clock timeout remains the last-resort backstop.
- Returns (choice, trace) matching the legacy contract so run_battle, the overlay
  panels, and quality_audit keep working unchanged.
"""
import logging
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy

import constants
from config import FoulPlayConfig
from data import all_move_json
from poke_engine import State as PokeEngineState, monte_carlo_tree_search

from fp.decision_trace import build_trace_base
from fp.helpers import type_effectiveness_modifier
from fp.search.poke_engine_helpers import battle_to_poke_engine_state
from fp.search.standard_battles import prepare_battles

logger = logging.getLogger(__name__)

CRITICAL_CLOCK_S = int(os.getenv("CLEAN_CRITICAL_CLOCK_S", "25"))
MIN_SEARCH_MS = int(os.getenv("CLEAN_MIN_SEARCH_MS", "800"))
NEAR_BEST_FRACTION = 0.75  # upstream: consider all moves within 75% of the best
# poke-engine >=0.0.47 threads per MCTS call. 1 on starved boxes (JIGGLY: measured
# 0.96x, pure contention); raise via env on big-core hosts (RWLEGION migration).
SEARCH_THREADS = max(1, int(os.getenv("CLEAN_SEARCH_THREADS", "1")))

_POOL = None


def _get_fallback_move(battle) -> str:
    """Emergency fallback (salvaged verbatim from the retired legacy path, per the
    P-3 salvage manifest): pick the best simple move without MCTS. Used on critical
    clock or search failure to avoid forfeiting the turn."""
    if battle.force_switch:
        for pkmn in battle.user.reserve:
            if pkmn.hp > 0:
                logger.warning(f"Timeout fallback (force_switch): switching to {pkmn.name}")
                return f"switch {pkmn.name}"
        logger.error("Timeout fallback: force_switch active but no alive reserves!")
        return "switch 1"

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

    for pkmn in battle.user.reserve:
        if pkmn.hp > 0:
            logger.warning(f"Timeout fallback: switching to {pkmn.name}")
            return f"switch {pkmn.name}"

    logger.error("Timeout fallback: no moves or switches available!")
    return "splash"


def _get_pool():
    global _POOL
    if _POOL is None:
        workers = max(1, int(getattr(FoulPlayConfig, "parallelism", None) or 3))
        _POOL = ProcessPoolExecutor(max_workers=workers)
    return _POOL


def _kill_pool():
    global _POOL
    try:
        if _POOL is not None:
            _POOL.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    _POOL = None


def _mcts_rows_from_state_string(state_str: str, search_time_ms: int):
    """Child-process entry: run MCTS and reduce the result to picklable tuples."""
    state = PokeEngineState.from_string(state_str)
    if SEARCH_THREADS > 1:
        res = monte_carlo_tree_search(state, search_time_ms, threads=SEARCH_THREADS)
    else:
        res = monte_carlo_tree_search(state, search_time_ms)
    rows = [(o.move_choice, int(o.visits), float(o.total_score)) for o in res.side_one]
    return rows, int(res.total_visits)


def _select_move(results):
    """Upstream's select_move_from_mcts_results, on reduced tuples.

    results: list of (rows, total_visits, chance, index)."""
    final_policy = {}
    for rows, total_visits, chance, index in results:
        if not rows or total_visits <= 0:
            continue
        best = max(rows, key=lambda r: r[1])
        logger.info(
            "Policy %s: %s visited %.2f%% avg_score=%.3f chance=%.3f",
            index, best[0],
            100.0 * best[1] / total_visits,
            (best[2] / best[1]) if best[1] else 0.0,
            chance,
        )
        for move, visits, _score in rows:
            final_policy[move] = final_policy.get(move, 0.0) + chance * (
                visits / total_visits
            )
    if not final_policy:
        return None, {}, []
    ranked = sorted(final_policy.items(), key=lambda kv: kv[1], reverse=True)
    threshold = ranked[0][1] * NEAR_BEST_FRACTION
    considered = [kv for kv in ranked if kv[1] >= threshold]
    logger.info("Considered choices:")
    for pct, mv in ((kv[1], kv[0]) for kv in considered):
        logger.info("\t%.3f%%: %s", pct * 100.0, mv)
    choice = random.choices(considered, weights=[kv[1] for kv in considered])[0][0]
    return choice, final_policy, [kv[0] for kv in considered]


def find_best_move(battle):
    t0 = time.time()
    # build_trace_base supplies battle_tag/turn/etc. -- write_decision_trace silently
    # drops traces without them (found the hard way on the first canary batch).
    try:
        trace = build_trace_base(battle)
    except Exception:
        trace = {}
    trace.update({
        "decision_mode": "mcts",
        "decision_mode_detail": "mcts_clean_port",
        "search_path": "clean",
    })
    time_remaining = getattr(battle, "time_remaining", None)

    # Inactivity-forfeit safety: on a critical clock skip search entirely.
    if time_remaining is not None and time_remaining <= CRITICAL_CLOCK_S:
        move = _get_fallback_move(battle)
        trace.update(
            decision_mode_detail="clean:critical_clock_fallback",
            choice=move,
            decision_time_s=round(time.time() - t0, 3),
        )
        logger.warning("clean path: critical clock (%.0fs) -> fallback %s", time_remaining, move)
        return move, trace

    battle = deepcopy(battle)
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    num_battles = int(getattr(FoulPlayConfig, "max_mcts_battles", None) or 3)
    search_ms = max(MIN_SEARCH_MS, int(getattr(FoulPlayConfig, "search_time_ms", None) or 2500))
    if time_remaining is not None and time_remaining < 90:
        # crude clock taper; run_battle's outer timeout is the hard backstop
        search_ms = max(MIN_SEARCH_MS, min(search_ms, int(time_remaining * 1000 // 12)))

    samples = prepare_battles(battle, num_battles)
    # prepare_battles returns (Battle, weight) pairs; the weights are the fork's
    # broken raw-count products -- ignored on purpose (uniform below).
    state_strings = []
    for b, _broken_weight in samples:
        state_strings.append(battle_to_poke_engine_state(b).to_string())
    if not state_strings:
        move = _get_fallback_move(battle)
        trace.update(
            decision_mode_detail="clean:no_samples_fallback",
            choice=move,
            decision_time_s=round(time.time() - t0, 3),
        )
        return move, trace

    chance = 1.0 / len(state_strings)
    results = []
    pool_mode = "process_pool"
    mcts_t0 = time.time()
    try:
        pool = _get_pool()
        futures = [
            (pool.submit(_mcts_rows_from_state_string, s, search_ms), idx)
            for idx, s in enumerate(state_strings)
        ]
        # generous per-turn ceiling; outer run_battle timeout is the real backstop
        ceiling = (search_ms / 1000.0) * 2.5 + 10.0
        for fut, idx in futures:
            rows, total_visits = fut.result(timeout=ceiling)
            results.append((rows, total_visits, chance, idx))
    except Exception as exc:
        logger.warning("clean path: pool failed (%s) -> inline sequential", exc)
        _kill_pool()
        pool_mode = "inline_fallback"
        results = []
        for idx, s in enumerate(state_strings):
            rows, total_visits = _mcts_rows_from_state_string(s, search_ms)
            results.append((rows, total_visits, chance, idx))
    mcts_elapsed = time.time() - mcts_t0

    choice, final_policy, considered = _select_move(results)
    if choice is None:
        choice = _get_fallback_move(battle)
        trace["decision_mode_detail"] = "clean:empty_policy_fallback"

    trace.update(
        choice=choice,
        mcts_policy_raw={k: round(v, 6) for k, v in final_policy.items()},
        considered_choices=considered,
        mcts_meta={
            "samples_attempted": len(state_strings),
            "samples_succeeded": len(results),
            "per_sample_ms": round(1000.0 * mcts_elapsed / max(1, len(results)), 1),
            "search_time_ms_per_sample": search_ms,
            "pool_mode": pool_mode,
            "aggregation": "uniform_visit_share",
        },
        decision_time_s=round(time.time() - t0, 3),
    )
    logger.info("clean path choice: %s (%.2fs)", choice, trace["decision_time_s"])
    return choice, trace
