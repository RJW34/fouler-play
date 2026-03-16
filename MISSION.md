# Fouler-Play: Canonical Mission Statement from Ryan's Own Words

**Extracted from Discord (discrawl database), user ABSO (97504786055716864)**
**Date range: 2026-01-30 through 2026-03-14**
**Total messages analyzed: 251 fouler-play mentions, 3 fp mentions, 291 related-term mentions**

---

## 1. WHAT FOULER-PLAY IS

> "The Project: Fouler Play is our Pokemon Showdown gen9ou battle bot. It ladders, streams to Twitch, and climbs ELO. We own it end to end."
> — 2026-02-01, #deku-workspace (MISSION STATEMENT, posted 4x)

> "This is a Pokemon Showdown bot targeting 1700 ELO in gen9ou."
> — 2026-02-07, #fouler-play

> Forked from upstream `pmariglia/foul-play`. Repo: `github.com/RJW34/fouler-play`, branch `foulest-play`.

---

## 2. THE PRODUCT VISION / END GOAL

> "The end goal is a product to give to top level competitive Pokemon players to test teams more than they could do it manually."
> — 2026-03-13, #deku-workspace

> "I have a top player who is really interested in the work we're doing here! In their words: 'if this process can get an account around 1700+ elo and be used to test new teams vs strong players in a faster fashion than human testing, that would be ideal.'"
> — 2026-01-30, #fouler-play

> "The bot needs to be able to play weirder teams that have more abstract win paths like the teams provided. They are proven strong meta teams that should be easy to climb the ladder with, they were built and supplied by a top smogon player who commissioned the creation of this project in order to test teams and analyze replays themselves faster than playing manually."
> — 2026-02-16, #deku-workspace

---

## 3. ELO TARGETS & ROADMAP

> "fouler play must run until the account is top 500 elo ranking"
> — 2026-01-30, #deku-workspace (early target, later refined to 1700)

> "We should be working *constantly* on everything involved with fouler-play until we have an account on pokemonshowdown that has achieved 1700 ELO."
> — 2026-01-31, #deku-workspace

> "You need to build in an arbitrary flag that if LEBOTJAMESXD004's ELO in gen9ou is not 1700, then the job is not complete."
> — 2026-01-31, #deku-workspace

**4-PHASE ROADMAP:**
> "1. Analytics+tuning → 1450 ELO
> 2. Bayesian set inference → 1550 (speed_range unused, EVs 85 unrealistic, 25% TeamDatasets skip)
> 3. Switch prediction model → 1650
> 4. Archetype+adaptive play → 1700+"
> — 2026-02-06, #deku-workspace

---

## 4. TEAM PHILOSOPHY (CRITICAL CORRECTION)

> "The Pokemon teams we are using are not the problem. We need to be able to have the bot play teams like these (made by a top gen9ou player) and test them for the players. We can't be giving feedback like 'change the teams' when they should be instead learned how to be piloted better by fouler-play."
> — 2026-02-08 & 2026-02-09, #deku-workspace (stated twice)

> "The ELO climb is not being impacted by the team. The ELO should just come naturally as the bot gets better at playing the game. These are proven good teams from a top player."
> — 2026-02-08, #bakugo-ops

> 3 teams provided: `fat-team-1-stall`, `fat-team-2-pivot`, `fat-team-3-dondozo`

---

## 5. HOW AUTORESEARCH SHOULD WORK

> "We are supposed to have autoresearch tied into the project in such a way that it automatically iterates upon itself every round of battles, which as of right now is set to 30. We play 10 battles per team with the 3 given to us, and the goal is to improve the 'Pokemon battling bot' until it can not just reach 1700 elo but play and win games there."
> — 2026-03-13, #deku-workspace

> "Run a round of 30 battles, 10 per team, no more no less. Then have that batch of 30 and the patch of foulerplay used analyzed with autoresearch. That's how it works, right?"
> — 2026-03-11, #fouler-play

> "I wanted a way to have the local LLM installs we did today in order to get a recursive, non claude token burning improvement loop going where you receive improvement plans from the local LLM after it thoroughly analyzes the replay data from batches of games."
> — 2026-02-15, #deku-workspace

