#!/usr/bin/env python3
"""Scheduled refresher for fp/matchup_weights.json (ongoing learning loop).

Deterministic, no LLM, no code-gen: scans recent local replays, runs the
mechanics-backed loss pipeline (replay_analysis.loss_learning), and rewrites
fp/matchup_weights.json with observed bad_matchups / problem_pokemon.

Intended to run on a schedule (e.g. every 30 min) so observed losses keep
feeding the live policy bias. Named without an ``update_`` prefix to avoid a
repo-root file watcher that holds exclusive locks on update_*.py files.

Usage:
    python refresh_matchup_weights.py [WINDOW]
"""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve()
# This file is expected to live at <repo>/scripts/refresh_matchup_weights.py
REPO = ROOT.parent.parent
sys.path.insert(0, str(REPO))

LOG_PATH = REPO / "logs" / "matchup_weights_refresh.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("refresh_matchup_weights")

try:
    from replay_analysis.loss_learning import build_loss_artifact, load_replay
    from replay_analysis.account_identity import resolve_bot_username
    from fp import matchup_memory
except Exception as exc:  # pragma: no cover
    log.error("import failure: %s", exc)
    raise

WINDOW = int(sys.argv[1]) if len(sys.argv) > 1 else 500


def main() -> int:
    bot = resolve_bot_username()
    replay_dir = REPO / "replay_analysis"
    files = [p for p in replay_dir.glob("gen9*.json") if not p.name.endswith("_gameplan.json")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[:WINDOW]

    artifacts = []
    losses = 0
    infra_losses = 0
    for p in files:
        try:
            art = build_loss_artifact(load_replay(p), bot_username=bot)
        except Exception as exc:
            log.debug("skip %s: %s", p.name, exc)
            continue
        # EXCLUDE infra-losses (inactivity/timeout/disconnect/forfeit/crash) from
        # the matchup-bias corpus: a game lost to the CLOCK from a winning
        # position must not bias the live policy away from that opponent species.
        # Only real played-out results inform matchup memory.
        if art.get("is_infra_loss"):
            infra_losses += 1
            continue
        artifacts.append(art)
        if art.get("result") == "loss":
            losses += 1

    if not artifacts:
        log.warning(
            "no piloting artifacts in window=%d (infra_losses_excluded=%d); leaving weights unchanged",
            WINDOW, infra_losses,
        )
        return 0

    log.info("infra-losses excluded from matchup corpus: %d", infra_losses)
    weights = matchup_memory.update_weights_from_artifacts(artifacts)
    n_flagged = sum(
        1 for sid in set(list(weights["bad_matchups"]) + list(weights["problem_pokemon"]))
        if matchup_memory.opponent_is_flagged(sid, weights)
    )
    matchup_memory.write_weights(weights)
    log.info(
        "refreshed: replays=%d losses=%d bad_matchups=%d problem_pokemon=%d flagged_live=%d -> %s",
        len(artifacts), losses, len(weights["bad_matchups"]),
        len(weights["problem_pokemon"]), n_flagged, matchup_memory.WEIGHTS_PATH,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
