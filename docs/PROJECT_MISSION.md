# fouler-play Mission

fouler-play is not just a forked Pokemon Showdown bot. Its devstream purpose is to become a serious Gen 9 OU improvement lab.

The human goal is:

- play bounded, real ladder batches on the account named by the active runtime lease and live process truth
- collect replay IDs, rating movement, decision traces, team choice, and failure classes
- turn battle evidence into concrete patches or tests
- repeat until the bot can reach and sustain 1700+ ELO and play competitive games at that level
- climb in explicit rating stages rather than treating every ladder batch as a 1700 push: prove 1500 safely, then 1600, then 1700 without a major pre-target skid, then the 30-game 1700 sustain window
- make the learning loop visible on stream without spamming Showdown or pretending random games are progress

The main way this project can drift off-base is by treating "the bot is playing" as success. Playing only matters if every batch leaves behind evidence: what happened, why the bot lost or won, what code/team assumption should change, and whether the next batch improved.

## Launch Readiness

Before a live batch, DEKU should prove:

- Showdown credentials pass the login-only probe
- OBS browser surfaces are available
- no prior active battle is stuck
- the previous autoresearch/report packet has either been acted on or explicitly deferred
- chat callouts are bounded and non-spammy

## DEKU Instructions

DEKU should never start a ladder batch as an open-ended background task. Use bounded run counts, archive the proof, packetize the findings, and stop cleanly.