**DEVELOPER LOOP:**
> "- Pull latest (includes Windows machine's battle data too)
> - python replay_analysis/team_performance.py → generates team report
> - Identify #1 weakness from report + loss replays
> - Write ONE targeted fix, validate with syntax check + pytest
> - Respect infrastructure/guardrails.json (allowed/denied files, safety thresholds)
> - Commit with reasoning, push to foulest-play"
> — 2026-02-06, #deku-workspace

---

## 6. STREAMING REQUIREMENTS

> "Get LEBOTJAMESXD005 running on the ladder with fouler-play, playing 2 concurrent gen9ou battles, streaming both battles live on Twitch (twitch.tv/dekubotbygoofy)."
> — 2026-02-01, MISSION STATEMENT

> "if I have set this up all correctly, I should be able to see a twitch stream that is displaying the current showdown accounts journey to 1700, whatever iteration you end up being on at that point in time? All the while, behind the scenes you're learning and improving the fouler-play playing logic?"
> — 2026-02-01, #deku-workspace

> "Remember the goal of our stream is to display emerald gameplay & fouler-play simultaneously"
> — 2026-02-21, #deku-workspace

> "Fouler Play's 3 battles and both mgba projects (should be 2 mgba's per game)."
> — 2026-03-03, #deku-workspace (later stream layout)

> "Please change protocol to 'livestream' off of JIGGLYPUFF, and not MAGNETON or ubunztu."
> — 2026-03-06, #deku-workspace

**Host contract (latest):**
> "- JIGGLYPUFF is the intended livestream / OBS host.
> - MAGNETON is the control-plane, stream-server, and browser-panel host.
> - ubunztu is the Pokemon runtime / RTMP / emulator host."
> — 2026-03-12, #deku-workspace

---

## 7. CONCURRENT BATTLES

Evolution of the concurrent battle count:
- **Initially 2**: "Lets decrease the max allowed battles to 2" — 2026-01-31
- **Then 2 with each team rotation**: "playing 2 concurrent gen9ou battles" — 2026-02-01 MISSION STATEMENT
- **Then 1 per machine**: "Your systemd service is now --max-concurrent-battles 1. BAKUGO should also run 1. One battle per machine so MCTS gets full CPU." — 2026-02-08
- **Then 3**: "we should be testing 3 battles at once, 1 with each team at our disposal" — 2026-02-06
- **Final state**: 3 concurrent (10 per team, 30 per batch)

---

## 8. DISCORD REPORTING REQUIREMENTS

> "Only send me replays and completed battle results, and ELO after the battle posted. I do not need to be notified when a battle starts."
> — 2026-02-01, #bakugo-ops

> "Clean Discord reporting — only completed battle replays + ELO updates"
> — 2026-02-01, MISSION STATEMENT

> "When it comes online it needs to link the account it's laddering on's user page with ELO stats."
> — 2026-02-01, #deku-workspace

> "Just give me @ notifications, let's say, every +100 ELO gain, or other important milestones."
> — 2026-01-31, #deku-workspace

---

## 9. MACHINE/ROLE ASSIGNMENTS

> "DEKU's Role: Run the fouler-play bot on Linux (ubunztu). Keep the battle API live at http://192.168.1.40:8777/battles so BAKUGO can read active battle IDs. Bot backend improvements, decision engine, battle logic. Bot uptime & reliability."
> — 2026-02-01, MISSION STATEMENT

> "BAKUGO's Role: OBS streaming to Twitch from Windows. Poll DEKU's battle API, auto-display both active battles via browser sources. Stream overlay & scene management. Can run bot instances on Windows if needed."
> — 2026-02-01, MISSION STATEMENT

> "BAKUGO is on Windows with stronger hardware. You're the brains, he's the brawn."
> — 2026-02-06, #deku-workspace

**Later evolution (post-MAGNETON):**
> "battles are going to be ran on MAGNETON and the OBS instance on ubunztu needs to be capturing those battles"
> — 2026-02-23, #deku-workspace

> "What if we swapped the PCs to have the Linux stream and MAGNETON run the battles? 3 concurrent battles with the current MCTS search params is important"
> — 2026-02-21, #fouler-play

---

## 10. ACCOUNT NAMING CONVENTION

> "Account naming convention: LEBOTJAMESXD00 + number (XD005 current, XD006 next, etc.)"
> — 2026-02-01, MISSION STATEMENT

