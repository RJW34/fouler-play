#!/usr/bin/env python3
"""
Fouler Play Bot Monitor - Simplified Version for Quick Recovery
Monitors bot output and posts battle results to Discord (analysis disabled)
"""

import asyncio
import re
import sys
import json
import time
import signal
import subprocess
import atexit
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import os
import logging

# Configure logging
logging.basicConfig(
    filename='bot_monitor_debug.log',
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    filemode='w',
    encoding='utf-8'
)
logging.info("Bot Monitor starting up...")

# Load environment variables
load_dotenv()

# Process tracking
PID_DIR = Path(__file__).parent / ".pids"
PID_FILE = PID_DIR / "bot_monitor.pid"
BOT_MAIN_PID_FILE = PID_DIR / "bot_main.pid"
DRAIN_FILE = PID_DIR / "drain.request"

# Discord webhook URLs (from .env)
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")  # For project updates
DISCORD_BATTLES_WEBHOOK = os.getenv("DISCORD_BATTLES_WEBHOOK_URL")  # For battle notifications

# Patterns to detect in bot output
BATTLE_START_PATTERN = re.compile(
    r'(?:Initialized|Found pending|(?<!un)registered|Joining|Battle started).*?'
    r'(?:'
    r'(battle-[a-z0-9-]+-\d+)(?:.*?against:\s*(.+)|.*?vs\s+(.+))'  # case 1: ID then opponent
    r'|'
    r'(?:vs\s+(.*?)\s+)?(battle-[a-z0-9-]+-\d+)'                    # case 2: vs opponent then ID
    r')', re.IGNORECASE)
BATTLE_END_PATTERN = re.compile(r'(Won|Lost) with team: (.+)')
REPLAY_PATTERN = re.compile(r'https://replay\.pokemonshowdown\.com/([\w-]+)')
ELO_PATTERN = re.compile(r'W: (\d+)\s+L: (\d+)')
WINNER_PATTERN = re.compile(r'(?:Battle finished: (battle-[\w-]+-\d+)\s+)?Winner: (.+)', re.IGNORECASE)
BATTLE_TAG_PATTERN = re.compile(r'battle-[a-z0-9]+-\d+')
WORKER_PATTERN = re.compile(r'Battle worker (\d+) started')


class BattleState:
    """State for a single battle"""
    def __init__(self, battle_id, opponent, start_time):
        self.battle_id = battle_id
        self.opponent = opponent
        self.start_time = start_time
        self.result = None  # "won", "lost", "tie"
        self.replay_url = None


