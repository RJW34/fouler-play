#!/usr/bin/env python3
"""
Fouler Play Analysis Poster
Runs team_performance.py, formats the output, posts to Discord #fouler-play-feedback.
Designed to run every N battles or on a schedule (cron or hermes cronjob).
"""
import json
import os
import sys
import subprocess
import requests
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path("/home/ryan/projects/fouler-play")
REPORT_JSON = PROJECT_ROOT / "replay_analysis" / "team_report.json"
LAST_POSTED_FILE = PROJECT_ROOT / ".last_analysis_posted"

# Webhooks from .env
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
FEEDBACK_WEBHOOK = os.getenv("DISCORD_FEEDBACK_WEBHOOK_URL", "")
MAIN_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")

WEBHOOK = FEEDBACK_WEBHOOK or MAIN_WEBHOOK

TEAM_EMOJI = {
    "fat-team-3-dondozo": "🐋",
    "fat-team-2-pivot": "🔄",
    "fat-team-1-stall": "🛡️",
}

TREND_EMOJI = {
    "improving": "📈",
    "declining": "📉",
    "stable": "➡️",
}


def run_analysis():
    """Run team_performance.py and regenerate team_report.json."""
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "replay_analysis" / "team_performance.py"), "--json"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        # Try without --json flag (older version)
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "replay_analysis" / "team_performance.py")],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
    return result.returncode == 0


def should_post(total_battles: int) -> bool:
    """Only post if battle count has grown by at least 10 since last post."""
    if not LAST_POSTED_FILE.exists():
        return True
    try:
        last = int(LAST_POSTED_FILE.read_text().strip())
        return (total_battles - last) >= 10
    except Exception:
        return True


def build_embeds(report: dict) -> list:
    embeds = []

    total = report.get("total_battles", 0)
    generated = report.get("generated_at", "")[:16].replace("T", " ")
    teams = report.get("teams", {})

    # Overall summary
    total_wins = sum(t.get("wins", 0) for t in teams.values())
    total_losses = sum(t.get("losses", 0) for t in teams.values())
    overall_wr = total_wins / (total_wins + total_losses) * 100 if (total_wins + total_losses) > 0 else 0

    # Build description with per-team stats
    desc_lines = [f"**{total} battles total** · {generated}"]
    desc_lines.append(f"Overall: **{total_wins}W/{total_losses}L** ({overall_wr:.0f}% WR)\n")

    for team_name, t in sorted(teams.items(), key=lambda x: -x[1].get("win_rate", 0)):
        emoji = TEAM_EMOJI.get(team_name, "🎮")
        trend = TREND_EMOJI.get(t.get("trend", "stable"), "➡️")
        wr = t.get("win_rate", 0) * 100
        w = t.get("wins", 0)
        l = t.get("losses", 0)
        l10 = t.get("last_10", {})
        l10_wr = l10.get("win_rate", 0) * 100
        l10_w = l10.get("wins", 0)
        l10_g = l10.get("games", 10)

        desc_lines.append(
            f"{emoji} **{team_name}** {trend}\n"
            f"  {w}W/{l}L · {wr:.0f}% WR · Last {l10_g}: {l10_w}W/{l10_g-l10_w}L ({l10_wr:.0f}%)"
        )

    embeds.append({
        "title": "📊 Fouler Play — Team Performance Report",
        "description": "\n".join(desc_lines),
        "color": 0x7289da,
        "footer": {"text": "fouler-play autoresearch · npctypebeat"},
    })

    # Per-team recommendation embeds
    recs = report.get("recommendations", {})
    for team_name, rec_data in recs.items():
        emoji = TEAM_EMOJI.get(team_name, "🎮")
        t = teams.get(team_name, {})
        wr = t.get("win_rate", 0) * 100
        trend = TREND_EMOJI.get(t.get("trend", "stable"), "➡️")

        # Normalise: could be a list or a single dict
        if isinstance(rec_data, dict):
            rec_list = [rec_data]
        elif isinstance(rec_data, list):
            rec_list = rec_data
        else:
            rec_list = []

        fields = []
        for rec in rec_list[:3]:
            weakness = rec.get("weakness") or ""
            detail = rec.get("detail") or ""
            if detail and detail != "No critical weakness identified.":
                label = f"⚠️ {weakness}" if weakness else "⚠️ Issue"
                fields.append({
                    "name": label,
                    "value": detail[:200],
                    "inline": False,
                })

        if fields:
            embeds.append({
                "title": f"{emoji} {team_name} · {wr:.0f}% WR {trend}",
                "fields": fields,
                "color": 0xff4444 if wr < 45 else (0xffaa00 if wr < 55 else 0x44ff44),
            })

    return embeds


