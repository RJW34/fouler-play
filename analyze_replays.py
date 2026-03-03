"""Analyze losses by fetching replay data from Pokemon Showdown."""
import json, re, datetime, urllib.request
from pathlib import Path
from collections import Counter, defaultdict

LOGS_DIR = Path("logs")
cutoff = datetime.datetime(2026, 3, 3, 6, 15)
battle_logs = sorted(LOGS_DIR.glob("battle-gen9ou-*.log"), key=lambda p: p.stat().st_mtime)
batch_logs = [f for f in battle_logs if datetime.datetime.fromtimestamp(f.stat().st_mtime) > cutoff]

# Extract replay URLs and results from logs
losses = []
all_battles = []

for log_path in batch_logs:
    # Read just the first and last parts (skip the massive middle)
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        # Read first 200 lines for setup info
        first_lines = []
        for i, line in enumerate(f):
            first_lines.append(line)
            if i > 200:
                break
    
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        all_lines = f.readlines()
        last_lines = all_lines[-100:]
    
    combined = first_lines + last_lines
    text = ''.join(combined)
    
    winner = ''
    opponent = ''
    team = ''
    replay = ''
    max_turn = 0
    
    for line in combined:
        if 'Battle finished:' in line and 'Winner:' in line:
            m = re.search(r'Winner:\s*(.+)', line)
            if m: winner = m.group(1).strip()
        if 'against:' in line and 'Claimed' in line:
            m = re.search(r'against:\s*(.+)', line)
            if m: opponent = m.group(1).strip()
        if 'with team:' in line:
            m = re.search(r'with team:\s*(.+)', line)
            if m: team = m.group(1).strip()
        if 'replay.pokemonshowdown.com' in line:
            m = re.search(r'(https://replay\.pokemonshowdown\.com/\S+)', line)
            if m: replay = m.group(1)
    
    is_loss = winner and 'npctypebeat' not in winner.lower()
    
    # Also check rotated logs for replays and other info
    for rotated in sorted(log_path.parent.glob(log_path.name + '.*')):
        with open(rotated, 'r', encoding='utf-8', errors='replace') as f:
            rot_lines = f.readlines()
            for line in rot_lines[-50:] + rot_lines[:50]:
                if not winner and 'Battle finished:' in line and 'Winner:' in line:
                    m = re.search(r'Winner:\s*(.+)', line)
                    if m: winner = m.group(1).strip()
                if not replay and 'replay.pokemonshowdown.com' in line:
                    m = re.search(r'(https://replay\.pokemonshowdown\.com/\S+)', line)
                    if m: replay = m.group(1)
    
    entry = {
        'opponent': opponent,
        'team': team,
        'replay': replay,
        'winner': winner,
        'is_loss': is_loss,
        'file': log_path.name,
    }
    all_battles.append(entry)
    if is_loss:
        losses.append(entry)

# Now use the replays to analyze - fetch replay JSON
print(f"Total battles in batch: {len(all_battles)}")
wins = sum(1 for b in all_battles if not b['is_loss'] and b['winner'])
print(f"Wins: {wins}, Losses: {len(losses)}")
print()

# Team breakdown
team_w = Counter()
team_l = Counter()
for b in all_battles:
    if not b['winner']:
        continue
    if b['is_loss']:
        team_l[b['team']] += 1
    else:
        team_w[b['team']] += 1

print("=== PER TEAM ===")
for team in sorted(set(list(team_w.keys()) + list(team_l.keys()))):
    w = team_w[team]
    l = team_l[team]
    total = w + l
    wr = w / total * 100 if total else 0
    print(f"  {team}: {w}W-{l}L ({wr:.0f}%)")

print()
print("=== LOSS REPLAYS ===")
for i, loss in enumerate(losses):
    print(f"  Loss {i+1}: vs {loss['opponent']:20} | {loss['team']:25} | {loss['replay']}")

# Fetch and analyze top losses from replays
print("\n=== FETCHING REPLAY DATA FOR LOSS ANALYSIS ===")

loss_patterns = Counter()
loss_details = []

