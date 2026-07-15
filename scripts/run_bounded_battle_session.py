#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streaming import state_store  # noqa: E402


RUNTIME_STATE_ROOT = Path(
    os.getenv("FOULER_RUNTIME_STATE_ROOT", str(ROOT))
).expanduser().absolute()
SESSION_PID_FILE = RUNTIME_STATE_ROOT / "pids" / "devstream_battle_session.pid"


def active_battle_count() -> int:
    payload = state_store.read_active_battles()
    battles = payload.get("battles")
    if isinstance(battles, list):
        return len(battles)
    try:
        return max(0, int(payload.get("count") or 0))
    except (TypeError, ValueError):
        return 0


def cleanup_owned_session_pid_file() -> bool:
    """Remove only the session PID claim owned by this wrapper process."""
    try:
        payload = json.loads(SESSION_PID_FILE.read_text(encoding="utf-8"))
        recorded_pid = int(payload.get("pid") or 0)
        command = " ".join(str(part) for part in payload.get("command") or []).lower()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if recorded_pid not in {os.getpid(), os.getppid()}:
        return False
    if "run_bounded_battle_session.py" not in command:
        return False
    try:
        SESSION_PID_FILE.unlink()
    except OSError:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    command = list(argv if argv is not None else sys.argv[1:])
    if command and command[0] == "--":
        command = command[1:]
    try:
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
    finally:
        cleanup_owned_session_pid_file()


if __name__ == "__main__":
    raise SystemExit(main())
