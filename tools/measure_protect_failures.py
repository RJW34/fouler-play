#!/usr/bin/env python3
"""Measure baseline rate of failed consecutive Protect-family attempts.

Scans every replay JSON in the runtime replay dir, reconstructs the bot slot
from |player| lines, and counts turns where the bot used a Protect-family move
on consecutive turns and got |-fail|.

This is the DISCRIMINATING METRIC for the consecutive-Protect hypothesis: a
mechanical event count, not an ELO proxy.
"""
import json
import sys
from pathlib import Path

PROTECT_FAMILY = {
    "protect", "detect", "kingsshield", "spikyshield", "banefulbunker",
    "obstruct", "silktrap", "burningbulwark", "maxguard",
}
BOT = "DekuFoulerFresh"


def norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def scan(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    log = d.get("log", "")
    if not log:
        return None
    lines = [l.strip() for l in log.split("\n") if l.strip()]

    slot = None
    for l in lines:
        if l.startswith("|player|"):
            p = l.split("|")
            if len(p) >= 4 and BOT.lower() in p[3].lower():
                slot = p[2]
    if not slot:
        return None

    result = None
    for l in lines:
        if l.startswith("|win|"):
            winner = l.split("|")[2].strip()
            result = "win" if winner.lower() == BOT.lower() else "loss"

    turn = 0
    prev_move = ""
    pending = None
    fails = []
    successes_consecutive = 0
    for l in lines:
        if l.startswith("|turn|"):
            pending = None
            try:
                turn = int(l.split("|")[2])
            except Exception:
                pass
            continue
        if l.startswith(("|switch|", "|drag|")):
            p = l.split("|")
            if len(p) >= 3 and p[2].startswith(f"{slot}a:"):
                prev_move = ""
                successes_consecutive = 0
                pending = None
            continue
        if l.startswith("|move|"):
            p = l.split("|")
            if len(p) < 4 or not p[2].startswith(f"{slot}a:"):
                continue
            mv = norm(p[3])
            if mv in PROTECT_FAMILY and prev_move in PROTECT_FAMILY:
                pending = (turn, p[3].strip(), p[2].split(":", 1)[-1].strip(),
                           successes_consecutive)
            else:
                pending = None
            prev_move = mv
            continue
        if l.startswith(f"|-singleturn|{slot}a:") and "Protect" in l:
            successes_consecutive += 1
            pending = None
            continue
        if pending and l.startswith(f"|-fail|{slot}a:"):
            fails.append(pending)
            successes_consecutive = 0
            pending = None
    return {"result": result, "fails": fails, "n_turns": turn}


def main():
    d = Path(sys.argv[1])
    files = sorted(d.glob("gen9ou-*.json"))
    total = 0
    battles_with_fail = 0
    fail_events = 0
    losses = 0
    loss_with_fail = 0
    detail = []
    for f in files:
        r = scan(f)
        if not r:
            continue
        total += 1
        if r["result"] == "loss":
            losses += 1
        if r["fails"]:
            battles_with_fail += 1
            fail_events += len(r["fails"])
            if r["result"] == "loss":
                loss_with_fail += 1
            for t, mv, mon, nsucc in r["fails"]:
                detail.append(f"  {f.stem} [{r['result']}] turn {t}: {mv} by {mon} FAILED "
                              f"(after {nsucc} consecutive successes)")
    print(f"replays scanned (bot identified): {total}")
    print(f"  losses: {losses}")
    print(f"battles with >=1 failed consecutive Protect: {battles_with_fail}")
    print(f"total failed-consecutive-Protect events:     {fail_events}")
    print(f"  of which in losses: {loss_with_fail}")
    if total:
        print(f"RATE: {fail_events} events / {total} battles = "
              f"{100.0*fail_events/total:.2f} per 100 battles")
    print("\nEVENTS:")
    for line in detail:
        print(line)


if __name__ == "__main__":
    main()