for i, loss in enumerate(losses):
    replay_url = loss['replay']
    if not replay_url:
        print(f"  Loss {i+1}: No replay URL, skipping")
        continue
    
    # PS replay JSON endpoint
    json_url = replay_url + '.json'
    try:
        req = urllib.request.Request(json_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            replay_data = json.loads(resp.read())
    except Exception as e:
        print(f"  Loss {i+1}: Failed to fetch replay: {e}")
        continue
    
    log_text = replay_data.get('log', '')
    log_lines = log_text.split('\n')
    
    # Parse the replay log
    our_fainted = []
    their_fainted = []
    our_pokemon_hp = {}
    their_pokemon_hp = {}
    current_turn = 0
    our_moves = []
    our_switches = []
    their_moves = []
    
    for line in log_lines:
        # Turn
        m = re.match(r'\|turn\|(\d+)', line)
        if m:
            current_turn = int(m.group(1))
        
        # Our faint (we are p1)
        if '|faint|p1a:' in line:
            m = re.search(r'\|faint\|p1a:\s*(.+)', line)
            if m: our_fainted.append((current_turn, m.group(1).strip()))
        
        # Their faint
        if '|faint|p2a:' in line:
            m = re.search(r'\|faint\|p2a:\s*(.+)', line)
            if m: their_fainted.append((current_turn, m.group(1).strip()))
        
        # Our moves
        if '|move|p1a:' in line:
            m = re.search(r'\|move\|p1a:\s*([^|]+)\|([^|]+)', line)
            if m: our_moves.append((current_turn, m.group(1).strip(), m.group(2).strip()))
        
        # Their moves
        if '|move|p2a:' in line:
            m = re.search(r'\|move\|p2a:\s*([^|]+)\|([^|]+)', line)
            if m: their_moves.append((current_turn, m.group(1).strip(), m.group(2).strip()))
        
        # Our switches
        if '|switch|p1a:' in line:
            m = re.search(r'\|switch\|p1a:\s*([^|]+)', line)
            if m: our_switches.append((current_turn, m.group(1).strip()))
    
    detail = {
        'opponent': loss['opponent'],
        'team': loss['team'],
        'turns': current_turn,
        'our_fainted': our_fainted,
        'their_fainted': their_fainted,
        'our_moves': our_moves,
        'our_switches': our_switches,
        'their_moves': their_moves,
        'replay': replay_url,
    }
    loss_details.append(detail)
    
    print(f"\n--- Loss {i+1}: vs {loss['opponent']} ({loss['team']}, {current_turn} turns) ---")
    print(f"  Our KOs: {len(our_fainted)} | Their KOs: {len(their_fainted)}")
    print(f"  Our fainted: {', '.join(f'{mon} (T{t})' for t, mon in our_fainted)}")
    print(f"  Their fainted: {', '.join(f'{mon} (T{t})' for t, mon in their_fainted)}")
    
    # Detect patterns
    # 1. Got swept - 3+ mons fainted in last 5 turns
    late_faints = [f for f in our_fainted if f[0] > current_turn - 5]
    if len(late_faints) >= 3:
        loss_patterns['got_swept_late'] += 1
    
    # 2. Sweep by specific mon
    if their_moves:
        last_5_turn_moves = [m for m in their_moves if m[0] > current_turn - 5]
        sweeper_mons = Counter(m[1] for m in last_5_turn_moves)
        for mon, count in sweeper_mons.most_common(1):
            if count >= 3:
                loss_patterns[f'swept_by_mon'] += 1
                print(f"  ** SWEPT BY: {mon} in final turns")
    
    # 3. Failed to KO enough - fewer than 3 their mons fainted
    if len(their_fainted) < 3:
        loss_patterns['failed_to_ko'] += 1
    
    # 4. Close game - 4+ of their mons fainted
    if len(their_fainted) >= 4:
        loss_patterns['close_game'] += 1
    
    # 5. Switch-heavy play
    switch_count = len(our_switches)
    move_count = len(our_moves)
    if switch_count > move_count * 0.6 and switch_count > 10:
        loss_patterns['too_many_switches'] += 1
        print(f"  ** EXCESSIVE SWITCHING: {switch_count} switches vs {move_count} moves")
    
    # 6. Detect what moves we used most
    our_move_names = Counter(m[2] for m in our_moves)
    recovery_moves = sum(our_move_names.get(m, 0) for m in ['Recover', 'Soft-Boiled', 'Roost', 'Rest', 'Softboiled'])
    if recovery_moves > move_count * 0.3:
        loss_patterns['too_passive_recovery'] += 1
        print(f"  ** PASSIVE: {recovery_moves}/{move_count} moves were recovery ({recovery_moves/move_count*100:.0f}%)")
    
    # Show move distribution
    print(f"  Our moves ({move_count} total, {switch_count} switches): {dict(our_move_names.most_common(8))}")
    
    # Their dangerous moves
    their_move_names = Counter(m[2] for m in their_moves)
    print(f"  Their moves: {dict(their_move_names.most_common(8))}")

print(f"\n{'='*80}")
print("AGGREGATE LOSS PATTERNS:")
for pattern, count in loss_patterns.most_common():
    pct = count / len(losses) * 100
    print(f"  [{count}/{len(losses)}] ({pct:.0f}%) {pattern}")
