#!/usr/bin/env python3
"""
sync_loss_evidence.py -- pull fresh loss EVIDENCE (replay JSON + decision traces)
from the live JIGGLY runtime to ubunztu so the learn-loop can actually mine it.

WHY THIS EXISTS (the keystone fix)
----------------------------------
The fouler learn-loop on ubunztu (autoresearch -> improve_loop) decides what to
fix by reading, for each recorded LOSS in battle_stats.json:
  * its Showdown replay JSON   at  replay_analysis/<replay_id>.json
  * its per-turn decision traces at logs/decision_traces/<tag>_turn*.json

Those two artifacts are the ONLY evidence autoresearch accepts (everything else
is rejected as an unsupported claim). They are produced live ON JIGGLY by run.py
(_save_replay_json_locally + write_decision_trace). But the JIGGLY->ubunztu
data-sync only ships battle_stats.json -- and .gitignore excludes both
`logs/` and `replay_analysis/gen*.json`, so the evidence NEVER reaches ubunztu.

Net effect before this tool: autoresearch on ubunztu sees N recent losses but 0
replay JSONs and 0 fresh traces -> 0 evidence-backed issues -> top_issue=null ->
improve_loop records `skipped_no_issue` forever. The loop is correct and safe,
but STARVED. This tool feeds it.

WHAT IT DOES
------------
1. Reads the LOCAL battle_stats.json, collects the replay_ids of recent LOSSES
   (default: last --window battles).
2. For each loss whose evidence is missing locally, copies from JIGGLY:
     - replay_analysis/<rid>.json
     - logs/decision_traces/<tag>_turn*.json
   via `scp` over the standard Ryanj@JIGGLY path (NOT MIRAIDON).
3. Skips anything already present locally (idempotent, cheap).
4. Prints a truthful summary: losses in window, evidence already local, fetched,
   still-missing (e.g. replay expired on PS before save).

It is READ-ONLY on JIGGLY (scp pull only) and NEVER restarts anything. It does
NOT touch battle_stats.json, the emulators, OBS, or any live service.

CAPACITY
--------
Decision traces are tiny JSON; a 30-battle window is a few hundred KB. The tool
caps total bytes fetched (--max-mb, default 25) and total files (--max-files) so
it can never blow ubunztu's tight inode/disk budget. It logs the byte count.

Usage (on ubunztu):
  python infrastructure/sync_loss_evidence.py --window 30
  python infrastructure/sync_loss_evidence.py --window 30 --dry-run
  python infrastructure/sync_loss_evidence.py --window 50 --jiggly-repo 'D:/Projects/fouler-play'
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BATTLE_STATS = PROJECT_ROOT / "battle_stats.json"
REPLAY_DIR = PROJECT_ROOT / "replay_analysis"
TRACE_DIR = PROJECT_ROOT / "logs" / "decision_traces"
STATUS_PATH = PROJECT_ROOT / "devstream" / "truth" / "sync-evidence-status.json"
STATUS_SCHEMA_VERSION = "fouler-play-sync-evidence-status/v1"

JIGGLY_HOST = "Ryanj@192.168.1.126"
DEFAULT_JIGGLY_REPO = "D:/Projects/fouler-play"


def _normalize_replay_id(replay_id: str | None) -> str:
    if not replay_id:
        return ""
    rid = replay_id.replace("battle-", "", 1)
    return rid.removesuffix(".json")


def _battle_tag(battle: dict) -> str:
    """The on-disk trace tag is the FULL battle id incl. the room hash, prefixed
    'battle-' (that is how write_decision_trace names files)."""
    bid = battle.get("battle_id") or battle.get("replay_id") or ""
    if bid and not bid.startswith("battle-"):
        bid = "battle-" + bid
    return bid


def load_losses(window: int) -> list[dict]:
    data = json.loads(BATTLE_STATS.read_text(encoding="utf-8"))
    battles = data.get("battles", data) if isinstance(data, dict) else data
    recent = battles[-window:] if len(battles) > window else battles
    return [b for b in recent if b.get("result") == "loss"]


def _ssh_list_traces(jiggly_repo: str, tag: str) -> list[str]:
    """List trace filenames for a battle tag on JIGGLY (cmd dir, robust to spaces)."""
    win_dir = f"{jiggly_repo}/logs/decision_traces".replace("/", "\\")
    # Use cmd.exe dir /b so we don't depend on PowerShell encoding quirks.
    remote = f'cmd /c dir /b "{win_dir}\\{tag}_turn*.json"'
    cp = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", JIGGLY_HOST, remote],
        capture_output=True, text=True, timeout=40,
    )
    if cp.returncode != 0:
        return []
    return [ln.strip() for ln in cp.stdout.splitlines()
            if ln.strip().lower().endswith(".json")]


def _emit_status_json(
    status_path: Path,
    *,
    window: int,
    dry_run: bool,
    loss_count: int,
    have_replay: int,
    fetched_replay: int,
    have_trace: int,
    fetched_trace: int,
    missing_remote: list[str],
    budget_capped: list[str],
    fetched_bytes: int,
    fetched_files: int,
) -> None:
    """Write a machine-readable evidence-sync status alongside the stdout summary.

    Without this artifact, the only signal that the learn-loop is STARVED (every
    loss has 0 evidence) is buried in a stdout log line or surfaces hours later
    via autoresearch. The cycle-report / Discord status / cron can read this
    file to detect starvation immediately and surface it as a top-level blocker.
    """
    replay_budget_capped = sum(1 for x in budget_capped if x.startswith("replay:"))
    miss = loss_count - have_replay - fetched_replay - replay_budget_capped if loss_count else 0
    # Starvation = we have *no* evidence (replays OR traces) for *any* loss in
    # the window. Either bucket counts: traces alone are enough to back a
    # decision-instability finding.
    evidenced = (have_replay + fetched_replay) + (have_trace + fetched_trace)
    starved = loss_count > 0 and evidenced == 0
    starved_reason = None
    if starved:
        if budget_capped and not missing_remote:
            starved_reason = "all evidence budget-capped; raise --max-mb / --max-files"
        elif missing_remote and not budget_capped:
            starved_reason = "no evidence on JIGGLY for any loss; investigate JIGGLY writer"
        elif budget_capped and missing_remote:
            starved_reason = "mixed: some budget-capped, some missing on JIGGLY"
        else:
            starved_reason = "no evidence fetched and none queued; check window/sources"

    payload = {
        "schemaVersion": STATUS_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "window": window,
        "dryRun": dry_run,
        "lossCount": loss_count,
        "replay": {
            "local": have_replay,
            "fetched": fetched_replay,
            "missingRemote": sum(1 for x in missing_remote if x.startswith("replay:")),
            "budgetCapped": replay_budget_capped,
        },
        "trace": {
            "local": have_trace,
            "fetched": fetched_trace,
            "missingRemote": sum(1 for x in missing_remote if x.startswith("traces:")),
            "budgetCapped": sum(1 for x in budget_capped if x.startswith("traces:")),
        },
        "bytesFetched": fetched_bytes,
        "filesFetched": fetched_files,
        "missingRemoteSample": missing_remote[:8],
        "budgetCappedSample": budget_capped[:8],
        "miss": miss,
        "starved": starved,
        "starvedReason": starved_reason,
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _scp_pull(jiggly_repo: str, remote_rel: str, local_path: Path) -> int:
    """scp a single file from JIGGLY; return bytes fetched (0 on failure)."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    remote = f'{JIGGLY_HOST}:"{jiggly_repo}/{remote_rel}"'
    cp = subprocess.run(
        ["scp", "-o", "ConnectTimeout=10", "-q", remote, str(local_path)],
        capture_output=True, text=True, timeout=60,
    )
    if cp.returncode == 0 and local_path.exists():
        return local_path.stat().st_size
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="pull fouler loss evidence JIGGLY->ubunztu")
    ap.add_argument("--window", type=int, default=30,
                    help="How many recent battles to consider (matches autoresearch -n).")
    ap.add_argument("--jiggly-repo", default=DEFAULT_JIGGLY_REPO,
                    help="fouler-play repo path ON JIGGLY (forward slashes ok).")
    ap.add_argument("--max-mb", type=float, default=25.0,
                    help="Hard cap on total MB fetched (capacity guard).")
    ap.add_argument("--max-files", type=int, default=2000,
                    help="Hard cap on total files fetched (inode guard).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what WOULD be fetched; copy nothing.")
    ap.add_argument("--status-path", default=None,
                    help="Where to write the machine-readable status JSON; "
                         "consumed by cycle-report / Discord status. "
                         "Defaults to devstream/truth/sync-evidence-status.json. "
                         "Pass empty string to disable.")
    args = ap.parse_args()
    # Resolve default at call time so tests can monkeypatch STATUS_PATH.
    status_path_arg = args.status_path
    if status_path_arg is None:
        status_path_arg = str(STATUS_PATH)

    if not BATTLE_STATS.exists():
        print(f"[evidence-sync] no battle_stats.json at {BATTLE_STATS}", file=sys.stderr)
        return 2

    losses = load_losses(args.window)
    print(f"[evidence-sync] window={args.window}  losses={len(losses)}  "
          f"jiggly={args.jiggly_repo}  dry_run={args.dry_run}")

    budget_bytes = int(args.max_mb * 1024 * 1024)
    fetched_bytes = 0
    fetched_files = 0
    have_replay = need_replay = fetched_replay = 0
    have_trace = fetched_trace = 0
    # FOULER-SYNC-EVIDENCE-BUDGET-CAPPED-TRUTH-2026-06-03: split the "we didn't
    # land this evidence" bucket into TWO distinct causes so the SUMMARY does
    # not silently roll budget-capped skips into "still missing on JIGGLY".
    # Pre-fix, `miss = need_replay - fetched_replay` counted budget-capped
    # replays the same as scp-failed replays, but missing_remote only captured
    # the latter -- so "miss=5, still missing on JIGGLY: <empty>" lied by
    # omission. The two causes have OPPOSITE next steps (raise --max-mb /
    # --max-files vs. investigate JIGGLY-side writer), so they MUST be
    # surfaced distinctly for the learn-loop operator (or DEKU) to act.
    missing_remote: list[str] = []
    budget_capped: list[str] = []

    for b in losses:
        rid = _normalize_replay_id(b.get("replay_id") or b.get("battle_id"))
        tag = _battle_tag(b)
        if not rid:
            continue

        # --- replay JSON ---
        local_replay = REPLAY_DIR / f"{rid}.json"
        if local_replay.exists():
            have_replay += 1
        else:
            need_replay += 1
            if args.dry_run:
                print(f"[evidence-sync] WOULD fetch replay {rid}.json")
            elif fetched_bytes < budget_bytes and fetched_files < args.max_files:
                n = _scp_pull(args.jiggly_repo, f"replay_analysis/{rid}.json", local_replay)
                if n:
                    fetched_bytes += n; fetched_files += 1; fetched_replay += 1
                    print(f"[evidence-sync] +replay {rid}.json ({n}B)")
                else:
                    missing_remote.append(f"replay:{rid}")
            else:
                budget_capped.append(f"replay:{rid}")

        # --- decision traces ---
        local_traces = list(TRACE_DIR.glob(f"{tag}_turn*.json")) if tag else []
        if local_traces:
            have_trace += 1
            continue
        if not tag:
            continue
        if args.dry_run:
            print(f"[evidence-sync] WOULD fetch traces for {tag}")
            continue
        # If we are already at the cap before we even ask JIGGLY which traces
        # exist, that whole battle's traces are budget-capped -- record it
        # without burning an SSH roundtrip.
        if fetched_bytes >= budget_bytes or fetched_files >= args.max_files:
            budget_capped.append(f"traces:{tag}")
            continue
        names = _ssh_list_traces(args.jiggly_repo, tag)
        if not names:
            missing_remote.append(f"traces:{tag}")
            continue
        got_any = False
        capped_mid_battle = False
        for name in names:
            if fetched_bytes >= budget_bytes or fetched_files >= args.max_files:
                print("[evidence-sync] capacity cap reached; stopping.")
                capped_mid_battle = True
                break
            n = _scp_pull(args.jiggly_repo, f"logs/decision_traces/{name}",
                          TRACE_DIR / name)
            if n:
                fetched_bytes += n; fetched_files += 1; got_any = True
        if got_any:
            fetched_trace += 1
            print(f"[evidence-sync] +traces {tag} ({len(names)} turns)")
        if capped_mid_battle and not got_any:
            budget_capped.append(f"traces:{tag}")

    # miss counts ONLY scp-failed replays (i.e. genuinely-not-on-JIGGLY); the
    # budget-capped count is reported on its own so the two next-actions don't
    # blur. need_replay - fetched_replay - len(replay budget-capped) = miss.
    replay_budget_capped = sum(1 for x in budget_capped if x.startswith("replay:"))
    miss = need_replay - fetched_replay - replay_budget_capped
    print("[evidence-sync] SUMMARY: "
          f"replay(local={have_replay} fetched={fetched_replay} miss={miss}) "
          f"traces(local={have_trace} fetched={fetched_trace}) "
          f"bytes={fetched_bytes} files={fetched_files} "
          f"budget_capped={len(budget_capped)}")
    if missing_remote:
        print(f"[evidence-sync] still missing on JIGGLY ({len(missing_remote)}): "
              f"{', '.join(missing_remote[:8])}"
              + (" ..." if len(missing_remote) > 8 else ""))
        print("[evidence-sync] (expired-on-PS replays are expected; traces should "
              "exist for any battle the live runtime actually played).")
    if budget_capped:
        print(f"[evidence-sync] budget-capped, not fetched ({len(budget_capped)}): "
              f"{', '.join(budget_capped[:8])}"
              + (" ..." if len(budget_capped) > 8 else ""))
        print("[evidence-sync] (raise --max-mb / --max-files to land these; they "
              "exist on JIGGLY but we chose not to pull them this run).")

    if status_path_arg:
        _emit_status_json(
            Path(status_path_arg),
            window=args.window,
            dry_run=args.dry_run,
            loss_count=len(losses),
            have_replay=have_replay,
            fetched_replay=fetched_replay,
            have_trace=have_trace,
            fetched_trace=fetched_trace,
            missing_remote=missing_remote,
            budget_capped=budget_capped,
            fetched_bytes=fetched_bytes,
            fetched_files=fetched_files,
        )
        print(f"[evidence-sync] status -> {status_path_arg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
