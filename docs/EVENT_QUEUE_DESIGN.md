# Consolidated Discord Event Posting System

**Date:** 2026-02-15  
**Status:** Design (not yet implemented)

## Problem

Multiple components post to Discord independently and simultaneously:
- `bot_monitor.py` — batch reports, startup messages, loss analyses, turn reviews
- `infrastructure/event-handlers.py` — crash alerts, WR drops, SSH failures
- `pipeline.py` — improvement analysis reports

When a crash + batch completion happen in the same heartbeat, contradictory messages fire (e.g., "bot crashed" + "batch report with ELO"). No precondition checks, no dedup, no ordering.

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ bot_monitor   │  │ event-handler│  │ pipeline.py  │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                  │
       └────────┬────────┴──────────────────┘
                │
         queue_event()
                │
                ▼
    ┌───────────────────────┐
    │  event_queue.json     │  ← file-based, flock'd
    │  (append-only queue)  │
    └───────────┬───────────┘
                │
                ▼
    ┌───────────────────────┐
    │  event_poster.py      │  ← single consumer, systemd service
    │  (poll → check → post)│
    └───────────────────────┘
```

## Event Queue

**Location:** `/home/ryan/projects/fouler-play/event_queue.json`

**Schema:** JSON array of event objects:

```json
{
  "id": "uuid4",
  "type": "batch_report | crash_alert | wr_drop | startup | loss_analysis | turn_review | pipeline_report",
  "channel": "battles | project | feedback",
  "content": "message string",
  "precondition": "bot_running | bot_stopped | none",
  "priority": 0,
  "timestamp": "2026-02-15T14:30:00",
  "dedup_key": "optional hash for dedup",
  "buffered": false
}
```

**Priority:** 0 = crash alerts (highest), 1 = batch reports, 2 = turn reviews, 3 = pipeline reports

## queue_event() — Single Entry Point

```python
# fouler-play/event_queue_lib.py

import json, uuid, fcntl, hashlib
from datetime import datetime
from pathlib import Path

QUEUE_FILE = Path(__file__).parent / "event_queue.json"

