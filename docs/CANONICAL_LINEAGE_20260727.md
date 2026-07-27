# Fouler canonical lineage

This file is the repository-level source-of-truth record for the 2026-07-27
consolidation. It distinguishes source history from deployable code and prevents
an old worktree, launcher, or branch name from being mistaken for the live
runtime.

## Canonical contract

- Canonical source branch: `codex/fouler-canonical-season-20260727`
- Promotion target: `origin/master`. Source promotion follows the full suite and
  lint gate; live promotion separately requires immutable-release admission and
  live rollback gates.
- Deployable source: a clean, pushed commit from the canonical branch. A mutable
  checkout is never a release.
- Live source: the exact commit and tree recorded by the installed release
  manifest and finite-season authority. A branch name, directory name, or prose
  status file is not proof of what is running.
- Runtime data: external state rooted on `E:` and named by the finite-season
  authority. `battle_stats.json`, logs, caches, replay state, and credentials are
  not release content.
- Runtime owner: `DEVSTREAM-JIG-FoulerSeasonSupervisor`. Legacy Fouler tasks are
  predecessors to be exported and disabled at cutover, not alternate owners.
- Public output: off. Nothing in this repository authorizes Start Streaming.

## Selected tree

The selected base is `b46880f52078`, the safety-rich protected-learn-loop line.
The canonical line then adds:

- `5307af06ef10`: immutable finite-season runtime authority, exact release and
  account binding, bounded continuation, external mutable state, atomic result
  persistence, and reversible Windows task installation.
- `a51a511f`: sharpened static evaluation and the KO guard from the otherwise
  unrelated learn-and-climb history, integrated after the existing
  matchup-memory bias.
- `7427d57b`: flatness-gated MCTS blend, dense decision-regret tooling,
  AST-anchored whole-function tooling, migrations, offline monitor, and tests.
  The older direct shared-checkout mutation path was not selected; the current
  isolated candidate-worktree transaction remains authoritative.
- `9ce0a076`: explicit done predicate, scoring, and loss-hypothesis burndown.
- `2137fc45`: battle-termination classifier.

Exact commit IDs above are historical integration points. The deployable
identity is always the later canonical head recorded in the release manifest.

## Fragment disposition

| Ref or line | Disposition | Reason |
|---|---|---|
| `claude/fouler-protect-learnloop-deploy-20260722` | Base, incorporated | Most complete safety, reporting, overlay, replay, candidate-gate, and learning line. |
| `origin/master` / `feat/learn-and-climb-20260613` | Selectively incorporated; ancestry bridged by `8ae19ffe05bb` | Search/eval, flatness, regret, whole-function tooling, done criteria, and termination analysis were ported before the ancestry-only merge. Old live launchers and shared-checkout improve windows conflict with the finite-season and isolated-candidate contracts and did not enter the selected tree. |
| `codex/jiggly-runtime-audit-fix` | Subsumed by the selected base plus finite-season authority | Its singleton, replay, reporting, and runtime-lease fixes are present in later implementations. Its old clean-supervisor and improve-window launchers are not runtime authorities. |
| `codex/devstream-fouler-sync-20260524` and `claude/improve-agent-cli-auth` | Subsumed | Exact-rating proof, deployment-spacing logic, and Claude CLI authentication are present in the selected implementation. Generated battle/proof dumps remain historical evidence, not source. |
| `codex/fouler-runtime-truth-20260712` | Superseded | Bounded drawdown intent is enforced by the finite-season authority without the old keepalive ownership path. |
| `claude/fouler-golive-runtime-fixes` | Superseded | Non-destructive cutover intent is retained; the new installer uses graceful drain, immutable releases, explicit backup, and exact rollback. |
| `claude/fouler-unattended-and-ip-fix-20260720` | Code subsumed; documentation and IP values superseded | Autoresearch evidence-quality and material-state logic are present later. Its `.125` address and legacy unattended supervisor instructions are stale. |
| `fouler/discord-report-quality-20260720` | Subsumed | The selected line includes the battle-specific message and correct last-five behavior. |
| `fouler/learn-loop-protect-and-closer-20260720` | Subsumed plus classifier port | Protect, deployment-judge, and closer correctness exist later; the missing termination classifier was ported explicitly. |
| `hermes/done-registry-20260720` | Done registry ported; shared surface-control edits superseded | Repository-local completion semantics were retained. OBS/control ownership belongs to the devstream broadcast manifest and director, not a Fouler launcher. |
| `rebaseline/upstream-anchor-20260704` | Preserved, not promoted | Its recorded Phase-A gate was `ACCEPT=false` and explicitly inconclusive. The branch remains the provenance for the 0.0.47 upstream experiment; it cannot replace the safety-rich engine without a discriminating head-to-head result. |
| `opus48/multisample-mcts` | Historical documentation only | No unique deployable engine change follows the shared base at its local tip. |
| `no-claude-standalone`, `hermes-integration`, `rescue/*`, `data-sync/*`, early `fix/atomic-singleton-lock-*`, and old sync/audit remote histories | Preserved, non-canonical | These unrelated histories remain reachable in the pre-consolidation bundle and remote refs. They are not release inputs and may not be launched. |

## Explicitly rejected runtime fragments

The following are preserved for provenance but must not be installed or invoked:

- `scripts/fouler_clean_supervisor.ps1` as a live owner.
- Legacy improve-window scheduled tasks and wrappers that force-kill the ladder,
  edit a shared checkout, clear a lease, or restart an old supervisor.
- Any launcher whose command points at `D:\Projects\fouler-play` rather than an
  admitted immutable release.
- Any run whose account, team, release, source tree, state root, or pause epoch
  differs from the finite-season authority.
- Any source or overlay configuration that embeds an old JIGGLYPUFF IP instead
  of consuming the canonical endpoint/port registry.

## Preserved provenance

Before reconciliation, all Fouler refs were captured in:

`devstream-codex-handoff/proofs/restoration-20260727T031042Z/fouler-all-refs-pre-consolidation.bundle`

SHA-256:
`C91AD718AC161E3E223D33DBE2C45AB6C3C6CC15E9547D1B3D571B7C396F06E3`

That bundle is recovery evidence, not an alternate source checkout or runtime.

## Promotion gate

The canonical line is not allowed to become the default branch or a live
release until all of the following are true:

1. Full Python suite and strict runtime lint pass from the exact candidate.
2. Search/eval additions pass the targeted regression suite and a
   discriminating candidate-versus-incumbent gate or remain explicitly disabled.
3. The immutable release manifest binds commit, tree, requirements, staged file
   hashes, task definition, account-season record, state root, and rollback.
4. Exactly one supervisor and one account-bound ladder controller exist after
   cutover and after task restart.
5. A 30-game boundary continues only through the bounded finite-season policy;
   no legacy launcher resurrects.
6. Horizontal and vertical OBS surfaces report the same battle IDs, ELO, season,
   and snapshot revision while output remains off.
7. Drawdown, pause, stale-release, duplicate-controller, occupied-port, and
   rollback injections all fail closed.
