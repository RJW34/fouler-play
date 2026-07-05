# Mechanics-Backed Loss Learning Runbook

> **DORMANT (2026-07-04):** The "Running A Review" procedure below has no
> automated servicer -- nobody runs `replay_analysis.loss_learning` per battle,
> so the engine self-improvement loop it feeds is PARKED (constitution D4). The
> per-battle "replay review required" Discord prompts are now suppressed
> (`IMPROVE_LOOP_PARKED_NOTE` in `infrastructure/discord_reporting.py`, default
> parked; re-arm with `FOULER_IMPROVE_LOOP_ACTIVE=1`). This runbook is still a
> valid MANUAL procedure for an on-demand human/agent review; it is NOT a live
> loop.

Fouler-play must treat battle losses as evidence, not as a prompt for invented
Pokemon advice. The deterministic loss-learning layer lives in
`replay_analysis/loss_learning.py` and reads local Pokemon Showdown replay JSON
or log-equivalent artifacts.

## Evidence Sources

- Battle facts: local Pokemon Showdown replay JSON `log` lines.
- Pokemon facts: `data/pokedex.json`, generated from Pokemon Showdown
  `data/pokedex.ts` by `data/scripts/update_pokedex.py`.
- Move facts: `data/moves.json`, generated from Pokemon Showdown
  `data/moves.ts` by `data/scripts/update_moves.py`.
- Type chart: `fp.helpers.DAMAGE_MULTIPICATION_ARRAY`.
- Format usage support: `data/pkmn_sets_cache/<format>/showdown_sets.json` and
  `data/pkmn_sets_cache/<format>/replay_moves.json`.
- Damage engine boundary: `poke-engine` is installed locally for battle search,
  but loss learning does not claim unobserved damage ranges unless a complete
  local calc is provided. Observed log damage is source-backed; speculative
  damage ranges remain unknown.

## Running A Review

From `D:\Projects\fouler-play` on JIGGLYPUFF:

```powershell
.venv\Scripts\python.exe -m replay_analysis.loss_learning `
  replay_analysis\gen9ou-2613806724.json `
  --bot-username <runtime-account> `
  --output replay_analysis\loss_learning_latest.json
```

Multiple local losses can be reviewed together:

```powershell
.venv\Scripts\python.exe -m replay_analysis.loss_learning `
  replay_analysis\gen9ou-2613782584.json `
  replay_analysis\gen9ou-2613805929.json `
  --bot-username <runtime-account> `
  --min-repeats 2 `
  --output replay_analysis\loss_learning_latest.json
```

Use local replay JSON first. Do not fetch or scrape broad external datasets just
to explain a loss. If a replay is unavailable, report that detailed loss
learning is blocked for that battle.

## Reading The Output

The output contains:

- `artifacts`: one normalized loss artifact per replay.
- `team_learning_summary.proven_lessons`: repeated, source-backed lessons.
- `team_learning_summary.hypotheses`: source-backed findings seen fewer than
  `min_repeats` times.
- `team_learning_summary.must_not_conclude.unknown_claims`: claims that may be
  true, but are not backed by local mechanics data or battle-log proof.
- `team_learning_summary.must_not_conclude.rejected_claims`: claims contradicted
  by local data.

Do not update team files, search policy, or gameplan rules from a single loss
unless the artifact contains explicit battle-log proof and the change is a
low-risk tactical review item. Team-structure changes require repeated evidence
or manual review.

## Evidence-Backed Vs Hypothesis

Evidence-backed:

- "Earthquake KOed Torkoal on turn 1 and is locally verified as super-effective."
- "Spikes damaged our side on turns 2 and 5 in the battle log."
- "Gholdengo used Recover in this replay."

Hypothesis or unknown:

- "The opponent was Choice Scarf" without item reveal, Trick, Frisk, damage, or
  speed-order proof.
- "We lose to Gholdengo" from one replay.
- "A move was illegal" when the move exists but local learnset proof is absent.

Rejected:

- A type-effectiveness claim that contradicts the local type chart.
- An ability claim not listed for that Pokemon in local pokedex data, unless the
  battle log explicitly revealed an unusual transformation/source.
- A move name absent from local Pokemon Showdown move data.
