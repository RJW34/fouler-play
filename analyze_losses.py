"""Deep-dive into loss patterns from the 30-battle batch."""
import re, glob, datetime
from pathlib import Path
from collections import Counter, defaultdict

LOGS_DIR = Path("logs")
cutoff = datetime.datetime(2026, 3, 3, 6, 15)
battle_logs = sorted(LOGS_DIR.glob("battle-gen9ou-*.log"), key=lambda p: p.stat().st_mtime)
batch_logs = [f for f in battle_logs if datetime.datetime.fromtimestamp(f.stat().st_mtime) > cutoff]

losses = []
for log_path in batch_logs:
    text = log_path.read_text(encoding='utf-8', errors='replace')
    lines = text.split('\n')
    
    # Is this a loss?
    winner = ''
    opponent = ''
    team = ''
    max_turn = 0
    replay = ''
    
    for line in lines:
        if 'Battle finished:' in line and 'Winner:' in line:
            m = re.search(r'Winner:\s*(.+)', line)
            if m: winner = m.group(1).strip()
        if 'against:' in line and 'Claimed' in line:
            m = re.search(r'against:\s*(.+)', line)
            if m: opponent = m.group(1).strip()
        if 'with team:' in line:
            m = re.search(r'with team:\s*(.+)', line)
            if m: team = m.group(1).strip()
        tm = re.search(r'Turn:\s+(\d+)', line)
        if tm:
            max_turn = max(max_turn, int(tm.group(1)))
        if 'replay.pokemonshowdown.com' in line:
            m = re.search(r'(https://replay\.pokemonshowdown\.com/\S+)', line)
            if m: replay = m.group(1)
    
    if 'npctypebeat' not in winner.lower() and winner:
        losses.append({
            'file': log_path.name,
            'path': log_path,
            'opponent': opponent,
            'team': team,
            'turns': max_turn,
            'replay': replay,
            'text': text,
            'lines': lines,
        })

print(f"Analyzing {len(losses)} losses...\n")

# For each loss, extract the key battle narrative
for i, loss in enumerate(losses):
    lines = loss['lines']
    print(f"{'='*80}")
    print(f"LOSS {i+1}: vs {loss['opponent']} | {loss['team']} | {loss['turns']} turns")
    print(f"Replay: {loss['replay']}")
    
    # Extract turn-by-turn summary: moves, switches, faints, damage
    turn_events = []
    current_turn = 0
    our_fainted = []
    their_fainted = []
    our_pokemon_active = ''
    their_pokemon_active = ''
    
    for line in lines:
        # Turn marker
        tm = re.search(r'\|turn\|(\d+)', line)
        if tm:
            current_turn = int(tm.group(1))
            continue
        
        # Our faints
        if '|faint|p1a:' in line:
            m = re.search(r'\|faint\|p1a:\s*(.+)', line)
            if m: our_fainted.append(f"T{current_turn}: {m.group(1).strip()}")
        
        # Their faints
        if '|faint|p2a:' in line:
            m = re.search(r'\|faint\|p2a:\s*(.+)', line)
            if m: their_fainted.append(f"T{current_turn}: {m.group(1).strip()}")
    
    print(f"Our mons fainted ({len(our_fainted)}):")
    for f in our_fainted:
        print(f"  {f}")
    print(f"Their mons fainted ({len(their_fainted)}):")
    for f in their_fainted:
        print(f"  {f}")
    
    # Key decision moments: look at Choice lines in last half of battle
    mid_turn = loss['turns'] // 2
    choices = []
    forced_lines = []
    for line in lines:
        if 'Choice:' in line and 'INFO' in line:
            m = re.search(r'Choice:\s*(.+?)(?:\s*\(decided|\s*$)', line)
            if m: choices.append(m.group(1).strip())
        if 'FORCED LINE' in line:
            m = re.search(r'FORCED LINE:\s*(.+)', line)
            if m: forced_lines.append(m.group(1).strip()[:80])
    
    # Show last 10 choices
    print(f"Last 10 choices:")
    for c in choices[-10:]:
        print(f"  {c}")
    
    # Show forced lines
    if forced_lines:
        print(f"Forced lines used ({len(forced_lines)}):")
        for fl in forced_lines[-5:]:
            print(f"  {fl}")
    
    # Look for switch loops (same pokemon switched in 3+ times in last 15 turns)
    switch_pattern = Counter()
    for line in lines:
        m = re.search(r'\|switch\|p1a:\s*([^|]+)', line)
        if m:
            switch_pattern[m.group(1).strip().split('|')[0]] += 1
    
    excessive = {k: v for k, v in switch_pattern.items() if v >= 5}
    if excessive:
        print(f"Excessive switches:")
        for mon, count in sorted(excessive.items(), key=lambda x: -x[1]):
            print(f"  {mon}: switched in {count} times")
    
    # Look for key errors
    errors = [l for l in lines if 'Invalid choice' in l or 'error' in l.lower() and 'ERROR' in l]
    if errors:
        print(f"Errors found: {len(errors)}")
        for e in errors[-3:]:
            print(f"  {e.strip()[:120]}")
    
    print()

# Summary patterns
print(f"{'='*80}")
print("AGGREGATE LOSS PATTERNS:")
print(f"{'='*80}")

# Turn length distribution for losses
short = sum(1 for l in losses if l['turns'] < 20)
medium = sum(1 for l in losses if 20 <= l['turns'] < 40)
long_ = sum(1 for l in losses if l['turns'] >= 40)
print(f"Game length: {short} short (<20t), {medium} medium (20-40t), {long_} long (40+t)")

# Team analysis
team_losses = Counter(l['team'] for l in losses)
print(f"\nLosses by team:")
for team, count in team_losses.most_common():
    print(f"  {team}: {count} losses")

# Faint analysis - are we losing all 6 or getting close?
print(f"\nDetailed faint counts per loss (who gets swept?):")
for i, loss in enumerate(losses):
    our_faints = loss['text'].count('|faint|p1a:')
    their_faints = loss['text'].count('|faint|p2a:')
    print(f"  Loss {i+1} vs {loss['opponent']:20} ({loss['team']:25}): Us={our_faints} fainted, Them={their_faints} fainted | {loss['turns']}t")
