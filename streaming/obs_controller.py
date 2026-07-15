#!/usr/bin/env python3
"""Fail-closed tombstone for the retired unauthenticated OBS output controller."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] streaming/obs_controller.py is disabled; OBS output is operator-gated.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
