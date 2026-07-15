#!/usr/bin/env python3
"""Retired standalone Fouler event producer.

Event state now remains in the project-local journal and DEKU observation
outbox. Keeping this tombstone makes stale launchers fail visibly without
emitting test events or competing with the managed producer.
"""

from __future__ import annotations

import sys
from typing import NoReturn


class RetiredEventProducerError(RuntimeError):
    """The standalone producer is no longer an authorized execution path."""


def _retired(*_args: object, **_kwargs: object) -> NoReturn:
    raise RetiredEventProducerError(
        "infrastructure/event-handlers.py is retired; use the local DEKU observation outbox"
    )


class EventHandler:
    """Fail-closed compatibility surface for stale imports."""

    log_event = staticmethod(_retired)
    on_batch_analysis_complete = staticmethod(_retired)
    on_wr_drop = staticmethod(_retired)
    on_process_crash = staticmethod(_retired)
    on_ssh_failure = staticmethod(_retired)
    update_unified_performance = staticmethod(_retired)
    post_to_discord = staticmethod(_retired)


def main() -> int:
    print(
        "retired: standalone Fouler event producer cannot run; DEKU owns observation delivery",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
