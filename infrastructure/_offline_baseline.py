#!/usr/bin/env python3
"""
_offline_baseline.py -- poke-env baseline challenger for the offline eval harness.

Runs INSIDE .venv-eval (poke-env + compatible websockets). Connects to the local
pokemon-showdown server, challenges the fouler bot N times with a fixed team, and
writes a result JSON (from the baseline's perspective).

Not meant to be run directly by users -- offline_eval.py orchestrates it.
"""
import argparse
import asyncio
import json
from pathlib import Path

from poke_env import AccountConfiguration, ServerConfiguration
from poke_env.player import (
    SimpleHeuristicsPlayer,
    MaxBasePowerPlayer,
    RandomPlayer,
)


def load_team(team_file: str) -> str:
    return Path(team_file).read_text(encoding="utf-8")


BASELINES = {
    "simple": SimpleHeuristicsPlayer,
    "maxbp": MaxBasePowerPlayer,
    "random": RandomPlayer,
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
    args = ap.parse_args()

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
        max_concurrent_battles=1,
        start_timer_on_battle_start=False,
    )

    # Challenge the fouler bot N times, one at a time so fouler (single worker)
    # can accept each in turn. A short settle delay between challenges gives
    # fouler's worker loop time to re-enter accept_challenge state, avoiding a
    # race where a challenge is issued before the bot is listening (which would
    # otherwise block send_challenges forever).
    sent = 0
    for i in range(args.battles):
        # Wait until fouler has finished the previous battle and is idle again.
        for _ in range(int(args.per_battle_timeout)):
            if player.n_finished_battles >= i:
                break
            await asyncio.sleep(1)
        try:
            await asyncio.wait_for(
                player.send_challenges(args.opponent, n_challenges=1),
                timeout=args.per_battle_timeout,
            )
            sent += 1
        except asyncio.TimeoutError:
            print(f"[baseline] challenge {i} timed out; bot may have exited", flush=True)
            break
        except Exception as e:
            print(f"[baseline] challenge {i} error: {e}", flush=True)
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
