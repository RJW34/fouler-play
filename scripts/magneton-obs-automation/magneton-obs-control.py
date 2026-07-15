#!/usr/bin/env python3
"""Retired MAGNETON OBS controller.

MAGNETON is not a venture runtime node. Production OBS presentation is managed on
JIGGLYPUFF, and repository code must not start recording or streaming outputs.
"""

from __future__ import annotations

import sys


RETIREMENT_MESSAGE = (
    "retired: MAGNETON OBS automation cannot control recording or stream output"
)


def main() -> int:
    print(RETIREMENT_MESSAGE, file=sys.stderr)
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
