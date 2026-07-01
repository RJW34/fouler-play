# fouler-play Go-Live Rectification Scaffold - 2026-07-01

Status: NO-GO.

This file bookmarks the repair plan for the competitive Showdown lane after
the 2026-07-01 root-cause audit. It is scaffolding only. It does not authorize
ladder starts, Discord posts, scheduled-task changes, team edits, credential
handling, or Twitch actions.

## Current Gate

Latest local start-gate command:

```powershell
py -3 scripts\fouler_mission_monitor.py --write --start-gate-only --no-refresh-health
```

Result: blocked. The lane is not failing because a runner is absent; it is
failing because the mission evidence does not prove safe improvement toward a
1700 sustain contract.

Known blockers from the audit:

- stale health and rating truth
- current rating around the 1100s, not near the 1700 sustain target
- large drawdowns and stop-loss breach history
- missing accepted offline-eval resume proof
- missing post-packet improvement proof
- stale or invalid `latest-elo-proof.json`

## Acceptance Contract

fouler-play can be promoted only when:

- `scripts/fouler_mission_monitor.py --start-gate-only` returns
  `decision=allow-next-proof-window`
- the account, runtime lease, and process truth identify exactly one runner
- the active account matches the current mission contract, not stale examples
- no team files are edited or redesigned
- stop-loss recovery has accepted offline eval proof plus post-packet
  improvement proof
- `latest-elo-proof.json` proves an uninterrupted 1700 floor and at least 30
  post-target rated games, including at least 10 games per fixed team

## Rectification Packets

| Packet | Purpose | Entry Gate | Exit Proof |
| --- | --- | --- | --- |
| `fouler-r1-eval-ledger-cleanup` | Retire or archive stale dead offline-eval status only under a fresh finite lease. | current HERMES proof-window lease naming this cleanup | offline-eval doctor no longer reports a dead stale status marker |
| `fouler-r2-frozen-baseline` | Build a 200-battle frozen baseline against the local Pokemon Showdown eval server. | Showdown checkout provisioned, eval server reachable | `eval_results/offline/frozen.json` has at least `IMPROVE_AGENT_EVAL_BATTLES` battles |
| `fouler-r3-candidate-compare` | Run candidate eval and compare it to frozen. | frozen baseline green | `candidate.json` plus `compare-frozen-vs-candidate.json` accepted and fresh |
| `fouler-r4-post-packet-proof` | Apply one bounded improvement packet and prove it improved after a small battle window. | candidate compare accepted | `devstream/truth/post-packet-eval.json` reports improving evidence integrity |
| `fouler-r5-staged-ladder-proof` | Reopen bounded ladder windows in stages: 1500, 1600, 1700, then sustain. | start gate allows next proof window | fresh chronological ELO proof with no unmanaged skid and no post-1700 floor break |

## Verification Commands

Use these as the repo-local gate before asking HERMES for project promotion:

```powershell
py -3 infrastructure\offline_eval_readiness.py --require-ready
py -3 scripts\fouler_mission_monitor.py --write --start-gate-only --no-refresh-health
py -3 scripts\devstream_cycle_report.py --write
py -3 -m pytest -q -p no:cacheprovider tests\test_offline_eval_readiness.py tests\test_fouler_mission_monitor.py tests\test_devstream_cycle_report.py tests\test_devstream_post_packet_eval.py
```

## Handoff Rule

If a later agent cannot produce accepted offline eval, post-packet improvement,
and chronological 1700 sustain proof, this lane stays NO-GO even if Showdown is
reachable or a bot process is running.
