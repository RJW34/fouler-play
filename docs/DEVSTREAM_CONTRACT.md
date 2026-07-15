# fouler-play Devstream Contract

fouler-play is a bounded-cycle competitive Pokemon Showdown improvement lab. The project started from `foul-play`, but the devstream goal is much sharper: tune it for high-level Gen 9 OU until the bot can reach 1700+ ELO and play credible games against players in that band.

Live battles are not just content. They are the training and evaluation loop: collect decision traces, replay evidence, matchup failures, ladder rating movement, and DEKU-authored improvement notes, then feed those findings back into the bot.

## Runtime Boundaries

- `ubunztu` is the control-plane and development home for status, dry-run, analysis, and HERMES proof work.
- `JIGGLYPUFF` is the only production battle/OBS worker. It may run only an exact pushed commit from `D:\Releases\fouler-play\<commit>` after a deployment receipt and finite DEKU-signed v3 lease bind the physical host, account, source tree, run count, cycles, concurrency, replay behavior, and expiry.
- Uses repo-local virtualenvs so `poke-engine` and Showdown runtime dependencies are present without modifying system Python.
- Does not autostart from the contract exporter.
- The canonical OBS HTTP surface is `streaming/serve_obs_page.py` on `127.0.0.1:8777` of the active runtime host.
- The health probe is read-only by default and does not start games, restart services, or mutate battle state.
- Existing developer-loop and pipeline services are not treated as the same thing as the devstream runner.
- Controller probes that use `scripts/jigglypuff_devstream_control.py status --read-only` must not write local mirror files or remote `devstream\truth\jigglypuff-runtime.json`; scheduled/resident proof production uses the normal status path so the artifact is produced on JIGGLYPUFF with `producer.expectedHostMatched=true`.

### Owner-authorized runtime-boundary exception

The autonomous improvement guardrail still treats `run.py`, `config.py`, `.env`, and
`teams/**` as `never_modify`. For this operator-reviewed immutable-runtime migration, the
owner explicitly authorized the narrow changes already present in `run.py`, `config.py`,
and `teams/load_team.py` that move secrets, logs, state, provenance, and the team-rotation
cursor outside the release and make runtime authority fail closed. This exception does not
authorize autonomous edits, team-definition changes, or future expansion of those paths;
`infrastructure/guardrails.json` remains unchanged and continues to constrain HERMES.

## OBS Surfaces

- `http://127.0.0.1:8777/slot/1`
- `http://127.0.0.1:8777/slot/2`
- `http://127.0.0.1:8777/slot/3`
- `http://127.0.0.1:8777/overlay?mode=bottom&hide_recent=1`
- `http://127.0.0.1:8777/health`

The `/dashboard/hybrid` and `/overlay/hybrid` endpoints are operator-only decision review surfaces. Public OBS scenes must use slot or battle-lab overlay endpoints only, with health, model decisions, and proof details kept in reports rather than visible Twitch panels.

## External Go-Live Gates

