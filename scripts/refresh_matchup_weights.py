#!/usr/bin/env python3
"""Scheduled refresher for external runtime matchup weights.

Deterministic, no LLM, no code-gen: scans recent local replays, runs the
mechanics-backed loss pipeline (replay_analysis.loss_learning), and rewrites the
external runtime weights file with observed bad_matchups / problem_pokemon.

Intended to run on a schedule (e.g. every 30 min) so observed losses keep
feeding the live policy bias. Named without an ``update_`` prefix to avoid a
repo-root file watcher that holds exclusive locks on update_*.py files.

Usage:
    python refresh_matchup_weights.py [WINDOW]
"""
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve()
# This file is expected to live at <repo>/scripts/refresh_matchup_weights.py
REPO = ROOT.parent.parent
sys.path.insert(0, str(REPO))

from infrastructure.runtime_paths import resolve_runtime_paths  # noqa: E402

LOG_PATH = resolve_runtime_paths(REPO).log_root / "matchup_weights_refresh.log"
log = logging.getLogger("refresh_matchup_weights")


def configure_logging(log_path: Path = LOG_PATH) -> None:
    """Attach external runtime handlers when the refresher is executed."""
    if any(getattr(handler, "_fouler_matchup_refresh", False) for handler in log.handlers):
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler._fouler_matchup_refresh = True  # type: ignore[attr-defined]
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler._fouler_matchup_refresh = True  # type: ignore[attr-defined]
    log.setLevel(logging.INFO)
    log.addHandler(file_handler)
    log.addHandler(stream_handler)
    log.propagate = False

try:
    from replay_analysis.loss_learning import build_loss_artifact, load_replay
    from replay_analysis.account_identity import resolve_bot_username
    from fp import matchup_memory
except Exception as exc:  # pragma: no cover
    log.error("import failure: %s", exc)
    raise

DEFAULT_WINDOW = 500


def matchup_memory_enabled(env_path: Path | None = None) -> bool:
    """Read the runtime kill switch even when the scheduler did not load .env."""
    raw = os.environ.get("MATCHUP_MEMORY_ENABLED")
    if raw is None:
        path = env_path or (REPO / ".env")
        try:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                if key.strip() == "MATCHUP_MEMORY_ENABLED":
                    raw = value.split("#", 1)[0].strip().strip("\"'")
                    break
        except OSError:
            pass
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def main(window: int = DEFAULT_WINDOW) -> int:
    if not matchup_memory_enabled():
        log.info(
            "MATCHUP_MEMORY_ENABLED is disabled; leaving %s unchanged",
            matchup_memory.WEIGHTS_PATH,
        )
        return 0

    bot = resolve_bot_username()
    replay_dir = resolve_runtime_paths(REPO).state_root / "replay_analysis"
    files = [p for p in replay_dir.glob("gen9*.json") if not p.name.endswith("_gameplan.json")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[:window]

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
            window, infra_losses,
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
    configure_logging()
    requested_window = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WINDOW
    raise SystemExit(main(requested_window))
