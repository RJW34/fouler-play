#!/usr/bin/env python3
"""Run Fouler through run.py with offline-eval-only battle stats storage.

The normal entry point writes to the live root battle_stats.json. Offline eval
uses the same run.py decision path, but its synthetic local battles must stay out
of live ladder evidence and autoresearch inputs.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "eval_results" / "offline"


def _strip_run_py_sentinel(argv: list[str]) -> list[str]:
    if argv and Path(argv[0]).name.lower() == "run.py":
        return argv[1:]
    return argv


def offline_battle_stats_path(
    *,
    root: Path = PROJECT_ROOT,
    env: Mapping[str, str] | None = None,
) -> Path:
    env = os.environ if env is None else env
    raw = str(env.get("FOULER_OFFLINE_BATTLE_STATS_FILE") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        return path
    label = str(env.get("FOULER_OFFLINE_EVAL_LABEL") or "eval").strip() or "eval"
    return root / "eval_results" / "offline" / f"{label}-battle_stats.json"


def configure_run_module(
    run_module: ModuleType,
    argv: list[str],
    *,
    root: Path = PROJECT_ROOT,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, list[str]]:
    stats_path = offline_battle_stats_path(root=root, env=env)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    run_module.BATTLE_STATS_FILE = stats_path
    return stats_path, ["run.py", *_strip_run_py_sentinel(argv)]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    os.environ.setdefault("FOULER_OFFLINE_EVAL", "1")
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    import run as run_module

    stats_path, run_argv = configure_run_module(run_module, argv)
    sys.argv = run_argv
    print(f"[offline-eval-runner] battle stats redirected to {stats_path}", file=sys.stderr)

    try:
        from process_lock import acquire_lock

        if not acquire_lock(username=getattr(run_module.FoulPlayConfig, "username", "unknown")):
            run_module.logger.error("Another bot instance is already running. Exiting.")
            return 1
    except ImportError as exc:
        run_module.logger.warning("Process lock unavailable (%s); continuing without singleton lock.", exc)
    except Exception as exc:
        run_module.logger.warning("Process lock failed (%s); continuing without singleton lock.", exc)

    try:
        asyncio.run(run_module.run_foul_play())
    except Exception:
        run_module.logger.error(traceback.format_exc())
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
