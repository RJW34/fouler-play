#!/usr/bin/env python3
"""Fail-closed tombstone for the retired standalone analysis webhook poster."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] replay_analysis/analysis_poster.py is disabled; use the DEKU event queue.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