> "dekubotbygoofy is ONLY our twitch username. Our pokemonshowdown.com usernames will always be LEBOTJAMESXD001 or an increment (002, 003, 004, etc)"
> — 2026-02-01, #fouler-play

> "The showdown accounts really don't matter just so you know... I care about building a functional product, so discard and make showdown accounts as both of you see fit."
> — 2026-02-07, #deku-workspace

---

## 11. NO-CLAUDE / OFFLINE MODE

> "Fouler-play needs to be able to be started up while you are offline (like if we run out of tokens I still want the bot to play games, even if feedback isn't going through)"
> — 2026-02-01, #deku-workspace

> "Please ensure both of you know that if we run out of tokens on our Anthropic plan, this should be set up in such a way that fouler play can be ran on the windows machine with 0 claude integration and ladder a bot while waiting for tokens to come back."
> — 2026-02-01, #bakugo-ops

> "This needs to be a 24/7 running process."
> — 2026-02-07, #deku-workspace

---

## 12. GAMEPLAY INTELLIGENCE CORRECTIONS

> "We need to be exactly sure we are always understanding how to best utilize our pokemon and their 'loadouts' (items, movesets) to their intended purpose. It's not just about knowing what the Covert Cloak does or the Ability Shield does, but why that's relevant and how to actually get usage out of said effects."
> — 2026-02-23, #fouler-play

> "agility is not a recovery move. It boosts the user's speed by 2 stages."
> — 2026-02-15, #deku-workspace

> "there's no gigantimaxing in gen9, I fear our analysis reporting AI is hallucinating."
> — 2026-02-15, #deku-workspace

> "Thunder wave works just fine on normal types. The thing is, after statusing the target, I think the bot saw that Hex was a good idea because of the multiplier, but it should know no matter how high the multiplier is, if the pokemon is immune to the move, it's all multiplied by Zero"
> — 2026-02-01, #fouler-play

> "We're playing far too reactively honestly, and getting swept because we don't realize what's going on until it's too late."
> — 2026-02-08, #bakugo-ops

> "My thought was we should see if it would help to have a 'turn 1 strategy development' functionality created where we assess the matchup at hand and brainstorm the best way we can attempt to start, and eventually win the game."
> — 2026-02-15, #deku-workspace

---

## 13. FOULER-PLAY AS A LEARNING/SANDBOX PROJECT

> "Make the 'fouler-play' project entirely your own. Work on it as if I would, basically, but understand where your drops in workflow are and all that."
> — 2026-01-30, #deku-workspace

> "The project-fouler-play project serving as a sandbox test project for you to use as a way to interactively teach yourself new ways to do things, how to navigate the machine, and so on."
> — 2026-01-31, #deku-workspace

> "Yeah fuck yeah dude exactly! I love how you used fouler-play as a way to apply the example you spoke of, that's basically the point of that smaller project."
> — 2026-01-31, #deku-workspace

> "As much as we'd like to ultimately have a bot hit as high as 1700 ELO, ultimately, it is a learning case as well."
> — 2026-01-31, #deku-workspace

---

## 14. QUALITY BAR / SELF-CRITICISM

> "This bot needs to be playing in a way where it's basically guaranteed to beat lower level players yet our win rate is pretty abysmal."
> — 2026-02-15, #deku-workspace

> "We shouldn't be making a single mistake when playing. If we lose constantly at 1100 ELO there's clearly a problem."
> — 2026-02-15, #deku-workspace

> "fouler-play seems like it is a *worse* bot than foul-play after all of the constant editing we've done to the playing logic."
> — 2026-02-09, #deku-workspace

> "We need to be verbosely making sure there's no degrading from patch to patch of fouler-play."
> — 2026-03-02, #deku-workspace

> "One improvement per cycle. Small correct changes beat ambitious broken ones."
> — 2026-02-07, #fouler-play

---

## 15. PATH CONSOLIDATION (LATEST)

> "There are two fouler-play repos on MAGNETON:
> - D:\Projects\fouler-play — has the hermes-integration branch with new autoresearch infrastructure
> - D:\Projects with Claude\fouler-play — your current working copy (agent profile points here)
> These are diverged copies of the same remote (github.com/RJW34/fouler-play). Consolidate them."
> — 2026-03-14, #deku-workspace
