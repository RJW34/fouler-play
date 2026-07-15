#!/usr/bin/env python3
"""Private Pokemon Showdown ladder opponent for the local Fouler rehearsal."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import re
import sys
import time
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.offline_eval_runner import (  # noqa: E402
    LoopbackNetworkGuard,
    install_loopback_network_guard,
    validate_loopback_url,
)


LOCKED_BATTLE_COUNT = 30
LOCKED_CONCURRENCY = 3
LOCKED_FORMAT = "gen9ou"


def _showdown_id(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _absolute_path(value: str, *, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path


def _loopback_endpoint_host(value: str) -> str:
    host = str(value or "").strip().lower()
    if host == "localhost":
        return "127.0.0.1"
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def _require_external_output(path: Path, *, label: str) -> None:
    project = PROJECT_ROOT.resolve(strict=False)
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(project)
    except ValueError:
        return
    raise ValueError(f"{label} must be outside the Fouler source tree")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the locked private-ladder opponent for Fouler rehearsal."
    )
    parser.add_argument("--websocket-url", required=True)
    parser.add_argument("--authentication-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--fouler-username", required=True)
    parser.add_argument("--team-file", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--network-audit-file", required=True)
    parser.add_argument("--baseline", choices=("simple", "maxbp", "random"), default="simple")
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> dict[str, object]:
    websocket = validate_loopback_url(
        args.websocket_url,
        label="Showdown websocket URL",
        schemes=frozenset({"ws"}),
    )
    authentication = validate_loopback_url(
        args.authentication_url,
        label="Showdown authentication URL",
        schemes=frozenset({"http"}),
    )
    if websocket.port != authentication.port:
        raise ValueError("Showdown websocket and authentication URLs must use the same port")
    if _loopback_endpoint_host(websocket.hostname or "") != _loopback_endpoint_host(
        authentication.hostname or ""
    ):
        raise ValueError("Showdown websocket and authentication URLs must use one loopback host")
    if _showdown_id(args.username) == _showdown_id(args.fouler_username):
        raise ValueError("opponent and Fouler usernames must be distinct")
    if not _showdown_id(args.username) or not _showdown_id(args.fouler_username):
        raise ValueError("Showdown usernames must contain an alphanumeric character")
    team_file = _absolute_path(args.team_file, label="team file")
    if not team_file.is_file():
        raise ValueError(f"team file does not exist: {team_file}")
    result_file = _absolute_path(args.result_file, label="result file")
    audit_file = _absolute_path(args.network_audit_file, label="network audit file")
    _require_external_output(result_file, label="result file")
    _require_external_output(audit_file, label="network audit file")
    if args.timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    return {
        "websocket": websocket,
        "authentication": authentication,
        "teamFile": team_file,
        "resultFile": result_file,
        "auditFile": audit_file,
    }


def _active_battle_count(player: object) -> int:
    battles = getattr(player, "battles", None)
    if not isinstance(battles, dict):
        battles = getattr(player, "_battles", {})
    if not isinstance(battles, dict):
        return 0
    return sum(
        1
        for battle in battles.values()
        if not bool(getattr(battle, "finished", False))
    )


async def _run_ladder(args: argparse.Namespace, paths: dict[str, object]) -> dict[str, object]:
    from poke_env import AccountConfiguration, ServerConfiguration

    from infrastructure._offline_baseline import BASELINES

    team_file = paths["teamFile"]
    assert isinstance(team_file, Path)
    server = ServerConfiguration(args.websocket_url, args.authentication_url)
    player = BASELINES[args.baseline](
        account_configuration=AccountConfiguration(args.username, None),
        server_configuration=server,
        battle_format=LOCKED_FORMAT,
        team=team_file.read_text(encoding="utf-8"),
        max_concurrent_battles=LOCKED_CONCURRENCY,
        save_replays=False,
        start_timer_on_battle_start=False,
    )

    peak_active = 0
    samples = 0
    started = time.monotonic()
    ladder_task = asyncio.create_task(player.ladder(LOCKED_BATTLE_COUNT))
    try:
        while not ladder_task.done():
            peak_active = max(peak_active, _active_battle_count(player))
            samples += 1
            await asyncio.sleep(0.05)
        await asyncio.wait_for(ladder_task, timeout=1.0)
    except BaseException:
        if not ladder_task.done():
            ladder_task.cancel()
            try:
                await ladder_task
            except asyncio.CancelledError:
                pass
        raise
    finally:
        peak_active = max(peak_active, _active_battle_count(player))
        try:
            await asyncio.wait_for(player.ps_client.stop_listening(), timeout=10.0)
        except Exception:
            pass

    finished = int(player.n_finished_battles)
    wins = int(player.n_won_battles)
    losses = int(player.n_lost_battles)
    ties = int(player.n_tied_battles)
    return {
        "schemaVersion": "fouler-play-local-rehearsal-opponent/v1",
        "privateServer": True,
        "matchmaking": "private-loopback-ladder",
        "format": LOCKED_FORMAT,
        "baseline": args.baseline,
        "requestedBattles": LOCKED_BATTLE_COUNT,
        "maxConcurrentBattles": LOCKED_CONCURRENCY,
        "finishedBattles": finished,
        "decisiveBattles": wins + losses,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "observedPeakActiveBattles": peak_active,
        "activeSamples": samples,
        "elapsedSeconds": round(time.monotonic() - started, 3),
    }


async def async_main(args: argparse.Namespace) -> int:
    paths = validate_args(args)
    audit_file = paths["auditFile"]
    result_file = paths["resultFile"]
    assert isinstance(audit_file, Path)
    assert isinstance(result_file, Path)
    audit: LoopbackNetworkGuard = install_loopback_network_guard(audit_path=audit_file)
    result: dict[str, object]
    exit_code = 1
    try:
        result = await asyncio.wait_for(
            _run_ladder(args, paths),
            timeout=float(args.timeout_seconds),
        )
        result["networkAudit"] = audit.snapshot()
        valid = (
            result["finishedBattles"] == LOCKED_BATTLE_COUNT
            and result["decisiveBattles"] == LOCKED_BATTLE_COUNT
            and result["ties"] == 0
            and result["observedPeakActiveBattles"] == LOCKED_CONCURRENCY
            and audit.snapshot()["blockedExternalAttemptCount"] == 0
        )
        result["ok"] = valid
        exit_code = 0 if valid else 1
    except Exception as exc:
        result = {
            "schemaVersion": "fouler-play-local-rehearsal-opponent/v1",
            "ok": False,
            "privateServer": True,
            "errorType": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "networkAudit": audit.snapshot(),
        }
    finally:
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        audit.write()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except (RuntimeError, ValueError) as exc:
        print(f"[local-rehearsal-opponent] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
