#!/usr/bin/env python3
"""Fail-closed tombstone for the retired ffmpeg Twitch server."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] streaming/clean_stream.py is disabled; only the verified OBS director may start output.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
