#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streaming import state_store


def active_battle_count() -> int:
    payload = state_store.read_active_battles()
    battles = payload.get("battles")
    if isinstance(battles, list):
        return len(battles)
    try:
        return max(0, int(payload.get("count") or 0))
    except (TypeError, ValueError):
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    command = list(argv if argv is not None else sys.argv[1:])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("bounded battle session requires a child command", file=sys.stderr)
        return 2

    result = subprocess.run(
        command,
        cwd=str(ROOT),
        env=os.environ.copy(),
        check=False,
    )
    return_code = int(result.returncode)
    if return_code == 0 and active_battle_count() == 0:
        state_store.write_runtime_ready_status(
            summary="Bounded ladder session completed; no active battles remain.",
            mode="bounded_session_complete",
        )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
