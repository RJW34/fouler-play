#!/usr/bin/env python3
"""Fail-closed tombstone for the retired process-spawning Discord monitor."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] bot_monitor.py is disabled; use scripts/devstream_session.py "
        "under the leased battle supervisor.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
