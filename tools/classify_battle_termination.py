#!/usr/bin/env python3
"""Classify how every recorded battle actually ENDED, from Showdown protocol truth.

The battle row schema carries seven cryptographic digests and no field describing
the terminal condition, and no turn count. So "roughly half of losses are the
engine churning rather than gameplay" cannot be checked from battle_stats.json at
all. The raw protocol logs under state/fouler/replay_analysis do carry it.

Markers are DISCOVERED here rather than assumed: pass --survey to dump every
distinct |-message|, |inactive| and |player| line shape in the corpus, so the
classifier is built from what Showdown actually emitted, not from what we guessed
it emits.

Terminal classes:
  played        battle reached |win| with no operational marker
  forfeit       "<user> forfeited"
  inactivity    "<user> lost due to inactivity" / timer ran out
  disconnect    "<user> disconnected" / lost connection
  unknown       no |win| line at all (truncated/abandoned artifact)
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

BOT = "DekuFoulerFresh"


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def lines_of(d):
    log = (d or {}).get("log") or ""
    return [l.strip() for l in log.split("\n") if l.strip()]


def bot_slot(lines):
    for l in lines:
        if l.startswith("|player|"):
            p = l.split("|")
            if len(p) >= 4 and BOT.lower() in p[3].lower():
                return p[2]
    return None


def turn_count(lines):
    t = 0
    for l in lines:
        if l.startswith("|turn|"):
            try:
                t = max(t, int(l.split("|")[2]))
            except Exception:
                pass
    return t


def classify(lines):
    """Return (result_for_bot, end_reason, turns)."""
    slot = bot_slot(lines)
    turns = turn_count(lines)

    winner = None
    for l in lines:
        if l.startswith("|win|"):
            winner = l.split("|")[2].strip()
    if winner is None:
        return (None, "unknown", turns, 0, False)

    result = "win" if winner.lower() == BOT.lower() else "loss"

    # TERMINAL CAUSE comes from |-message| ONLY.
    #
    # First attempt at this classified 83/83 losses as operational, which is
    # obviously false. Cause: `|inactive|` lines are TIMER WARNINGS, not terminal
    # conditions — "Battle timer is ON", "<user> has N seconds left" — and they
    # appear in nearly every battle (1,473 occurrences of the bot's own warning
    # across the corpus). Matching /timer/ or /inactive/ over them labels healthy
    # played-out games as forfeits. Only |-message| states why a battle ENDED.
    messages = [l for l in lines if l.startswith("|-message|")]
    reason = "played"
    for l in messages:
        body = l.split("|", 2)[-1].strip()
        low_b = body.lower()
        subject_is_bot = body.lower().startswith(BOT.lower())
        if "lost due to inactivity" in low_b:
            reason = "inactivity_bot" if subject_is_bot else "inactivity_opponent"
            break
        if "forfeit" in low_b:
            reason = "forfeit_bot" if subject_is_bot else "forfeit_opponent"
            break
        if "lost because of their inactivity" in low_b:
            reason = "inactivity_bot" if subject_is_bot else "inactivity_opponent"
            break

    # Timer PRESSURE is a separate signal from timer LOSS: how close the bot came.
    pressure = 0
    for l in lines:
        m = re.match(r"\|inactive\|" + re.escape(BOT) + r" has (\d+) seconds left", l)
        if m:
            pressure = int(m.group(1)) if pressure == 0 else min(pressure, int(m.group(1)))
    disconnected = any("disconnected and has a minute to reconnect" in l and BOT.lower() in l.lower()
                       for l in lines)

    return (result, reason, turns, pressure, disconnected)




def main():
    d = Path(sys.argv[1])
    survey = "--survey" in sys.argv
    files = sorted(d.glob("gen9ou-*.json"))

    if survey:
        shapes = Counter()
        for f in files:
            for l in lines_of(load(f)):
                if l.startswith(("|-message|", "|inactive|", "|inactiveoff|")):
                    # normalize usernames out so shapes collapse
                    s = re.sub(r"\b" + re.escape(BOT) + r"\b", "<BOT>", l)
                    s = re.sub(r"\d+", "<N>", s)
                    shapes[s[:150]] += 1
        print("=== distinct operational message shapes in corpus ===")
        for s, n in shapes.most_common(40):
            print(f"  {n:5d}  {s}")
        return

    by_result = Counter()
    reasons = Counter()
    loss_reasons = Counter()
    turns_by_reason = {}
    short_losses = []

    for f in files:
        lines = lines_of(load(f))
        if not lines:
            continue
        result, reason, turns, pressure, disconnected = classify(lines)
        if result is None:
            reasons["unknown_no_win_line"] += 1
            continue
        by_result[result] += 1
        reasons[reason] += 1
        if disconnected:
            reasons["_bot_disconnect_event"] += 1
        if pressure and pressure <= 30:
            reasons["_bot_low_on_time_<=30s"] += 1
        if result == "loss":
            loss_reasons[reason] += 1
            if pressure and pressure <= 30:
                reasons["_loss_with_bot_low_on_time"] += 1
            turns_by_reason.setdefault(reason, []).append(turns)
            if turns and turns <= 5:
                short_losses.append((f.stem, turns, reason))

    total = sum(by_result.values())
    losses = by_result["loss"]
    print(f"replays classified: {total}  (wins {by_result['win']}, losses {losses})")
    print()
    print("=== LOSS TERMINAL CLASSES ===")
    for r, n in loss_reasons.most_common():
        ts = turns_by_reason.get(r, [])
        med = sorted(ts)[len(ts) // 2] if ts else 0
        pct = 100.0 * n / losses if losses else 0
        print(f"  {r:22s} {n:4d}  ({pct:5.1f}% of losses)  median turns={med}")
    print()
    operational = sum(n for r, n in loss_reasons.items() if r != "played")
    print(f"OPERATIONAL (non-played) losses: {operational}/{losses} = "
          f"{100.0*operational/max(losses,1):.1f}%")
    print()
    print("=== timer-pressure / disconnect signals (corpus-wide) ===")
    for k in ("_bot_disconnect_event", "_bot_low_on_time_<=30s", "_loss_with_bot_low_on_time"):
        print(f"  {k:32s} {reasons.get(k, 0)}")
    print()
    print("=== losses ending at <=5 turns (would look 'operational') ===")
    for s, t, r in short_losses[:15]:
        print(f"  {s}  turns={t}  {r}")
    if not short_losses:
        print("  none")


if __name__ == "__main__":
    main()