def queue_event(
    event_type: str,
    channel: str,         # "battles" | "project" | "feedback"
    content: str,
    precondition: str = "none",  # "bot_running" | "bot_stopped" | "none"
    priority: int = 2,
    dedup_key: str = None  # if provided, dedup against recent posts
):
    """Append an event to the queue. File-locked for concurrent safety."""
    event = {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "channel": channel,
        "content": content,
        "precondition": precondition,
        "priority": priority,
        "timestamp": datetime.now().isoformat(),
        "dedup_key": dedup_key or hashlib.md5(
            f"{event_type}:{content[:100]}".encode()
        ).hexdigest(),
        "buffered": False,
    }

    with open(QUEUE_FILE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            queue = json.load(f)
        except (json.JSONDecodeError, ValueError):
            queue = []
        queue.append(event)
        f.seek(0)
        f.truncate()
        json.dump(queue, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)
```

## event_poster.py — Single Consumer

Runs as systemd service `fouler-event-poster.service`. Polls queue every 2 seconds.

```python
# fouler-play/event_poster.py  (pseudo-code)

DEDUP_WINDOW = 10  # seconds
recent_posts = {}  # dedup_key -> timestamp

def check_precondition(precondition: str) -> bool:
    if precondition == "none":
        return True
    if precondition == "bot_running":
        return is_bot_running()
    if precondition == "bot_stopped":
        return not is_bot_running()
    return True

def is_bot_running() -> bool:
    """Check if fouler-play bot process is alive."""
    pid_file = Path("/home/ryan/projects/fouler-play/.pids/bot_main.pid")
    if not pid_file.exists():
        return False
    try:
        data = json.loads(pid_file.read_text())
        pid = data["pid"]
        os.kill(pid, 0)  # signal 0 = existence check
        return True
    except (ProcessLookupError, OSError, KeyError):
        return False

def is_duplicate(event) -> bool:
    key = event["dedup_key"]
    now = time.time()
    if key in recent_posts and (now - recent_posts[key]) < DEDUP_WINDOW:
        return True
    return False

def post_event(event):
    """Post via Discord webhook."""
    webhook = WEBHOOKS[event["channel"]]
    requests.post(webhook, json={"content": event["content"]})
    recent_posts[event["dedup_key"]] = time.time()

def poll_loop():
    while True:
        queue = load_and_lock_queue()
        remaining = []
        # Sort by priority (lower = higher priority)
        queue.sort(key=lambda e: e["priority"])

        for event in queue:
            if is_duplicate(event):
                continue  # drop it
            if check_precondition(event["precondition"]):
                post_event(event)
                time.sleep(0.5)  # rate limit between posts
            else:
                # Buffer: keep in queue for retry
                event["buffered"] = True
                remaining.append(event)

        write_and_unlock_queue(remaining)
        # Prune buffered events older than 10 minutes
        time.sleep(2)
```

## Precondition Matrix

| Event Type | Precondition | Logic |
|---|---|---|
| `crash_alert` | `bot_stopped` | Only post if bot PID is dead |
| `batch_report` | `bot_running` | Only post if bot is alive (data is fresh) |
| `startup` | `none` | Always post (it IS the bot starting) |
| `loss_analysis` | `none` | Always post (replay analysis is async) |
| `turn_review` | `none` | Always post |
| `wr_drop` | `bot_running` | Only if bot is running (stale drops are noise) |
| `pipeline_report` | `none` | Always post |

## Dedup Strategy

- Each event gets a `dedup_key` = MD5 of `"{type}:{first 100 chars of content}"`
- Poster tracks `recent_posts[dedup_key] = timestamp`
- Skip if same key posted within last 10 seconds
- Crash alerts specifically: dedup on `"crash:{process_name}"` with 60s window

## Integration Points — What Changes

### 1. `bot_monitor.py`
**Replace** `send_discord_message()` calls with `queue_event()`:

```python
# Before:
await self.send_discord_message(msg, channel="battles", suppress_embeds=True)

# After:
from event_queue_lib import queue_event
queue_event("batch_report", "battles", msg, precondition="bot_running", priority=1)
```

Affected methods:
- `flush_batch_report()` → queue batch report
- `analyze_loss_async()` → queue turn review
- `run_bot()` → queue startup message

### 2. `infrastructure/event-handlers.py`
**Replace** `post_to_discord()` with `queue_event()`:

```python
# Before:
EventHandler.post_to_discord(f"🚨 {process_name} crashed")

# After:
queue_event("crash_alert", "project", f"🚨 {process_name} crashed",
            precondition="bot_stopped", priority=0,
            dedup_key=f"crash:{process_name}")
```

### 3. `pipeline.py`
**Replace** direct webhook/openclaw calls with `queue_event()`:

```python
queue_event("pipeline_report", "project", report_text, priority=3)
```

## Systemd Service

```ini
# /etc/systemd/system/fouler-event-poster.service
[Unit]
Description=Fouler Play Event Poster
After=network.target

[Service]
Type=simple
User=ryan
WorkingDirectory=/home/ryan/projects/fouler-play
ExecStart=/home/ryan/projects/fouler-play/venv/bin/python event_poster.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Buffering Behavior

When bot is down:
1. `batch_report` events (precondition=`bot_running`) stay in queue
2. `crash_alert` fires immediately (precondition=`bot_stopped` ✓)
3. When bot restarts, buffered batch reports either post (if still relevant) or get pruned (if >10 min old)

## Edge Cases

- **Queue file corruption:** If JSON parse fails, start fresh `[]`
- **Poster crashes:** Systemd restarts it; queue persists on disk
- **Bot monitor crashes:** Events already queued are still posted
- **Concurrent writers:** `fcntl.flock` on queue file prevents corruption
- **Queue grows unbounded:** Prune events older than 10 minutes on each poll

## Migration Path

1. Add `event_queue_lib.py` (queue_event function)
2. Add `event_poster.py` (consumer service)
3. Install systemd service
4. Replace `send_discord_message` / `post_to_discord` calls one file at a time
5. Remove webhook logic from bot_monitor.py last (it's the biggest change)
