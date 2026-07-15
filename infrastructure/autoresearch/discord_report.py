#!/usr/bin/env python3
"""Retired direct Discord reporter.

Fouler reporting is produced as durable events and delivered by the DEKU control
plane. This compatibility path intentionally cannot post or mutate report state.
"""

from __future__ import annotations

import sys


RETIREMENT_MESSAGE = (
    "retired: use infrastructure/event_poster.py to enqueue Fouler events for DEKU"
)


def send_to_discord(_message: str) -> bool:
    """Fail closed for callers that still import the legacy sender."""
    raise RuntimeError(RETIREMENT_MESSAGE)


def main() -> int:
    print(RETIREMENT_MESSAGE, file=sys.stderr)
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
