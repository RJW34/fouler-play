# Fouler Play Discord Channel Configuration

**Current Webhook Setup (VERIFIED):**

## Channel Mapping

### 1. #project-fouler-play (1466691161363054840)
**Webhook:** `DISCORD_WEBHOOK_URL`
**Purpose:** Project updates, system status, elo tracking
**Posts:**
- Bot startup/shutdown notifications
- Process status changes
- System errors
- General updates

### 2. #fouler-play-battles (1466911872967250138)
**Webhook:** `DISCORD_BATTLES_WEBHOOK_URL`
**Purpose:** Live battle notifications
**Posts:**
- Battle start notifications ("Started battle vs X")
- Battle results (Win/Loss)
- Replay links
- Real-time battle updates

### 3. #fouler-play-feedback (1466869808200028264)
**Webhook:** `DISCORD_FEEDBACK_WEBHOOK_URL`
**Purpose:** Turn-by-turn analysis and learning
**Posts:**
- Loss analysis reports
- Mistake identification
- Turn review insights
- Improvement suggestions

## Webhook URLs (from .env)

```bash
# Project updates
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1467010283741384849/...

# Battle notifications
DISCORD_BATTLES_WEBHOOK_URL=https://discord.com/api/webhooks/1467011637885145190/...

# Turn reviews and feedback
DISCORD_FEEDBACK_WEBHOOK_URL=https://discord.com/api/webhooks/1467251085092851856/...
```

## Usage in bot_monitor.py

```python
# Send to project channel
await self.send_discord_message("System update", channel="project")

# Send to battles channel
await self.send_discord_message("Battle result", channel="battles")

# Send to feedback channel
await self.send_discord_message("Turn review", channel="feedback")
```

## Verification

Test webhooks:
```bash
# Test project webhook
curl -X POST "https://discord.com/api/webhooks/1467010283741384849/..." \
  -H "Content-Type: application/json" \
  -d '{"content": "Test from #project-fouler-play"}'

# Test battles webhook
curl -X POST "https://discord.com/api/webhooks/1467011637885145190/..." \
  -H "Content-Type: application/json" \
  -d '{"content": "Test from #fouler-play-battles"}'

# Test feedback webhook
curl -X POST "https://discord.com/api/webhooks/1467251085092851856/..." \
  -H "Content-Type: application/json" \
  -d '{"content": "Test from #fouler-play-feedback"}'
```

## Current Issues

**Post-BAKUGO Integration:**
- Need to verify battles are posting to #fouler-play-battles
- Need to verify turn reviews are posting to #fouler-play-feedback
- Check if BAKUGO's additions broke webhook routing

## Debugging

Check bot_monitor.py for proper channel routing:
```bash
grep -n 'channel="battles"' bot_monitor.py
grep -n 'channel="feedback"' bot_monitor.py
```

Verify webhooks are loaded:
```bash
grep "DISCORD.*WEBHOOK" .env
```

Check recent logs for webhook errors:
```bash
tail -100 bot_monitor_output.log | grep -i webhook
```
