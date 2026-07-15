#!/usr/bin/env python3
"""Fail-closed tombstone for the retired direct Fouler launcher."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] launch.py is disabled; use the receipt-gated leased battle supervisor.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
