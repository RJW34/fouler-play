"""Analyze the 30-battle batch results for patterns."""
import json, os, re, glob
from collections import defaultdict, Counter
from pathlib import Path

LOGS_DIR = Path("logs")
TRACES_DIR = LOGS_DIR / "decision_traces"

# Get battle logs from this session (created after 2026-03-03 05:37 EST)
# We'll filter by the batch start time
import datetime

# Find all recent battle logs
battle_logs = sorted(LOGS_DIR.glob("battle-gen9ou-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)

# The batch started around 05:37 EST = we use logs from today's session
# Filter to logs modified after 06:15 (when we restarted with drain disabled)
cutoff = datetime.datetime(2026, 3, 3, 6, 15)
batch_logs = [f for f in battle_logs if datetime.datetime.fromtimestamp(f.stat().st_mtime) > cutoff]
print(f"Found {len(batch_logs)} battle logs from this batch\n")

# Analyze each battle
results = []
for log_path in sorted(batch_logs, key=lambda p: p.stat().st_mtime):
    lines = log_path.read_text(encoding='utf-8', errors='replace').split('\n')
    
    result = {}
    result['file'] = log_path.name
    
    # Find opponent, winner, team, turn count
    for line in lines:
        if 'Claimed pending battle' in line and 'against:' in line:
            m = re.search(r'against:\s*(.+)', line)
            if m:
                result['opponent'] = m.group(1).strip()
        if 'Battle finished:' in line and 'Winner:' in line:
            m = re.search(r'Winner:\s*(.+)', line)
            if m:
                result['winner'] = m.group(1).strip()
        if 'Lost with team:' in line or 'Won with team:' in line:
            m = re.search(r'with team:\s*(.+)', line)
            if m:
                result['team'] = m.group(1).strip()
        if 'lost due to inactivity' in line:
            result['inactivity'] = True
        if re.match(r'.*Turn:\s+(\d+)', line):
            m = re.search(r'Turn:\s+(\d+)', line)
            if m:
                result['max_turn'] = max(result.get('max_turn', 0), int(m.group(1)))
        if 'rating:' in line and 'npctypebeat' in line:
            m = re.search(r'<strong>(\d+)</strong>', line)
            if m:
                result['elo_after'] = int(m.group(1))
    
    # Determine W/L
    winner = result.get('winner', '')
    if 'npctypebeat' in winner.lower():
        result['result'] = 'WIN'
    elif winner:
        result['result'] = 'LOSS'
    else:
        result['result'] = 'UNKNOWN'
    
    results.append(result)

# Summary
wins = [r for r in results if r['result'] == 'WIN']
losses = [r for r in results if r['result'] == 'LOSS']
print(f"=== OVERALL: {len(wins)}W-{len(losses)}L ({len(wins)/(len(wins)+len(losses))*100:.1f}% WR) ===\n")

# Per team breakdown
teams = defaultdict(lambda: {'w': 0, 'l': 0, 'turns_w': [], 'turns_l': []})
for r in results:
    team = r.get('team', 'unknown')
    if r['result'] == 'WIN':
        teams[team]['w'] += 1
        teams[team]['turns_w'].append(r.get('max_turn', 0))
    elif r['result'] == 'LOSS':
        teams[team]['l'] += 1
        teams[team]['turns_l'].append(r.get('max_turn', 0))

print("=== PER TEAM ===")
for team, stats in sorted(teams.items()):
    total = stats['w'] + stats['l']
    wr = stats['w'] / total * 100 if total > 0 else 0
    avg_turns_w = sum(stats['turns_w']) / len(stats['turns_w']) if stats['turns_w'] else 0
    avg_turns_l = sum(stats['turns_l']) / len(stats['turns_l']) if stats['turns_l'] else 0
    print(f"  {team}: {stats['w']}W-{stats['l']}L ({wr:.0f}%) | Avg turns: W={avg_turns_w:.0f} L={avg_turns_l:.0f}")

# ELO tracking
elos = [r.get('elo_after') for r in results if r.get('elo_after')]
if elos:
    print(f"\n=== ELO: Started ~{elos[0]} -> Ended {elos[-1]} ===")

# Analyze losses in detail
print("\n=== LOSS DETAILS ===")
for r in losses:
    opp = r.get('opponent', '?')
    team = r.get('team', '?')
    turns = r.get('max_turn', '?')
    inact = ' [INACTIVITY]' if r.get('inactivity') else ''
    elo = r.get('elo_after', '?')
    print(f"  vs {opp:20} | {team:25} | {turns} turns | ELO->{elo}{inact}")

# Now analyze decision traces for losses
print("\n=== LOSS PATTERN ANALYSIS ===")

# For each loss, read the last few turns of the battle log to identify what went wrong
loss_patterns = Counter()
loss_examples = defaultdict(list)

for r in losses:
    log_path = LOGS_DIR / r['file']
    lines = log_path.read_text(encoding='utf-8', errors='replace').split('\n')
    
    # Look for key patterns in the last 100 lines
    tail = '\n'.join(lines[-200:])
    
    # Pattern: Invalid choice
    if 'Invalid choice' in tail or 'invalid choice' in tail:
        loss_patterns['invalid_choice'] += 1
        loss_examples['invalid_choice'].append(r.get('opponent', '?'))
    
    # Pattern: Inactivity timeout
    if 'lost due to inactivity' in tail:
        loss_patterns['inactivity_timeout'] += 1
        loss_examples['inactivity_timeout'].append(r.get('opponent', '?'))
    
    # Pattern: Got swept (multiple KOs in last turns)
    ko_count = tail.count('|faint|p1a:')
    if ko_count >= 3:
        loss_patterns['got_swept'] += 1
        loss_examples['got_swept'].append(r.get('opponent', '?'))
    
    # Pattern: Long stall game lost
    if r.get('max_turn', 0) > 50:
        loss_patterns['long_game_loss'] += 1
        loss_examples['long_game_loss'].append(f"{r.get('opponent', '?')} ({r.get('max_turn', '?')} turns)")
    
    # Pattern: Short game loss (got blitzed)
    if r.get('max_turn', 0) < 15 and r.get('max_turn', 0) > 0:
        loss_patterns['short_game_loss'] += 1
        loss_examples['short_game_loss'].append(f"{r.get('opponent', '?')} ({r.get('max_turn', '?')} turns)")
    
    # Look for specific decision issues
    # Switch loop detection
    switch_lines = [l for l in lines[-200:] if 'Choice: switch' in l]
    if len(switch_lines) > 6:
        loss_patterns['excessive_switching'] += 1
        loss_examples['excessive_switching'].append(r.get('opponent', '?'))
    
    # Look for forced lines that went wrong
    forced = [l for l in lines[-200:] if 'FORCED LINE' in l]
    if forced:
        for f in forced[-3:]:
            m = re.search(r'FORCED LINE:\s*(.+)', f)
            if m:
                loss_patterns[f'forced_line: {m.group(1)[:60]}'] += 1

for pattern, count in loss_patterns.most_common(10):
    examples = ', '.join(loss_examples.get(pattern, [])[:3])
    print(f"  [{count}x] {pattern}")
    if examples:
        print(f"        Examples: {examples}")

# Deep dive: read the actual final turns of the 3 most recent losses
print("\n=== TOP 3 LOSS REPLAYS (final moments) ===")
for i, r in enumerate(losses[-3:]):
    log_path = LOGS_DIR / r['file']
    lines = log_path.read_text(encoding='utf-8', errors='replace').split('\n')
    
    # Find move/switch/faint lines in the last portion
    print(f"\n--- Loss #{i+1}: vs {r.get('opponent', '?')} ({r.get('team', '?')}, {r.get('max_turn', '?')} turns) ---")
    
    action_lines = []
    for line in lines[-300:]:
        if any(x in line for x in ['|move|', '|switch|', '|faint|', '|turn|', 'Choice:', 'FORCED LINE', '-damage|', '|-heal|']):
            # Clean up the line
            clean = line.strip()
            if clean.startswith('DEBUG') or clean.startswith('INFO'):
                clean = re.sub(r'^(DEBUG|INFO)\s+', '', clean)
            if len(clean) > 120:
                clean = clean[:120] + '...'
            action_lines.append(clean)
    
    # Show last 30 action lines
    for line in action_lines[-30:]:
        print(f"    {line}")
