# State Endpoint Status Field Fix

**Date:** 2026-02-08  
**Issue:** `/state` endpoint status field not updating during active battles  
**Impact:** OBS overlay showed "SCANNING..." during battles

## Problem

The `/state` endpoint at `http://192.168.1.40:8777/state` reported `status: "Searching"` even when BugInTheCode had an active battle. Battle data existed in `battles[]` array but the `status` field didn't update to reflect active battle state.

### Root Cause

Two state server implementations (`serve_obs_page.py` and `stream_server.py`) were reading status from `stream_status.json` which only gets updated when battles **end** (in `fp/run_battle.py`). When a battle started, the status file still contained stale "Searching" value from the previous battle.

The servers never checked `active_battles.json` to determine current battle state when serving the `/state` endpoint.

## Solution

Updated both server files to dynamically compute status based on active battles:

### Files Modified

1. **`streaming/serve_obs_page.py`** (production server)
   - `build_state_payload()`: Added logic to set status="Active" when battles exist
   - `handle_status()`: Same logic for `/status` endpoint

2. **`streaming/stream_server.py`** (legacy Linux streaming server)
   - `_build_status_payload()`: Added same logic

### Logic Added

```python
if battles:
    status["status"] = "Active"
    status["battle_info"] = ", ".join(
        f"vs {b.get('opponent', 'Unknown')}" for b in battles
    )
else:
    # No active battles - revert to Searching
    if status.get("status") in ("Active", "Battling"):
        status["status"] = "Searching"
        status["battle_info"] = "Searching..."
```

## Verification

### Manual Testing

```bash
# With active battle (BugInTheCode vs Adlet)
$ curl http://192.168.1.40:8777/state | jq .status.status
"Active"  ✅

# After battle ends (battles cleared)
$ curl http://192.168.1.40:8777/state | jq .status.status
"Searching"  ✅
```

### Automated Test

Created `tests/test_state_endpoint_status.py` to prevent regression:
- Verifies status="Active" when battles exist
- Verifies status="Searching" when battles cleared
- Tests battle_info field updates correctly

```bash
$ venv/bin/python tests/test_state_endpoint_status.py
✅ All status field tests passed
```

## Deployment

1. Server restarted with fix: `venv/bin/python -m streaming.serve_obs_page`
2. Running on ubunztu:8777 (PID 390748)
3. BAKUGO notified for OBS overlay verification

## Expected Behavior

- **Before battle:** status="Searching", battle_info="Searching..."
- **During battle:** status="Active", battle_info="vs Opponent"
- **After battle:** status="Searching", battle_info="Searching..."
- **OBS overlay:** Should transition from "SCANNING..." → battle display automatically

## Related Code

- Battle lifecycle: `fp/run_battle.py` updates `stream_status.json` on battle end
- Battle tracking: `streaming/state_store.py` manages `active_battles.json`
- OBS integration: BAKUGO polls `/state` endpoint for overlay updates