- Official Pokemon Showdown ladder automation is blocked until a Pokemon Showdown administrator gives written approval for this disclosed bot and its three-concurrent-battle shape. The public server currently permits multiple games technically, but the [April 2026 staff policy update](https://www.smogon.com/forums/threads/ladder-bots-and-usage-based-tiering.3774656/page-3#post-10957757) describes undisclosed bot limiters and does not grant blanket ladder-bot approval. Local/private Showdown rehearsals do not satisfy the public-ELO gate.
- The bot must obey the [Pokemon Showdown rules](https://pokemonshowdown.com/rules): no self-play on the public ladder, boosting, intentional losses, timer abuse, spam, exploits, or behavior intended to game usage or rating systems.
- Production battle chat defaults to an opt-in, rate-limited `gg`. The historical live-only Twitch message remains implemented but requires the independent `FOULER_POST_BATTLE_PROMO_AUTHORIZED=1` gate plus positive stream truth; keep that gate off unless a Pokemon Showdown administrator gives written authorization for promotional battle chat.
- Twitch Dual Format requires OBS Studio 32 or newer, Enhanced Broadcasting, and a supported current vertical plugin. The production scene must render both 1920x1080 and 1080x1920 from one main OBS start action; the vertical plugin's separate go-live action remains unused. Verify both compositions locally, then use bandwidth-test mode and [Twitch Inspector](https://inspector.twitch.tv/) before a public broadcast.
- A pasted or otherwise exposed Twitch key is never production-ready. Rotate it in Twitch, provision the replacement directly into protected OBS configuration, and keep it out of files, commands, logs, screenshots, proof artifacts, and chat.

## Truth Files

- `active_battles.json`
- `stream_status.json`
- `daily_stats.json`
- `battle_stats.json`
- `replay_analysis/autoresearch_latest.json`
- `replay_analysis/reports/autoresearch_latest.md`
- `stability_report.json`
- `devstream/truth/elo-proof.schema.json`
- `devstream/truth/elo-proof.example.json`

`stream_status.json` and report files may be stale while the project is idle. The health probe reports that as `idle` or `degraded`; it does not start battles to refresh them.

## Bounded Session Workflow

Production launch is one receipt-bound transaction:

1. Push the reviewed commit and install that exact revision under `D:\Releases\fouler-play\<commit>`.
2. Generate the deployment receipt on JIGGLYPUFF and transfer its exact bytes to DEKU.
3. On DEKU, issue one finite signed v3 lease from that receipt. The private key never leaves DEKU.
4. Stage the public keyring and signed lease with `scripts/install_runtime_authority.ps1`; this step starts no process and mutates no task.
5. Validate the staged lease, receipt, checkout, host binding, bounds, and account before backing up or replacing `HERMES-FoulerBattleSupervisor`.
6. Start one supervised three-slot batch from the same immutable release. Direct `run.py`, mutable-checkout, local lease-minting, and autorenew paths are retired.

`scripts/devstream_session.py doctor` and non-executing login/session commands remain useful read-only developer probes. `scripts/devstream_session.py start --execute` is not the production launch authority; the exact-release Windows supervisor wrapper owns that boundary.

`scripts/showdown_login_check.py --execute` is the credential proof gate. It logs into Pokemon Showdown, does not queue a battle, does not chat, and never prints the password. A bounded ladder cycle should not start until this probe passes.

The managed stop path is drain-first. It writes the drain request, waits for `active_battles.json` to clear, and then terminates only exact-release owned process trees. Use forced termination only when forfeiting active battles is acceptable and after task/process backup evidence exists.

## Improvement Loop

The intended loop is:

1. Run a bounded battle batch.
2. Capture battle stats, replays, and decision traces.
3. Have DEKU summarize losses, matchup failures, incorrect choices, and team/archetype drift.
4. Convert the report into constrained work packets.
5. Patch evaluation, prediction, team intent, or reporting logic.
6. Re-run regression tests and another bounded battle batch.

The stream should make that loop visible, not merely show random ladder games.

## Health Probe

Run:

```bash
cd /home/ryan/projects/fouler-play
.venv/bin/python scripts/devstream_health.py
```

When the OBS server is running, `/health` returns the same structured payload via `streaming/serve_obs_page.py`.

For controller-side inspection, prefer:

```bash
cd /home/ryan/projects/fouler-play
.venv/bin/python scripts/jigglypuff_devstream_control.py status --read-only
```

This is a no-write probe. It reports JIGGLYPUFF status without creating a controller-produced mirror or refreshing the remote runtime proof file. A proof artifact refreshed by the normal status path must include `proofArtifact.written=true` and `producer.expectedHostMatched=true`.

`scripts/devstream_session.py doctor --require-ready` is also read-only. It must be able to import
`psutil` from the selected Python so PID files can be verified against real process command lines and
start times. If `psutil` is missing, stale `.bot.pid` or `.pids/*.pid` artifacts are treated as
untrusted blockers, not live runtime proof.

The doctor also fails closed when account authorities disagree. `.env` / `SHOWDOWN_USER_ID`, mission
docs, and any runtime lease must name the same Showdown account before an execute path is considered
ready. A dry-run lease authorizes only its dry-run purpose, and an expired proof window never authorizes
execute.

## Next Work Packets

1. Generate live ELO proof files that conform to `devstream/truth/elo-proof.schema.json`.
2. Generate a richer completion summary after each bounded session, including battle ids, replay ids, and rating deltas.
3. Wire `scripts/devstream_packetize.py --write` into a human-reviewed DEKU packet flow.
4. Write `devstream/truth/completion.json` at bounded cycle end with battle counts, replay ids, report paths, rating deltas, and validation status.
5. Retire or clearly label legacy 6-slot text-source docs so the browser-source architecture is obvious.
6. Promote `showdown_login_check.py --execute` into the standard DEKU certification step before any ladder batch.
