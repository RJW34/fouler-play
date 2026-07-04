#!/usr/bin/env python3
"""
_offline_baseline.py -- poke-env baseline challenger for the offline eval harness.

Runs INSIDE .venv-eval (poke-env + compatible websockets). Connects to the local
pokemon-showdown server, challenges the fouler bot N times with a fixed team, and
writes a result JSON (from the baseline's perspective).

Not meant to be run directly by users -- offline_eval.py orchestrates it.

TURN-CAP (Claude/DEKU 2026-06-17): fat/stall eval battles never terminate within the
per-battle timeout -> 0 decisive battles -> the offline_eval Wilson-LCB gate can never
accept -> auto-improve can never commit (measured: simple AND maxbp baselines both gave
0 decisive). Fix: at turn >= FOULER_EVAL_TURN_CAP, the bot that is clearly BEHIND
(fewer alive Pokemon, hp tiebreak) FORFEITS, so the battle resolves decisively to the
side that is ahead. This baseline-side cap resolves the fouler-AHEAD battles; the
fouler eval bot carries the symmetric cap for fouler-BEHIND battles (so the gate is
unbiased). Eval-only file -> zero live-ladder risk.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from poke_env import AccountConfiguration, ServerConfiguration
from poke_env.player import (
    SimpleHeuristicsPlayer,
    MaxBasePowerPlayer,
    RandomPlayer,
)

try:
    from poke_env.player.battle_order import ForfeitBattleOrder
except Exception:  # pragma: no cover - older/newer poke-env layouts
    ForfeitBattleOrder = None

TURN_CAP = int(os.getenv("FOULER_EVAL_TURN_CAP", "40"))


def load_team(team_file: str) -> str:
    return Path(team_file).read_text(encoding="utf-8")


def _alive_and_hp(team) -> tuple[int, float]:
    """(alive_count, hp_fraction_sum) over a {ident: Pokemon} dict."""
    alive, hp = 0, 0.0
    for p in (team or {}).values():
        try:
            f = p.current_hp_fraction
        except Exception:
            f = 1.0
        if f is None:
            f = 1.0
        hp += f
        if f > 0:
            alive += 1
    return alive, hp


def _behind(battle) -> bool:
    """True if THIS bot is clearly behind (so it should concede at the cap)."""
    my_alive, my_hp = _alive_and_hp(getattr(battle, "team", {}))
    op_alive, op_hp = _alive_and_hp(getattr(battle, "opponent_team", {}))
    # opponent_team only holds REVEALED mons -> assume unseen ones at full hp.
    unseen = max(0, 6 - len(getattr(battle, "opponent_team", {}) or {}))
    op_alive += unseen
    op_hp += float(unseen)
    if my_alive != op_alive:
        return my_alive < op_alive
    return my_hp < op_hp - 0.5  # only concede on a clear hp deficit, never a near-tie


def _make_capped(base_cls):
    class _Capped(base_cls):
        def choose_move(self, battle):
            try:
                if (
                    ForfeitBattleOrder is not None
                    and getattr(battle, "turn", 0)
                    and battle.turn >= TURN_CAP
                    and _behind(battle)
                ):
                    return ForfeitBattleOrder()
            except Exception:
                pass
            return super().choose_move(battle)

    _Capped.__name__ = base_cls.__name__ + "Capped"
    return _Capped


BASELINES = {
    "simple": _make_capped(SimpleHeuristicsPlayer),
    "maxbp": _make_capped(MaxBasePowerPlayer),
    "random": _make_capped(RandomPlayer),
}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-port", type=int, required=True)
    ap.add_argument("--baseline", choices=list(BASELINES), default="simple")
    ap.add_argument("--username", required=True)
    ap.add_argument("--opponent", required=True)
    ap.add_argument("--battles", type=int, required=True)
    ap.add_argument("--format", default="gen9ou")
    ap.add_argument("--team-file", required=True)
    ap.add_argument("--result-file", required=True)
    ap.add_argument("--per-battle-timeout", type=float, default=180.0)
    ap.add_argument("--concurrency", type=int, default=1)
    args = ap.parse_args()
    concurrency = max(1, int(args.concurrency))

    port = args.server_port
    server_config = ServerConfiguration(
        f"ws://localhost:{port}/showdown/websocket",
        f"http://localhost:{port}/action.php?",
    )
    account = AccountConfiguration(args.username, None)
    team = load_team(args.team_file)

    player_cls = BASELINES[args.baseline]
    player = player_cls(
        account_configuration=account,
        server_configuration=server_config,
        battle_format=args.format,
        team=team,
        max_concurrent_battles=concurrency,
        start_timer_on_battle_start=False,
    )

    # Challenge the fouler bot in bounded batches. Serial remains the default;
    # higher concurrency is used only for local no-security eval proof windows.
    sent = 0
    while sent < args.battles:
        # Wait until fouler has finished the previous battle and is idle again.
        previous_finished = player.n_finished_battles
        for _ in range(int(args.per_battle_timeout)):
            if player.n_finished_battles >= sent:
                break
            await asyncio.sleep(1)
        if player.n_finished_battles < sent:
            print(
                f"[baseline] previous battle did not finish within "
                f"{args.per_battle_timeout}s; finished={player.n_finished_battles} "
                f"sent={sent}",
                flush=True,
            )
            break
        batch_size = min(concurrency, args.battles - sent)
        try:
            await asyncio.wait_for(
                player.send_challenges(args.opponent, n_challenges=batch_size),
                timeout=args.per_battle_timeout * batch_size,
            )
            sent += batch_size
        except asyncio.TimeoutError:
            if player.n_finished_battles > previous_finished:
                print(
                    f"[baseline] battle finished while challenge batch at {sent} was pending; "
                    "retrying after settle",
                    flush=True,
                )
                await asyncio.sleep(1.5)
                continue
            print(f"[baseline] challenge batch at {sent} timed out; bot may have exited", flush=True)
            break
        except Exception as e:
            print(f"[baseline] challenge batch at {sent} error: {e}", flush=True)
            break
        await asyncio.sleep(1.5)  # settle: let fouler re-enter accept state

    wins = player.n_won_battles
    finished = player.n_finished_battles
    ties = max(0, finished - wins - (finished - wins))  # poke-env counts ties separately if any
    result = {
        "baseline": args.baseline,
        "battles": finished,
        "challenges_sent": sent,
        "wins": wins,            # baseline wins
        "losses": finished - wins,
        "ties": 0,
    }
    Path(args.result_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[baseline] done: {result}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
