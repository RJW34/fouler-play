#!/usr/bin/env python3
"""Fail-closed tombstone for the retired standalone replay webhook reporter."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] infrastructure/replay_analyzer.py is disabled; use queue-owned analysis reporting.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
