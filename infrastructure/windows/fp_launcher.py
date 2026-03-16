#!/usr/bin/env python3
"""
Fouler-Play Direct Launcher
Launches run.py as a fully detached Windows process (no console, no parent signals)
and writes the .pids/bot_main.pid file for hermes_health.py.
"""
import subprocess
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(r'D:\Projects with Claude\fouler-play')
sys.path.insert(0, str(REPO))

# Load .env manually
env_file = REPO / '.env'
env_vars = dict(os.environ)
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            env_vars[k.strip()] = v.strip()

def get(key, default=''):
    return env_vars.get(key, default)

# Build run.py command
py_exe = sys.executable  # Use same Python that runs this launcher
cmd = [
    py_exe, '-u', str(REPO / 'run.py'),
    '--websocket-uri', get('PS_WEBSOCKET_URI', 'wss://sim3.psim.us/showdown/websocket'),
    '--ps-username', get('PS_USERNAME'),
    '--ps-password', get('PS_PASSWORD'),
    '--bot-mode', 'search_ladder',
    '--pokemon-format', get('PS_FORMAT', 'gen9ou'),
    '--search-time-ms', get('PS_SEARCH_TIME_MS', '3000'),
    '--run-count', '999999',
    '--save-replay', 'always',
    '--log-level', get('BOT_LOG_LEVEL', 'DEBUG'),
    '--max-concurrent-battles', get('MAX_CONCURRENT_BATTLES', '3'),
    '--search-parallelism', get('SEARCH_PARALLELISM', '2'),
    '--max-mcts-battles', get('MAX_MCTS_BATTLES', '3'),
    '--log-to-file'
]

team_names = get('TEAM_NAMES')
if team_names:
    cmd.extend(['--team-names', team_names])

# Windows flags for fully detached process
DETACHED_PROCESS       = 0x00000008
CREATE_NO_WINDOW       = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

print(f'[LAUNCHER] Starting run.py...')
print(f'[LAUNCHER] Account: {get("PS_USERNAME")}')
print(f'[LAUNCHER] Format: {get("PS_FORMAT", "gen9ou")}')
print(f'[LAUNCHER] Teams: {team_names}')

proc = subprocess.Popen(
    cmd,
    cwd=str(REPO),
    creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
    env=env_vars
)

# Write PID file
pid_dir = REPO / '.pids'
pid_dir.mkdir(exist_ok=True)
pid_file = pid_dir / 'bot_main.pid'
pid_data = {
    'pid': proc.pid,
    'name': 'bot_main',
    'started_at': time.time(),
    'command': ' '.join(cmd)
}
pid_file.write_text(json.dumps(pid_data), encoding='utf-8')

print(f'[LAUNCHER] run.py PID={proc.pid}')
print(f'[LAUNCHER] PID file written: {pid_file}')
print(f'[LAUNCHER] Process fully detached from console.')

# Quick sanity check
import time as _t
_t.sleep(2)
try:
    os.kill(proc.pid, 0)
    print(f'[LAUNCHER] Confirmed: PID {proc.pid} is alive after 2s.')
except Exception as e:
    print(f'[LAUNCHER] WARNING: PID check failed: {e}')
