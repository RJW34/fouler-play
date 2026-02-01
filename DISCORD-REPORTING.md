# Discord Reporting Strategy

**Problem:** Building tools and systems but not reporting progress to Discord where Ryan can see it.

**Solution:** Structured reporting to relevant channels during work.

## Channel Map

**Primary:** `#project-fouler-play` (1466691161363054840)
- System status updates
- Diagnostic reports
- Learning system progress
- Bot elo tracking

**Coordination:** `#deku-bakugo-sync` (1467359048650330316)
- Cross-platform coordination (Linux ↔ Windows)
- BAKUGO task delegation
- Joint diagnostic results

**Feedback:** `#fouler-play-feedback` (1466869808200028264)
- Turn-by-turn gameplay review
- Strategy discussions
- Human input on bot decisions

## Reporting Schedule

### Every Heartbeat (30 min)
Post to `#project-fouler-play`:
```
📊 Status: Stream ✅ | Bot ✅ | Overlay ✅ | Elo: 1523 (+12)
Observer: ✅ Running | Games: 15 collected
```

**Only if status changed** or significant event (elo jump, crash, recovery)

### When Games Analyzed (10+ games)
Post to `#project-fouler-play`:
```
🎯 Learned Patterns from 23 High-Elo Games

✅ Best Openings:
- Corviknight: U-turn → 85% (17W / 3L)
- Clodsire: Toxic → 78% (14W / 4L)

❌ Avoid:
- Blissey: Seismic Toss → 25% (3W / 9L)

🚫 Common Mistakes:
- Skarmory: Whirlwind (7 losses vs 1 win)

Full analysis: /fouler-play/research/learned-patterns/
```

### On System Events
- Bot crash → immediate post with diagnosis
- Elo milestone → celebratory post
- New learning integrated → announcement
- Auto-fix triggered → log what was fixed

### BAKUGO Coordination
Post to `#deku-bakugo-sync` when:
- Windows work needed (OBS, overlay, desktop)
- Cross-platform diagnostics complete
- Coordinating startup/shutdown
- Delegating tasks

## Implementation

### Manual Reporting (current)
Use `message` tool to send updates:
```bash
# In any script
clawdbot message send --target 1466691161363054840 --message "Status update"
```

### Automated Reporting (future)
- Heartbeat hook: auto-post status if changed
- Analysis hook: auto-post when patterns learned
- Event hooks: auto-post on crashes/recoveries
- GitHub Actions: auto-post on commits/deploys

## Message Formatting

**Status Updates:** Compact, emoji-based
```
📊 Stream: ✅ | Bot: ✅ | Elo: 1523 (+12)
```

**Analysis Results:** Structured with clear sections
```
🎯 Learned Patterns
✅ DO THIS
❌ AVOID THIS
```

**Errors:** Clear problem + action taken
```
❌ Bot crashed (SIGTERM)
✅ Auto-restarted (PID 12345)
📝 Logged to diagnostics/
```

**Delegation:** @mention with clear task
```
@BAKUGO - OBS overlay refresh needed
Current issue: Browser source stuck
Action needed: Restart browser source in OBS
```

## Anti-Patterns to Avoid

❌ **Silent work:** Building tools for hours without updates
❌ **Batch dumps:** Posting 5 paragraphs at once
❌ **Wrong channel:** Posting to #deku-workspace instead of project channel
❌ **No evidence:** "It's working" without verification
❌ **Spam:** Posting every tiny change

✅ **Good patterns:**
- Event-driven updates (when something happens)
- Evidence-based (screenshots, logs, numbers)
- Right channel for the topic
- Concise format
- Clear next steps

## Current Gaps (what I just fixed)

1. ✅ Posted learning system status to #project-fouler-play
2. ✅ Coordinated with BAKUGO on #deku-bakugo-sync
3. ✅ Created diagnostic reporting script
4. 📋 TODO: Integrate auto-reporting into heartbeat
5. 📋 TODO: Post analysis results when first games analyzed

---

**Key Principle:** Build in public (to Discord). Ryan should see progress as it happens, not after the fact.
