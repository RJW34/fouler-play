"""
Launcher for fouler-play bot.  Runs from ANY shell without quoting headaches.

Usage:
    python launch.py 9 3          # 9 battles, 3 concurrent workers
    python launch.py 1            # 1 battle, 1 worker
    python launch.py              # infinite mode, 1 worker
    python launch.py --kill-stale # just kill stale bot processes and exit
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
ENV_FILE = REPO / ".env"


def load_env():
    """Load .env into os.environ (only sets if not already set)."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Remove surrounding quotes if present
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def kill_stale_procs():
    """Kill stale fouler-play python processes (bot + OBS server)."""
    if sys.platform != "win32":
        print("[CLEANUP] Not on Windows, skipping process cleanup.")
        return

    ps_script = r"""
    $targets = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match 'fouler-play' -and
        $_.Name -match '^(py|python).*\.exe$' -and
        ($_.CommandLine -match 'run\.py' -or $_.CommandLine -match 'bot_monitor\.py')
    }
    if ($targets) {
        foreach ($p in $targets) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                Write-Output ("[CLEANUP] Killed PID " + $p.ProcessId)
            } catch {}
        }
    } else {
        Write-Output "[CLEANUP] No stale bot processes found."
    }
    """
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        check=False,
    )


def ensure_obs_server(port=8777):
    """Start the OBS helper server if it isn't already running."""
    import urllib.request
    url = f"http://localhost:{port}/obs-debug"
    try:
        urllib.request.urlopen(url, timeout=2)
        print(f"[OBS] Helper server already running on port {port}.")
        return
    except Exception:
        pass

    print(f"[OBS] Starting OBS helper server on port {port}...")
    subprocess.Popen(
        [sys.executable, "-m", "streaming.serve_obs_page"],
        cwd=str(REPO),
        stdout=open(REPO / "obs_server.log", "w"),
        stderr=open(REPO / "obs_server.err.log", "w"),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    time.sleep(2)
    try:
        urllib.request.urlopen(url, timeout=4)
        print("[OBS] Helper server ready.")
    except Exception as e:
        print(f"[OBS] WARNING: server not reachable: {e}")


def build_bot_args(battles, concurrent):
    """Build the CLI args list for run.py."""
    def env(key, default=""):
        return os.environ.get(key, default)

    run_count = 999999 if battles == 0 else battles

    args = [
        sys.executable, str(REPO / "run.py"),
        "--websocket-uri", env("PS_WEBSOCKET_URI", "wss://sim3.psim.us/showdown/websocket"),
        "--ps-username", env("PS_USERNAME"),
        "--ps-password", env("PS_PASSWORD"),
        "--bot-mode", "search_ladder",
        "--pokemon-format", env("PS_FORMAT", "gen9ou"),
        "--search-time-ms", env("PS_SEARCH_TIME_MS", "3000"),
        "--run-count", str(run_count),
        "--save-replay", env("SAVE_REPLAY", "always"),
        "--log-level", env("BOT_LOG_LEVEL", "INFO"),
        "--max-concurrent-battles", str(concurrent),
        "--search-parallelism", "1",
        "--max-mcts-battles", env("MAX_MCTS_BATTLES", "1"),
    ]

    # Team selection: TEAM_NAMES > TEAM_LIST > TEAM_NAME > default
    team_names = env("TEAM_NAMES")
    team_list = env("TEAM_LIST")
    team_name = env("TEAM_NAME")
    if team_names:
        args += ["--team-names", team_names]
    elif team_list:
        args += ["--team-list", team_list]
    else:
        args += ["--team-name", team_name or "gen9/ou/fat-team-1-stall"]

    if env("BOT_LOG_TO_FILE") == "1":
        args.append("--log-to-file")

    spectator = env("SPECTATOR_USERNAME")
    enable_spec = env("ENABLE_SPECTATOR_INVITES", "1").lower()
    if spectator and enable_spec not in ("0", "false", "no", "off"):
        args += ["--spectator-username", spectator]

    return args


def main():
    parser = argparse.ArgumentParser(description="Launch fouler-play bot")
    parser.add_argument("battles", nargs="?", type=int, default=0,
                        help="Number of battles (0 = infinite)")
    parser.add_argument("concurrent", nargs="?", type=int, default=1,
                        help="Concurrent battle workers")
    parser.add_argument("--kill-stale", action="store_true",
                        help="Just kill stale processes and exit")
    parser.add_argument("--no-obs", action="store_true",
                        help="Don't start OBS helper server")
    parser.add_argument("--detach", action="store_true",
                        help="Launch bot in a new window and return immediately")
    args = parser.parse_args()

    load_env()
    kill_stale_procs()

    if args.kill_stale:
        return

    if not os.environ.get("PS_USERNAME"):
        print("[ERROR] PS_USERNAME not set. Check your .env file.")
        sys.exit(1)
    if not os.environ.get("PS_PASSWORD"):
        print("[ERROR] PS_PASSWORD not set. Check your .env file.")
        sys.exit(1)

    if not args.no_obs:
        port = int(os.environ.get("OBS_SERVER_PORT", "8777"))
        auto_obs = os.environ.get("AUTO_START_OBS_SERVER", "1").lower()
        if auto_obs not in ("0", "false", "no", "off"):
            ensure_obs_server(port)

    bot_args = build_bot_args(args.battles, args.concurrent)

    mode = "infinite" if args.battles == 0 else f"{args.battles} battles"
    print(f"\n[START] {mode}, {args.concurrent} concurrent worker(s)")
    print(f"[START] Team config: {os.environ.get('TEAM_NAMES', os.environ.get('TEAM_NAME', 'default'))}")
    print()

    if args.detach:
        # Launch in a new console window (Windows) or background (Linux)
        if sys.platform == "win32":
            subprocess.Popen(
                bot_args,
                cwd=str(REPO),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            print("[START] Bot launched in new window.")
        else:
            subprocess.Popen(
                bot_args,
                cwd=str(REPO),
                start_new_session=True,
            )
            print("[START] Bot launched in background.")
    else:
        # Run in current terminal (blocking)
        result = subprocess.run(bot_args, cwd=str(REPO))
        print(f"\n[DONE] Exit code: {result.returncode}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
