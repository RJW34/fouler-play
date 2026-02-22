#!/usr/bin/env python3
"""
Analyze recent losses to identify structural decision-making problems
"""

import json
import re
import glob
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# Battle IDs from recent losses
LOSSES_TO_ANALYZE = [
    "battle-gen9ou-2540341788",
    "battle-gen9ou-2540351598",
    "battle-gen9ou-2540357415",
    "battle-gen9ou-2540368572",
    "battle-gen9ou-2540374888",
    "battle-gen9ou-2540377938",
    "battle-gen9ou-2540380579",
    "battle-gen9ou-2540386458",
    "battle-gen9ou-2540389208",
    "battle-gen9ou-2540391282",
]

def find_log_file(battle_id):
    """Find the main log file for a battle"""
    pattern = f"logs/{battle_id}*.log"
    files = glob.glob(pattern)
    # Get the main .log file (not .log.1, .log.2, etc.)
    for f in files:
        if f.endswith('.log') and not re.search(r'\.log\.\d+$', f):
            return f
    return None

def extract_battle_info(log_path):
    """Extract key info from battle log"""
    info = {
        'battle_id': None,
        'opponent_team': [],
        'our_team': None,
        'turns': [],
        'decisions': [],
        'critical_moments': [],
        'errors': [],
    }
    
    with open(log_path, 'r', errors='ignore') as f:
        content = f.read()
    
    # Extract team info
    team_match = re.search(r'Team file:\s*(\S+)', content)
    if team_match:
        info['our_team'] = team_match.group(1)
    
    # Look for opponent's team
    poke_lines = re.findall(r'\|poke\|p2\|([^,\|]+)', content)
    info['opponent_team'] = list(set(poke_lines))
    
    # Extract turns and decisions
    turn_pattern = r'\|turn\|(\d+)'
    turns = re.findall(turn_pattern, content)
    info['turns'] = [int(t) for t in turns]
    
    # Look for decision logs
    decision_pattern = r'Decision for turn (\d+):\s*([^\n]+)'
    decisions = re.findall(decision_pattern, content)
    info['decisions'] = decisions
    
    # Look for forced switches or critical moments
    switch_pattern = r'Forced to switch'
    if re.search(switch_pattern, content):
        info['critical_moments'].append('Forced switch detected')
    
    # Look for errors
    error_pattern = r'ERROR|Exception|Traceback'
    if re.search(error_pattern, content, re.IGNORECASE):
        info['errors'].append('Error found in log')
    
    # Look for game state issues
    if 'damaged by hazards' in content:
        info['critical_moments'].append('Hazard damage accumulation')
    
    if 'knocked out' in content.lower() or 'fainted' in content.lower():
        faint_count = len(re.findall(r'fainted|knocked out', content, re.IGNORECASE))
        info['critical_moments'].append(f'Pokemon fainted: {faint_count} times')
    
    return info

def analyze_decision_quality(log_path):
    """Look for questionable decisions in logs"""
    issues = []
    
    with open(log_path, 'r', errors='ignore') as f:
        content = f.read()
    
    # Check for repeated switches (pivot loop)
    if content.count('switch') > 20:
        issues.append('Excessive switching detected (possible pivot confusion)')
    
    # Check for not setting hazards when should
    if 'Stealth Rock' not in content and 'Spikes' not in content:
        issues.append('No hazard setup detected (hazard team failing to set hazards)')
    
    # Check for letting opponent set up
    setup_moves = ['Swords Dance', 'Nasty Plot', 'Calm Mind', 'Dragon Dance']
    for move in setup_moves:
        if content.count(move) > 3:
            issues.append(f'Opponent allowed to set up with {move} multiple times')
    
    # Check for bad switches into obvious threats
    # This would need more context parsing
    
    return issues

def main():
    print("ANALYZING RECENT LOSSES - STRUCTURAL DIAGNOSIS")
    print("=" * 70)
    
    results = {}
    
    for battle_id in LOSSES_TO_ANALYZE:
        print(f"\n\nAnalyzing {battle_id}...")
        log_path = find_log_file(battle_id)
        
        if not log_path:
            print(f"  ❌ Log file not found")
            continue
        
        print(f"  ✓ Found log: {log_path}")
        
        info = extract_battle_info(log_path)
        issues = analyze_decision_quality(log_path)
        
        results[battle_id] = {
            'info': info,
            'issues': issues,
        }
        
        print(f"  Team: {info['our_team']}")
        print(f"  Opponent: {', '.join(info['opponent_team'][:6])}")
        print(f"  Turns: {max(info['turns']) if info['turns'] else 'Unknown'}")
        print(f"  Critical moments: {len(info['critical_moments'])}")
        if issues:
            print(f"  Issues found:")
            for issue in issues:
                print(f"    - {issue}")
    
    # Pattern analysis
    print("\n\n" + "=" * 70)
    print("PATTERN ANALYSIS")
    print("=" * 70)
    
    team_losses = defaultdict(int)
    all_issues = defaultdict(int)
    
    for battle_id, data in results.items():
        team = data['info']['our_team']
        if team:
            team_losses[team] += 1
        for issue in data['issues']:
            all_issues[issue] += 1
    
    print("\nLosses by team:")
    for team, count in sorted(team_losses.items(), key=lambda x: x[1], reverse=True):
        print(f"  {team}: {count} losses")
    
    print("\nCommon issues:")
    for issue, count in sorted(all_issues.items(), key=lambda x: x[1], reverse=True):
        print(f"  [{count}x] {issue}")
    
    # Save detailed results
    with open('loss_analysis_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print("\n\n✓ Detailed results saved to loss_analysis_results.json")

if __name__ == '__main__':
    main()
