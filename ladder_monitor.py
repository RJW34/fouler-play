"""Compact ladder status snapshot. Scans BOTH the INFO stdout capture
(ladder_child.log) AND run.py's DEBUG file log (logs/init.log*), because the raw
Showdown protocol lines that reveal an inactivity/clock forfeit
('<user> lost due to inactivity.') are DEBUG-only and never reach stdout.
Read-only. The Showdown ladder API is the authority on completed-game COUNT;
this reports the FORFEIT + reconnect signals the API cannot show."""
import glob
import re
from pathlib import Path

ROOT = Path(r"D:\Projects\fouler-play")
LOGDIR = ROOT / "logs"
USER = "dekufoulerlab"

paths = [LOGDIR / "ladder_child.log"] + [Path(p) for p in glob.glob(str(LOGDIR / "init.log*"))]
text = ""
for p in paths:
    try:
        text += p.read_text(encoding="utf-8", errors="replace") + "\n"
    except Exception:
        pass

sup = ""
try:
    sup = (LOGDIR / "ladder_supervisor.log").read_text(encoding="utf-8", errors="replace")
except Exception:
    pass

# OUR inactivity/clock forfeits -- the metric that MUST be zero.
our_inactivity = re.findall(rf"{USER} lost due to inactivity", text, re.IGNORECASE)
# Opponent forfeits (informational -- these are wins/aids for us, not our fault).
opp_forfeit = re.findall(r"\|-message\|(?!" + USER + r")\w+ forfeited", text, re.IGNORECASE)
generic_forfeit_us = re.findall(rf"{USER} forfeited", text, re.IGNORECASE)

# Reconnect-survival signals.
reconnect_ok = re.findall(r"Auto-reconnect succeeded.*", text)
reconnect_try = re.findall(r"Auto-reconnect attempt.*", text)
conn_closed = re.findall(r"ConnectionClosed", text)

restarts = len(re.findall(r"launching child \(restart #", sup))
finished = re.findall(r"battle_finished \(winner=([^)]*)\)", text)

print(f"our_inactivity_forfeits={len(our_inactivity)}  our_manual_forfeits={len(generic_forfeit_us)}  opp_forfeits={len(opp_forfeit)}")
print(f"battle_finished_events={len(finished)}  reconnect_succeeded={len(reconnect_ok)}  reconnect_attempts={len(reconnect_try)}  conn_closed={len(conn_closed)}")
print(f"supervisor_child_launches={restarts}")
for line in reconnect_ok[-3:]:
    print("  RECONNECT>", line.strip()[:150])