def post_to_discord(embeds: list) -> bool:
    if not WEBHOOK:
        print("No webhook configured")
        return False
    try:
        resp = requests.post(
            WEBHOOK,
            json={"embeds": embeds[:10]},
            timeout=15,
        )
        return resp.status_code == 204
    except Exception as e:
        print(f"Discord post failed: {e}")
        return False


def main():
    print(f"[{datetime.now():%H:%M:%S}] Running Fouler Play analysis poster...")

    # Run fresh analysis
    if not run_analysis():
        print("Analysis failed, aborting.")
        sys.exit(1)

    # Load report
    if not REPORT_JSON.exists():
        print(f"No report at {REPORT_JSON}")
        sys.exit(1)

    with open(REPORT_JSON) as f:
        report = json.load(f)

    total = report.get("total_battles", 0)
    print(f"  {total} battles in report.")

    # Check if we should post
    force = "--force" in sys.argv
    if not force and not should_post(total):
        print("  Not enough new battles since last post. Use --force to override.")
        sys.exit(0)

    # Build and send
    embeds = build_embeds(report)
    ok = post_to_discord(embeds)

    if ok:
        LAST_POSTED_FILE.write_text(str(total))
        print(f"  ✅ Posted to Discord ({total} battles, {len(embeds)} embeds)")
    else:
        print("  ❌ Discord post failed")
        sys.exit(1)


if __name__ == "__main__":
    main()


# FOULER-ACTION-ORIENTED-2026-05-20: action-oriented summary helper.
# Reads the latest hypothesis ledger to surface what's open, what shipped
# since last post, and what the measured ELO delta was. Falls back to
# stats-only if the ledger isn't populated.
def build_action_oriented_summary(report_data: dict, hypothesis_dir: str = None) -> str:
    """Compose a Discord-friendly action summary.

    Format:
        Latest batch: {n} battles, WR {pct}%, ELO {current} ({+/-delta} since last).
        Shipped: {hypothesis title} ({status}) — measured delta {x} ELO.
        Open hypotheses: {top issue title} (recommendation: {short})
        Watch: {one concrete next move}
    """
    import json, os
    from pathlib import Path as _P
    hd = hypothesis_dir or os.path.expanduser("~/.hermes/operator/fouler-hypotheses")
    hpath = _P(hd)
    open_hypos = []
    shipped_hypos = []
    if hpath.exists():
        for f in sorted(hpath.glob("*.json"), reverse=True)[:20]:
            try:
                h = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if h.get("status") == "open":
                open_hypos.append(h)
            elif h.get("status") in ("deployed", "measured", "kept"):
                shipped_hypos.append(h)
    lines = []
    teams = report_data.get("teams") or []
    overall_wr = report_data.get("win_rate") or report_data.get("overall_win_rate")
    overall_n = report_data.get("battle_count") or report_data.get("total_battles")
    elo = report_data.get("current_elo") or report_data.get("elo")
    elo_delta = report_data.get("elo_delta") or report_data.get("rating_delta")
    bits = []
    if overall_n is not None:
        bits.append(f"{overall_n} battles")
    if overall_wr is not None:
        bits.append(f"WR {overall_wr:.0%}" if isinstance(overall_wr, float) and overall_wr < 1.0 else f"WR {overall_wr}%")
    if elo is not None:
        e = f"ELO {elo}"
        if elo_delta is not None:
            e += f" ({'+' if elo_delta > 0 else ''}{elo_delta})"
        bits.append(e)
    if bits:
        lines.append("**Latest batch:** " + ", ".join(bits))
    if shipped_hypos:
        for h in shipped_hypos[:2]:
            m = h.get("measurement") or {}
            d = m.get("deltaELO")
            d_str = f" — delta {'+' if d and d > 0 else ''}{d} ELO" if d is not None else ""
            lines.append(f"**Shipped:** {h.get('title')} ({h.get('status')}){d_str}")
    if open_hypos:
        top = open_hypos[0]
        rec = (top.get("recommendation") or "").strip()
        if len(rec) > 120:
            rec = rec[:117].rstrip() + "..."
        lines.append(f"**Open:** {top.get('title')} — {rec}")
    if not shipped_hypos and not open_hypos:
        lines.append("_No hypotheses tracked yet (ledger empty)._ ")
    lines.append("**Next:** see `~/.hermes/operator/fouler-hypotheses/` for the full ledger.")
    return "\n".join(lines)
