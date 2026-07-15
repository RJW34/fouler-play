#!/usr/bin/env python3
"""Fail-closed tombstone for the retired ffmpeg Twitch control server."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "[RETIRED] streaming/stream_server.py is disabled; use the canonical OBS HTTP service for overlays only.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
