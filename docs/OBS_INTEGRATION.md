# OBS Battle Monitor Integration Guide

## Overview
The OBS scene "Multi-Battle Monitor" displays 6 concurrent battles by reading from battle data files that are updated in real-time by the Fouler Play bots.

## File Locations

### Linux (ubunztu - BugInTheCode/DEKU)
```
/home/ryan/projects/fouler-play/logs/deku-battle-1.txt
/home/ryan/projects/fouler-play/logs/deku-battle-2.txt
/home/ryan/projects/fouler-play/logs/deku-battle-3.txt
```

### Windows (MAGNETON - ALL CHUNG)
```
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-1.txt
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-2.txt
C:\Users\Ryan\projects\fouler-play\logs\chung-battle-3.txt
```

## File Format

Each battle file is a simple key=value format (6 lines):
```
opponent=OpponentUsername
team=stall
status=battling
elo=1450
turns=12
record=15-8
```

### Field Specifications

| Line | Field | Format | Example | Description |
|------|-------|--------|---------|-------------|
| 1 | opponent | string | `opponent=CoolTrainer123` | Current opponent username, or "Searching..." |
| 2 | team | enum | `team=stall` | Team name: stall, pivot, or dondozo |
| 3 | status | enum | `status=battling` | searching, battling, won, lost |
| 4 | elo | integer | `elo=1450` | Current ELO rating for this team |
| 5 | turns | integer | `turns=12` | Turn count (0 if not battling) |
| 6 | record | W-L | `record=15-8` | Win-loss record for this team |

## Bot Integration Points

### Battle State Machine

**1. IDLE → SEARCHING**
```python
update_battle_file(battle_slot, {
    "opponent": "Searching...",
    "status": "searching",
    "turns": 0
})
```

**2. SEARCHING → BATTLING**
```python
# When battle starts
update_battle_file(battle_slot, {
    "opponent": opponent_username,
    "status": "battling",
    "turns": 1
})
```

**3. During Battle (each turn)**
```python
# Update turn count
update_battle_file(battle_slot, {
    "turns": current_turn
})
```

**4. BATTLING → WON/LOST**
```python
# When battle ends
update_battle_file(battle_slot, {
    "status": "won",  # or "lost"
    "elo": new_elo,
    "record": f"{wins}-{losses}"
})

# Wait 3-5 seconds for visibility, then reset to searching
```

## Implementation Example

```python
import os

def update_battle_file(battle_slot: int, updates: dict):
    """
    Update a battle data file with new information.
    
    Args:
        battle_slot: 1, 2, or 3 (corresponds to team: stall, pivot, dondozo)
        updates: dict with keys: opponent, team, status, elo, turns, record
    """
    # Determine bot name and file path
    if IS_WINDOWS:
        bot_name = "chung"
        base_path = r"C:\Users\Ryan\projects\fouler-play\logs"
    else:
        bot_name = "deku"
        base_path = "/home/ryan/projects/fouler-play/logs"
    
    filepath = os.path.join(base_path, f"{bot_name}-battle-{battle_slot}.txt")
    
    # Read current state
    current = read_battle_file(filepath)
    
    # Merge updates
    current.update(updates)
    
    # Write atomically (write to temp, then rename)
    temp_path = filepath + ".tmp"
    with open(temp_path, 'w') as f:
        f.write(f"opponent={current['opponent']}\\n")
        f.write(f"team={current['team']}\\n")
        f.write(f"status={current['status']}\\n")
        f.write(f"elo={current['elo']}\\n")
        f.write(f"turns={current['turns']}\\n")
        f.write(f"record={current['record']}\\n")
    
    # Atomic replace
    os.replace(temp_path, filepath)

def read_battle_file(filepath: str) -> dict:
    """Read current battle state from file."""
    if not os.path.exists(filepath):
        return {
            "opponent": "Searching...",
            "team": "unknown",
            "status": "searching",
            "elo": 1400,
            "turns": 0,
            "record": "0-0"
        }
    
    data = {}
    with open(filepath, 'r') as f:
        for line in f:
            if '=' in line:
                key, value = line.strip().split('=', 1)
                data[key] = value
    return data
```

## Battle Slot Assignment

| Slot | Team | DEKU File | CHUNG File |
|------|------|-----------|------------|
| 1 | Stall | deku-battle-1.txt | chung-battle-1.txt |
| 2 | Pivot | deku-battle-2.txt | chung-battle-2.txt |
| 3 | Dondozo | deku-battle-3.txt | chung-battle-3.txt |

## OBS Text Source Configuration

Each OBS text source should be configured as:
- **Type:** Text (FreeType 2)
- **Read from file:** ✓
- **Text File:** Path to battle file
- **Chat log mode:** ✓ (auto-refreshes)
- **Use custom text extents:** ✓
- **Wrap:** Word wrap

For extracting specific lines, use OBS filters or Python script sources.

## Testing

Use the TEST-*.txt files to verify OBS layout:
```bash
# Copy test data to active files
cp logs/TEST-deku-battle-*.txt logs/deku-battle-*.txt
```

OBS should update within 1-2 seconds.

## Sync Between Machines

DEKU files (ubunztu) can be synced to MAGNETON for unified OBS display:
- **Option 1:** SMB share from MAGNETON, mount on ubunztu
- **Option 2:** rsync every 5 seconds
- **Option 3:** OBS on MAGNETON reads via network path (\\\\ubunztu\\...)

Recommended: Run OBS on MAGNETON, use network paths for DEKU files.

## Performance Notes

- File updates are cheap (6 lines, <100 bytes)
- Update frequency: 
  - **Searching:** Every 5-10 seconds
  - **Battling:** Every turn (~30-60 seconds)
  - **Won/Lost:** Once, then reset after 3 seconds
- OBS polls files every 1 second (chat log mode)

## Troubleshooting

**OBS not updating:**
1. Check file permissions (readable by OBS user)
2. Verify "Chat log mode" is enabled
3. Check file path in text source
4. Restart OBS scene collection

**Garbled text:**
1. Ensure UTF-8 encoding
2. Use atomic writes (temp file + rename)
3. Avoid writing mid-read (use file locks if needed)

**Sync lag between machines:**
1. Reduce sync interval
2. Check network latency (ping ubunztu → MAGNETON)
3. Consider running all on one machine
