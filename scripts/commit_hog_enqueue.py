#!/usr/bin/env python3
"""Queue a commit-hog ops_alert Discord event for the commit-hog watchdog.

Called by scripts/commit_hog_watchdog.ps1 (task HERMES-CommitHogWatchdog).
Inputs arrive via environment variables so PowerShell never has to quote or
escape the payload or any python source:

  FOULER_REPO               repo root, prepended to sys.path
                            (defaults to this file's parent's parent)
  FOULER_OPS_ALERT_CONTENT  the alert message content (required, non-empty)

Prints the queued event id (or "deduped" when the queue's 1-hour dedup window
rejects an identical message) as the LAST stdout line -- the watchdog reads
exactly that line. Exits non-zero on failure.

This file exists because `python -c` one-liners are fragile under PowerShell
5.1 native-argument quoting: the original multi-line -c payload was mangled
into a SyntaxError ('File "<string>", line 4', first watchdog run
2026-07-04T09:37:31Z). A checked-in helper is immune to shell quoting and
testable (tests/test_commit_hog_enqueue.py).
"""

import os
import sys


def main() -> int:
    repo = os.environ.get("FOULER_REPO") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    if repo not in sys.path:
        sys.path.insert(0, repo)

    content = (os.environ.get("FOULER_OPS_ALERT_CONTENT") or "").strip()
    if not content:
        print("FOULER_OPS_ALERT_CONTENT is empty; nothing to queue", file=sys.stderr)
        return 2

    from infrastructure.event_queue_lib import queue_event

    event_id = queue_event("ops_alert", "project", content, dedup_window_sec=3600)
    print(event_id or "deduped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
