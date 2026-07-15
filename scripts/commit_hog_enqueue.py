#!/usr/bin/env python3
"""Fail-closed tombstone for the retired Fouler-scoped host alert helper."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] commit_hog_enqueue.py is disabled; host alerts belong to global DEKU health.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
