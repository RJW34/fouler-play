# Claude runtime ops - 2026-06-10

- Ladder bot runs via task Claude-FoulerPlayer (AtStartup + every 15 min,
  IgnoreNew, NO execution time limit) -> scripts/fouler_clean_supervisor.ps1
  (guarantees exactly one .venv run.py on npctypebeat).
- OBS battle-spectate slot server (:8777, feeds the SHOWDOWN_MATCH_* browser
  sources in the CrossProjectDevstream scene collection) runs via task
  Claude-FoulerOBSHelper -> python -m streaming.serve_obs_page.
- Deployed branch on this box: fix/atomic-singleton-lock-20260604. The
  lease/fail-closed hardening (opus48/multisample-mcts tip) is NOT deployed
  here by design - it forbids autostart.
