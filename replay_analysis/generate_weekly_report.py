#!/usr/bin/env python3
"""Fail-closed tombstone for the retired weekly Discord webhook reporter."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] replay_analysis/generate_weekly_report.py is disabled; use the DEKU event queue.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
