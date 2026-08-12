"""Search-path dispatcher. The legacy 8,200-line pipeline was retired 2026-08-01
(owner directive) after the clean path validated live: canary batch 19-11 (63%),
median decision 3.28s vs legacy 5.7s, zero pool failures/tracebacks. The legacy
module is archived as fp/search/main.py.attic-20260801; its salvageable pieces
live on per the P-3 salvage manifest (BACKLOG.md). FOULER_SEARCH_PATH is kept
for future path experiments; any value other than 'clean' now logs once and
routes clean anyway."""
import logging
import os

logger = logging.getLogger(__name__)
_warned = False


def find_best_move(battle):
    global _warned
    flag = os.getenv("FOULER_SEARCH_PATH", "clean").strip().lower()
    if flag != "clean" and not _warned:
        logger.warning(
            "FOULER_SEARCH_PATH=%r requested but the legacy path is retired; using clean.",
            flag,
        )
        _warned = True
    from fp.search.clean_path import find_best_move as _f
    return _f(battle)
