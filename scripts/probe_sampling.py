#!/usr/bin/env python3
"""
probe_sampling.py - empirical check of whether MCTS searches a REAL sampled
opponent team or a blank/dummy one.

Builds a minimal gen9ou battle at the post-team-preview state with a partially
revealed opponent, runs prepare_battles() + battle_to_poke_engine_state(), and
reports for each sampled state how many opponent reserve slots are REAL
(sampled set with moves/item) vs DUMMY (pikachu lvl1 hp0 placeholder).
"""
import os
import sys
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants
from fp.battle import Battle, Pokemon
from data.pkmn_sets import SmogonSets, TeamDatasets
from fp.search.standard_battles import prepare_battles
from fp.search.poke_engine_helpers import battle_to_poke_engine_state


def make_pkmn(name, moves=None):
    p = Pokemon(name, 100)
    for m in (moves or []):
        p.add_move(m)
    return p


def main():
    fmt = "gen9ou"
    # Initialize the set datasets the way run_battle does at team preview
    opp_team = [
        "gholdengo", "kingambit", "greattusk", "dragapult", "ironvaliant", "ogerpon",
    ]
    our_team = ["toxapex", "corviknight", "blissey", "garganacl", "clodsire", "dondozo"]

    try:
        SmogonSets.initialize(fmt, opp_team + our_team)
    except Exception as e:
        print(f"SmogonSets.initialize failed: {e}")
    try:
        TeamDatasets.initialize(fmt, opp_team)
    except Exception as e:
        print(f"TeamDatasets.initialize note: {e}")

    from constants import BattleType as BT
    battle = Battle("probe-battle")
    battle.battle_type = BT.STANDARD_BATTLE
    battle.generation = "gen9"
    battle.pokemon_format = fmt
    battle.team_preview = False

    # our side
    battle.user.active = make_pkmn("toxapex", ["scald", "recover", "toxic", "haze"])
    battle.user.reserve = [make_pkmn(n) for n in our_team[1:]]

    # opponent: 1 active partially revealed, 2 revealed reserve, 3 unrevealed (known by name at preview)
    battle.opponent.active = make_pkmn("gholdengo", ["makeitrain"])
    battle.opponent.reserve = [
        make_pkmn("kingambit", ["suckerpunch"]),
        make_pkmn("greattusk", ["earthquake"]),
        make_pkmn("dragapult"),
        make_pkmn("ironvaliant"),
        make_pkmn("ogerpon"),
    ]

    N = 6
    sampled = prepare_battles(battle, N)
    print(f"prepare_battles returned {len(sampled)} sampled states (requested {N})")

    for i, (b, w) in enumerate(sampled):
        opp = [b.opponent.active] + list(b.opponent.reserve)
        real = 0
        dummy_like = 0
        details = []
        for p in opp:
            if p is None:
                continue
            nmoves = len([m for m in p.moves if m.name not in ("none", "")])
            has_item = bool(p.item) and p.item not in (None, "", "unknown_item", constants.UNKNOWN_ITEM)
            is_real = nmoves >= 1
            if is_real:
                real += 1
            else:
                dummy_like += 1
            details.append(f"{p.name}(mv={nmoves},item={p.item})")
        print(f"  state[{i}] w={w:.3f} real={real} thin={dummy_like} :: {', '.join(details)}")

    # Now the poke-engine state: count dummy pikachu placeholders
    print("\n--- poke-engine state opponent side ---")
    for i, (b, w) in enumerate(sampled[:2]):
        state = battle_to_poke_engine_state(b)
        s = state.to_string()
        dummies = s.lower().count("pikachu")
        print(f"  state[{i}] pikachu-dummy count in serialized state: {dummies}")

    return battle


def mcts_ab(battle):
    """
    Deterministic search-quality A/B: run the MCTS policy pass on the SAME position
    with opponent set-sampling ON vs OFF (FOULER_FORCE_NO_SETSAMPLE), and report how
    many opponent moves the engine actually sees and the resulting move policy. With
    sampling OFF, fully-unrevealed opponents are inert (no threats), so MCTS optimizes
    against a blank wall; with sampling ON it must answer real threats.
    """
    import importlib
    from concurrent.futures import ThreadPoolExecutor

    def run_once(no_setsample: bool):
        os.environ["FOULER_FORCE_NO_SETSAMPLE"] = "1" if no_setsample else "0"
        import fp.search.standard_battles as sb
        importlib.reload(sb)
        from poke_engine import monte_carlo_tree_search

        samples = sb.prepare_battles(deepcopy(battle), 4)
        # Count opponent moves visible to the engine across samples
        total_moves = 0
        for b, _w in samples:
            for p in [b.opponent.active] + list(b.opponent.reserve):
                if p is None:
                    continue
                total_moves += len([m for m in p.moves if m.name not in ("none", "")])
        # Run MCTS on the top-weighted sample
        state = battle_to_poke_engine_state(sorted(samples, key=lambda x: -x[1])[0][0])
        with ThreadPoolExecutor(max_workers=1) as ex:
            result = ex.submit(monte_carlo_tree_search, state, 800).result(timeout=20)
        policy = {}
        tv = float(getattr(result, "total_visits", 0) or 0) or 1.0
        for opt in getattr(result, "side_one", []) or []:
            mv = str(getattr(opt, "move_choice", "") or "")
            v = float(getattr(opt, "visits", 0) or 0)
            if mv and v > 0:
                policy[mv] = round(v / tv, 3)
        top = sorted(policy.items(), key=lambda x: -x[1])[:4]
        return total_moves, top

    print("\n=== MCTS search-quality A/B (same position) ===")
    moves_off, top_off = run_once(no_setsample=True)
    moves_on, top_on = run_once(no_setsample=False)
    print(f"  set-sampling OFF (pre-fix): opp moves visible to engine = {moves_off}")
    print(f"      MCTS top policy: {top_off}")
    print(f"  set-sampling ON  (fixed):   opp moves visible to engine = {moves_on}")
    print(f"      MCTS top policy: {top_on}")
    print(f"  -> visibility gain: {moves_on - moves_off} more opponent moves searched")
    os.environ["FOULER_FORCE_NO_SETSAMPLE"] = "0"


if __name__ == "__main__":
    b = main()
    try:
        mcts_ab(b)
    except Exception as e:
        print(f"[mcts_ab] skipped ({e})")