class BotMonitor:
    BATCH_SIZE = 9  # Report every N completed games

    def __init__(self):
        self._write_self_pid()
        atexit.register(self._cleanup_self_pid)
        self.process = None
        self.wins = 0
        self.losses = 0
        # Track multiple active battles
        self.active_battles = {}  # battle_id -> BattleState
        self.seen_battle_ids = set()  # Track ALL battles ever seen (prevents duplicates)
        self.last_winner = None  # for associating winner with replay
        self.last_battle_id = None  # for associating events
        self.finished_battles = {}  # battle_id -> (opponent, result) for completed battles awaiting replay
        self.posted_replays = set()  # Track posted replays to avoid duplicates
        self.num_workers = 0
        # Batch reporting state
        self.batch_results = []  # list of (opponent, result, replay_url)
        self.batch_wins_count = 0
        self.batch_losses_count = 0
        # Drain mode tracking
        self.drain_requested = False

    def _write_self_pid(self):
        try:
            data = {
                "pid": os.getpid(),
                "name": "bot_monitor",
                "started_at": time.time(),
                "command": " ".join(sys.argv),
            }
            with open(PID_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[MONITOR] Failed to write PID file: {e}")

    def _cleanup_self_pid(self):
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass
        self._cleanup_bot_main_pid()

    def _cleanup_bot_main_pid(self):
        try:
            if BOT_MAIN_PID_FILE.exists():
                BOT_MAIN_PID_FILE.unlink()
        except Exception:
            pass

    async def _shutdown_child(self, reason="shutdown", timeout_sec=30):
        if not self.process or self.process.returncode is not None:
            return
        try:
            await self.request_drain(reason)
        except Exception:
            pass
        try:
            await asyncio.wait_for(self.process.wait(), timeout=timeout_sec)
            return
        except asyncio.TimeoutError:
            pass
        try:
            self.process.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
            return
        except asyncio.TimeoutError:
            pass
        try:
            self.process.kill()
        except Exception:
            pass
        try:
            await self.process.wait()
        except Exception:
            pass

    async def shutdown(self, reason="shutdown"):
        await self._shutdown_child(reason=reason)
        self._cleanup_bot_main_pid()

    async def send_discord_message(self, message, channel="project"):
        """Send instant notification via Discord webhook"""
        if channel == "battles":
            webhook_url = DISCORD_BATTLES_WEBHOOK
        else:
            webhook_url = DISCORD_WEBHOOK

        if not webhook_url:
            safe_message = message
            try:
                safe_message.encode("cp1252")
            except UnicodeEncodeError:
                safe_message = message.encode("ascii", "replace").decode("ascii")
            print(f"[MONITOR] No webhook configured: {safe_message[:50]}...")
            return

        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"content": message}
            try:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status == 204:
                        print(f"[MONITOR] Sent to {channel}: {message[:50]}...")
                    else:
                        print(f"[MONITOR] Failed ({resp.status}): {message[:50]}...")
            except Exception as e:
                print(f"[MONITOR] Error sending message: {e}")

    async def request_drain(self, reason="operator"):
        """Ask the bot process to enter drain mode (no new battles)."""
        if not self.process or self.process.returncode is not None:
            print("[MONITOR] No running bot process to drain.")
            return False
        if self.drain_requested:
            print("[MONITOR] Drain already requested.")
            return False

        self.drain_requested = True
        try:
            payload = {
                "pid": self.process.pid,
                "requested_at": time.time(),
                "reason": reason,
            }
            DRAIN_FILE.write_text(json.dumps(payload))
        except Exception as e:
            print(f"[MONITOR] Failed to write drain file: {e}")
        try:
            if sys.platform == "win32":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGTERM)
            print(f"[MONITOR] Drain signal sent ({reason}).")
            return True
        except Exception as e:
            print(f"[MONITOR] Failed to send drain signal: {e}")
            return False

    async def flush_batch_report(self):
        """Post a summary of the last BATCH_SIZE games to Discord."""
        if not self.batch_results:
            return

        wins = self.batch_wins_count
        losses = self.batch_losses_count
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0

        msg = f"📊 **Batch Report ({total} games):** {wins}W - {losses}L ({wr:.0f}% WR)\\n"
        msg += f"**Overall Record:** {self.wins}W - {self.losses}L\\n\\n"

        # List results
        for opp, result, replay in self.batch_results:
            emoji = "✅" if result == "won" else "💀" if result == "lost" else "🤝"
            replay_link = f" — [replay]({replay})" if replay else ""
            msg += f"{emoji} vs {opp}{replay_link}\\n"

        msg += f"\\n🔍 **Analysis disabled during upgrade**"

        await self.send_discord_message(msg, channel="battles")

        # Reset batch
        self.batch_results = []
        self.batch_wins_count = 0
        self.batch_losses_count = 0

    def record_batch_result(self, opponent, result, replay_url=None):
        """Add a game result to the current batch."""
        self.batch_results.append((opponent, result, replay_url))
        if result == "won":
            self.batch_wins_count += 1
        elif result == "lost":
            self.batch_losses_count += 1

    async def monitor_output(self, stream):
        """Monitor bot output line by line"""
        try:
            while True:
                try:
                    line = await stream.readline()
                    if not line:
                        break
                    
                    line = line.decode('utf-8', errors='replace').strip()
                    logging.debug(f"Line: {line}")
                    try:
                        print(line)  # Echo to stdout
                    except UnicodeEncodeError:
                        print(line.encode("ascii", "replace").decode("ascii"))
                except asyncio.CancelledError:
                    logging.info("Monitor output cancelled - shutting down")
                    break
        except Exception as e:
            logging.error(f"Monitor output error: {e}")
            print(f"[MONITOR] Monitor error: {e}")

            # Track which battle is currently outputting messages
            if "Received for battle" in line:
                match = BATTLE_TAG_PATTERN.search(line)
                if match:
                    current_battle = match.group(0)
                    if current_battle in self.active_battles:
                        self.last_battle_id = current_battle

            # Detect ELO stats (format: "W: 123  L: 45")
            match = ELO_PATTERN.search(line)
            if match:
                wins = int(match.group(1))
                losses = int(match.group(2))
                self.wins = wins
                self.losses = losses

            # Detect worker count silently
            match = WORKER_PATTERN.search(line)
            if match:
                worker_id = int(match.group(1))
                self.num_workers = max(self.num_workers, worker_id + 1)

            # Detect battle start
            match = BATTLE_START_PATTERN.search(line)
            if match:
                # Extract based on which regex case matched
                if match.group(1):  # Case 1: ID then opponent
                    raw_id = match.group(1)
                    raw_opp = match.group(2) or match.group(3) or "Unknown"
                else:  # Case 2: vs Opponent then ID
                    raw_id = match.group(5)
                    raw_opp = match.group(4) or "Unknown"

                # Clean battle_id
                raw_id = raw_id.strip()
                if not raw_id.startswith('battle-'):
                    raw_id = f"battle-{raw_id}"
                
                battle_id = re.sub(r'[^a-zA-Z0-9-]', '', raw_id)
                opponent = re.sub(r'[^\w\s-]', '', raw_opp).strip()

                # Skip if we've already seen this battle
                if battle_id in self.seen_battle_ids:
                    continue

                # Mark as seen
                self.seen_battle_ids.add(battle_id)

                # Create battle state
                battle_state = BattleState(battle_id, opponent, datetime.now())
                self.active_battles[battle_id] = battle_state
                self.last_battle_id = battle_id
                logging.info(f"Battle started: {battle_id} vs {opponent}")

            # Detect winner
            match = WINNER_PATTERN.search(line)
            if match:
                detected_id = match.group(1)
                winner = match.group(2).strip()
                self.last_winner = winner

                # Associate with battle_id
                battle_id = detected_id
                if not battle_id:
                    battle_id = self.find_battle_for_event(line)
                
                # Load our username from env for comparison
                our_username = os.getenv("PS_USERNAME", "ALL CHUNG")
                if winner == our_username:
                    self.wins += 1
                    emoji = "🎉"
                    result = "won"
                elif winner == "None":
                    result = "Tie"
                    emoji = "🤝"
                else:
                    self.losses += 1
                    emoji = "💀"
                    result = "Lost"

                # Update battle state if found
                if battle_id and battle_id in self.active_battles:
                    opponent = self.active_battles[battle_id].opponent
                    # Move to finished_battles and remove from active immediately
                    self.finished_battles[battle_id] = (opponent, result)
                    del self.active_battles[battle_id]
                    # Record for batch report (replay URL added later when detected)
                    self.record_batch_result(opponent, result)
                else:
                    # Couldn't associate with a battle - still record it
                    self.record_batch_result("Unknown", result)

            # Detect replay link
            match = REPLAY_PATTERN.search(line)
            if match:
                replay_id = match.group(1)
                replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"

                # Attach replay URL to the most recent batch result for this battle
                if replay_url not in self.posted_replays:
                    self.posted_replays.add(replay_url)

                    # Update the batch entry with the replay URL
                    for i in range(len(self.batch_results) - 1, -1, -1):
                        opp, res, url = self.batch_results[i]
                        if url is None:
                            self.batch_results[i] = (opp, res, replay_url)
                            break

                    # Flush batch if we've hit BATCH_SIZE
                    if len(self.batch_results) >= self.BATCH_SIZE:
                        await self.flush_batch_report()

    def find_battle_for_event(self, line):
        """Try to find which battle an event belongs to"""
        # Look for battle tag in the line
        match = BATTLE_TAG_PATTERN.search(line)
        if match:
            battle_tag = match.group(0)
            # Return this battle tag if it's in active battles
            if battle_tag in self.active_battles:
                return battle_tag
        
        # Fall back to last battle if only one active
        if len(self.active_battles) == 1:
            return list(self.active_battles.keys())[0]
        
        # If multiple active and can't determine, return None
        return None

    async def run_bot(self):
        """Run the bot process and monitor it"""
        # Load credentials from environment
        ps_username = os.getenv("PS_USERNAME", "ALL CHUNG")
        ps_password = os.getenv("PS_PASSWORD", "ALLCHUNG")
        ps_websocket_uri = os.getenv("PS_WEBSOCKET_URI", "wss://sim3.psim.us/showdown/websocket")
        ps_format = os.getenv("PS_FORMAT", "gen9ou")
        team_names_env = os.getenv("TEAM_NAMES", "").strip()
        team_list_env = os.getenv("TEAM_LIST", "").strip()
        team_name_env = os.getenv("TEAM_NAME", "").strip()
        max_concurrent_env = os.getenv("MAX_CONCURRENT_BATTLES")
        search_parallelism_env = os.getenv("SEARCH_PARALLELISM")
        max_mcts_env = os.getenv("MAX_MCTS_BATTLES")
        bot_log_level = os.getenv("BOT_LOG_LEVEL", "INFO").strip().upper()
        bot_log_to_file = os.getenv("BOT_LOG_TO_FILE", "0").strip().lower() not in ("0", "false", "no", "off")
        
        cmd = [
            sys.executable, "-u", "run.py",  # -u for unbuffered output
            "--websocket-uri", ps_websocket_uri,
            "--ps-username", ps_username,
            "--ps-password", ps_password,
            "--bot-mode", "search_ladder",
            "--pokemon-format", ps_format,
            "--search-time-ms", "3000",
            "--run-count", "999999",
            "--save-replay", "always",
            "--log-level", bot_log_level
        ]

        # Team selection priority: TEAM_NAMES > TEAM_LIST > TEAM_NAME > default
        if team_names_env:
            cmd.extend(["--team-names", team_names_env])
        elif team_list_env:
            cmd.extend(["--team-list", team_list_env])
        elif team_name_env:
            cmd.extend(["--team-name", team_name_env])
        else:
            cmd.extend(["--team-name", "gen9/ou/fat-team-1-stall"])

        def _append_int_flag(flag, value):
            if value is None:
                return
            value = value.strip()
            if not value:
                return
            try:
                int(value)
            except ValueError:
                return
            cmd.extend([flag, value])

        _append_int_flag("--max-concurrent-battles", max_concurrent_env)
        _append_int_flag("--search-parallelism", search_parallelism_env)
        _append_int_flag("--max-mcts-battles", max_mcts_env)
        
        if bot_log_to_file:
            cmd.append("--log-to-file")

        # Ensure we're in the right directory
        cwd = Path(__file__).parent
        
        # Clean log file to prevent re-reading old battles
        log_file = cwd / "monitor.log"
        if log_file.exists():
            log_file.unlink()

        # Activate venv and run
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        env = os.environ.copy()
        env["FP_PARENT_PID"] = str(os.getpid())
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            creationflags=creationflags,
            env=env,
        )

        # Track the spawned bot process
        bot_pid = self.process.pid
        with open(BOT_MAIN_PID_FILE, 'w') as f:
            json.dump({
                'pid': bot_pid,
                'name': 'bot_main',
                'started_at': time.time(),
                'command': ' '.join(cmd)
            }, f)
        print(f"[MONITOR] Tracked bot process: PID {bot_pid}")

        # Extract username from cmd
        username = None
        for i, arg in enumerate(cmd):
            if arg == "--ps-username" and i + 1 < len(cmd):
                username = cmd[i + 1]
                break
        
        startup_msg = "🚀 **Fouler Play bot starting...**"
        if username:
            user_page = f"https://pokemonshowdown.com/users/{username.lower()}"
            startup_msg += f"\\n📊 **Account:** [{username}]({user_page})"
            startup_msg += "\\n⏳ *ELO stats will be posted once ladder data loads*"
        
        await self.send_discord_message(
            startup_msg,
            channel="battles"
        )

        # Monitor output
        await self.monitor_output(self.process.stdout)

        # Wait for process to complete
        await self.process.wait()
        self._cleanup_bot_main_pid()


async def main():
    monitor = BotMonitor()
    try:
        await monitor.run_bot()
    except KeyboardInterrupt:
        print("\\n[MONITOR] Shutting down...")
        try:
            if monitor.process:
                await monitor._shutdown_child(reason="KeyboardInterrupt", timeout_sec=5)
        except Exception as e:
            print(f"[MONITOR] Error during shutdown: {e}")
    except asyncio.CancelledError:
        print("\\n[MONITOR] Cancelled - shutting down...")
    except Exception as e:
        print(f"[MONITOR] Unexpected error: {e}")
    finally:
        await monitor.shutdown("monitor exit")


if __name__ == "__main__":
    asyncio.run(main())