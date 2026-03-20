# Git Hygiene

fouler-play should only stay dirty for intentional source work.

Runtime output stays local:
- `.discord_report_state.json`
- `battle_stats.json`
- `data/autoresearch/research_log.jsonl`
- `replay_analysis/team_report.json`
- logs, pid files, and `.out` process captures

Canonical shared tool:

```powershell
C:\Python314\python.exe D:\deku-workspace\scripts\git_hygiene.py status --repo fouler-play
C:\Python314\python.exe D:\deku-workspace\scripts\git_hygiene.py check --repo fouler-play --runtime-only
```

If runtime dirt was accidentally tracked, clean the index first:

```powershell
C:\Python314\python.exe D:\deku-workspace\scripts\git_hygiene.py fix-runtime --repo fouler-play
```

Workflow:
1. Check hygiene before starting a substantial task.
2. Keep hygiene commits separate from behavior or autoresearch changes when practical.
3. Before saying a task is done, pass the runtime-only check.
