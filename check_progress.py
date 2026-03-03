import json, os, time

stats_path = "battle_stats.json"
active_path = "active_battles.json"

d = json.load(open(stats_path))
b = d["battles"]
wins = sum(1 for x in b if x["result"] == "win")
losses = sum(1 for x in b if x["result"] == "loss")
disconnects = sum(1 for x in b if x.get("result") == "disconnect")

print(f"=== Battle Progress ===")
print(f"Completed: {len(b)}/30 | {wins}W-{losses}L-{disconnects}D")
if wins + losses > 0:
    print(f"Win rate: {wins/(wins+losses)*100:.1f}%")

# Per team
from collections import defaultdict
teams = defaultdict(lambda: {"w": 0, "l": 0})
for battle in b:
    tf = battle.get("team_file", "unknown")
    if battle["result"] == "win":
        teams[tf]["w"] += 1
    else:
        teams[tf]["l"] += 1
print("\nPer team:")
for t, r in sorted(teams.items()):
    total = r["w"] + r["l"]
    wr = r["w"] / total * 100 if total > 0 else 0
    print(f"  {t}: {r['w']}W-{r['l']}L ({wr:.0f}%)")

# Active battles
if os.path.exists(active_path):
    a = json.load(open(active_path))
    print(f"\nActive: {a['count']} battles")
    for battle in a["battles"]:
        print(f"  vs {battle['opponent']} (worker {battle['worker_id']}, started {battle['started'][:19]})")
