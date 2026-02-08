#!/usr/bin/env python3
"""
Fouler Play Bot Monitor - Event-driven Discord notifications
Monitors bot output and instantly posts battle results/replays to Discord
Also automatically analyzes losses for improvement opportunities

Supports concurrent battles - tracks multiple active battles simultaneously.
"""

import sys
import os

# Force UTF-8 for console output on Windows (avoid cp1252 emoji crashes)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import asyncio
import re
import sys
import aiohttp
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

# Warn if OBS is likely logged into the bot account (causes all slots to mirror one battle)
ps_username_env = os.getenv("PS_USERNAME", "").strip()
spectator_username_env = os.getenv("SPECTATOR_USERNAME", "").strip()
if not spectator_username_env:
    print("[MONITOR] Tip: set SPECTATOR_USERNAME in .env and log OBS into that account")
    print("[MONITOR]      This prevents OBS slots from auto-switching to the bot's active battle.")
elif ps_username_env and spectator_username_env.lower() == ps_username_env.lower():
    print("[MONITOR] WARNING: SPECTATOR_USERNAME matches PS_USERNAME.")
    print("[MONITOR]          OBS will auto-lock to the bot's active battle.")

# Process tracking
PID_DIR = Path(__file__).parent / ".pids"
PID_DIR.mkdir(exist_ok=True)
PID_FILE = PID_DIR / "bot_monitor.pid"
BOT_MAIN_PID_FILE = PID_DIR / "bot_main.pid"
DRAIN_FILE = PID_DIR / "drain.request"
LAST_STARTUP_FILE = Path(__file__).parent / ".last_startup.json"

# Startup message throttling (seconds) - only post startup if it's been this long
STARTUP_THROTTLE_SEC = int(os.getenv("STARTUP_THROTTLE_SEC", "1800"))  # Default: 30 minutes


# Discord webhook URLs (from .env)
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")  # For project updates
DISCORD_BATTLES_WEBHOOK = os.getenv("DISCORD_BATTLES_WEBHOOK_URL")  # For battle notifications
DISCORD_FEEDBACK_WEBHOOK = os.getenv("DISCORD_FEEDBACK_WEBHOOK_URL")  # For turn reviews

# Bot identity for multi-bot reporting
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "").strip()  # e.g. "🪲 DEKU" or "💥 BAKUGO"

# Fix Windows latin-1 encoded UTF-8 bytes (Issue 3)
if BOT_DISPLAY_NAME:
    try:
        # Fix Windows latin-1 encoded UTF-8 bytes
        BOT_DISPLAY_NAME = BOT_DISPLAY_NAME.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass  # Already valid UTF-8

# Import replay analyzer and turn reviewer (commented out during upgrade)
# from replay_analysis.analyzer import ReplayAnalyzer
# from replay_analysis.turn_review import TurnReviewer
from streaming.stream_integration import start_stream, stop_stream, update_stream_status
from streaming.state_store import update_daily_stats

# Patterns to detect in bot output
# NOTE: Battle IDs can have alphanumeric hash suffixes like:
#   battle-gen9ou-2534534840-5w39ofnyucn5a7blb08thwjutgn7jyjpw
# All patterns must use [a-z0-9-]+ (not \d+) for the trailing portion.
BATTLE_ID_RE = r'battle-[a-z0-9]+-\d+(?:-[a-z0-9]+)?'  # reusable battle ID pattern

BATTLE_START_PATTERN = re.compile(
    r'(?:Initialized|Found pending|(?<!un)registered|Joining|Battle started|Claimed).*?'
    r'(?:'
    r'(' + BATTLE_ID_RE + r')(?:.*?against:\s*(.+)|.*?vs\s+(.+))'  # case 1: ID then opponent
    r'|'
    r'(?:vs\s+(.*?)\s+)?(' + BATTLE_ID_RE + r')'                    # case 2: vs opponent then ID
    r')', re.IGNORECASE)
BATTLE_END_PATTERN = re.compile(r'(Won|Lost) with team: (.+)')
REPLAY_PATTERN = re.compile(r'https://replay\.pokemonshowdown\.com/([\w-]+)')
ELO_PATTERN = re.compile(r'W: (\d+)\s+L: (\d+)')
WINNER_PATTERN = re.compile(r'(?:Battle finished: (' + BATTLE_ID_RE + r')\s+)?Winner: (.+)', re.IGNORECASE)
BATTLE_TAG_PATTERN = re.compile(BATTLE_ID_RE)
WORKER_PATTERN = re.compile(r'Battle worker (\d+) started')


class BattleState:
    """State for a single battle"""
    def __init__(self, battle_id, opponent, start_time):
        self.battle_id = battle_id
        self.opponent = opponent
        self.start_time = start_time
        self.result = None  # "won", "lost", "tie"
        self.replay_url = None


def should_post_startup_message():
    """Check if we should post a startup message based on throttle time."""
    if STARTUP_THROTTLE_SEC <= 0:
        return True  # Always post if throttling is disabled
    
    try:
        if LAST_STARTUP_FILE.exists():
            data = json.loads(LAST_STARTUP_FILE.read_text())
            last_startup = data.get("timestamp", 0)
            elapsed = time.time() - last_startup
            return elapsed >= STARTUP_THROTTLE_SEC
    except Exception:
        pass
    
    return True  # Post on first startup or if file is corrupt


def record_startup_message():
    """Record that we posted a startup message."""
    try:
        data = {
            "timestamp": time.time(),
            "bot_name": BOT_DISPLAY_NAME or "Unknown"
        }
        LAST_STARTUP_FILE.write_text(json.dumps(data))
    except Exception as e:
        print(f"[MONITOR] Failed to record startup time: {e}")


class BotMonitor:
    def __init__(self):
        self._write_self_pid()
        atexit.register(self._cleanup_self_pid)
        self.process = None
        self.wins = 0
        self.losses = 0
        # Issue 4: Make BATCH_SIZE configurable via env var
        batch_size_env = os.getenv("BATCH_SIZE", "3").strip()
        try:
            self.BATCH_SIZE = int(batch_size_env)
            if self.BATCH_SIZE < 1:
                self.BATCH_SIZE = 3
        except ValueError:
            self.BATCH_SIZE = 3
        # Session rebasing: start session display at 0/0 even if early wins land quickly
        rebase_env = os.getenv("SESSION_REBASE_ON_START", "1").strip().lower()
        self.session_rebase_enabled = rebase_env not in ("0", "false", "no", "off")
        self.session_base_wins = None
        self.session_base_losses = None
        # Track multiple active battles
        self.active_battles = {}  # battle_id -> BattleState
        self.seen_battle_ids = set()  # Track ALL battles ever seen (prevents duplicates)
        self.last_winner = None  # for associating winner with replay
        self.last_battle_id = None  # for associating events
        self.battle_message_map = {}  # Track most recent battle for each message stream
        self.finished_battles = {}  # battle_id -> (opponent, result) for completed battles awaiting replay
        self.analyzer = None  # ReplayAnalyzer() - disabled during upgrade
        self.turn_reviewer = None  # TurnReviewer() - disabled during upgrade
        self.posted_replays = set()  # Track posted replays to avoid duplicates
        self.num_workers = 0
        # Batch reporting state
        self.batch_results = []  # list of (opponent, result, replay_url)
        self.batch_losses = []  # loss replay URLs for batch analysis
        self.batch_wins_count = 0
        self.batch_losses_count = 0
        # Drain mode tracking
        self.drain_requested = False
        drain_timeout_env = os.getenv("DRAIN_TIMEOUT_SEC", "900").strip()
        if drain_timeout_env.isdigit() and int(drain_timeout_env) > 0:
            self.drain_timeout_sec = int(drain_timeout_env)
        else:
            self.drain_timeout_sec = None

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

    async def send_discord_message(self, message, channel="project", suppress_embeds=False):
        """Send instant notification via Discord webhook

        Args:
            message: Message content
            channel: "project" (dev updates), "battles" (live feed), or "feedback" (turn reviews)
            suppress_embeds: If True, suppress automatic URL embeds (Discord flag 1 << 2 = 4)
        """
        if channel == "battles":
            webhook_url = DISCORD_BATTLES_WEBHOOK
        elif channel == "feedback":
            webhook_url = DISCORD_FEEDBACK_WEBHOOK
        else:
            webhook_url = DISCORD_WEBHOOK

        if not webhook_url:
            safe_message = message
            try:
                safe_message.encode("cp1252")
            except UnicodeEncodeError:
                safe_message = message.encode("ascii", "replace").decode("ascii")
            print(f"[MONITOR] No webhook configured: {safe_message}")
            return

        async with aiohttp.ClientSession() as session:
            payload = {"content": message}
            # Issue 2: Add suppress_embeds flag (SUPPRESS_EMBEDS = 1 << 2 = 4)
            if suppress_embeds:
                payload["flags"] = 4
            try:
                # Ensure UTF-8 encoding for Discord webhook
                async with session.post(webhook_url, json=payload, headers={"Content-Type": "application/json; charset=utf-8"}) as resp:
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

    async def wait_for_drain(self):
        """Wait for the bot process to exit after drain is requested."""
        if not self.process or self.process.returncode is not None:
            return
        try:
            if self.drain_timeout_sec is None:
                await self.process.wait()
            else:
                await asyncio.wait_for(self.process.wait(), timeout=self.drain_timeout_sec)
        except asyncio.TimeoutError:
            print("[MONITOR] Drain timed out. Forcing shutdown...")
            try:
                self.process.terminate()
            except Exception:
                pass
            await self.process.wait()

    async def fetch_elo(self, username, pokemon_format="gen9ou"):
        """Fetch current ELO and GXE from Pokemon Showdown ladder API.
        
        Returns:
            tuple: (elo, gxe) or (None, None) on failure
        """
        try:
            # Normalize username for API lookup (lowercase, no spaces)
            user_id = username.lower().replace(' ', '')
            
            # Try user API first - more reliable
            user_url = f"https://pokemonshowdown.com/users/{user_id}.json"
            async with aiohttp.ClientSession() as session:
                async with session.get(user_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # ratings is a dict like {"gen9ou": {"elo": 1169, "gxe": 53.8, ...}}
                        if 'ratings' in data and pokemon_format in data['ratings']:
                            rating = data['ratings'][pokemon_format]
                            elo = rating.get('elo')
                            gxe = rating.get('gxe')
                            if elo is not None:
                                return (elo, gxe)
            
            # Fallback to ladder API (slower but works if user API fails)
            ladder_url = f"https://pokemonshowdown.com/api/ladder/{pokemon_format}.json"
            async with aiohttp.ClientSession() as session:
                async with session.get(ladder_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        ladder = await resp.json()
                        # Search for user in ladder
                        for entry in ladder:
                            if entry.get('userid') == user_id:
                                return (entry.get('elo'), entry.get('gxe'))
            
            return (None, None)
        except Exception as e:
            print(f"[MONITOR] Failed to fetch ELO: {e}")
            return (None, None)

    async def flush_batch_report(self):
        """Post a summary of the last BATCH_SIZE games to Discord."""
        if not self.batch_results:
            return

        wins = self.batch_wins_count
        losses = self.batch_losses_count
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0

        # Fetch current ELO (Issue 1)
        ps_username = os.getenv("PS_USERNAME", "ALL CHUNG")
        ps_format = os.getenv("PS_FORMAT", "gen9ou")
        elo, gxe = await self.fetch_elo(ps_username, ps_format)
        
        elo_str = ""
        if elo is not None:
            elo_str = f" | **ELO: {elo}**"
            if gxe is not None:
                elo_str += f" ({gxe:.1f} GXE)"

        name_tag = f" [{BOT_DISPLAY_NAME}]" if BOT_DISPLAY_NAME else ""
        msg = f"📊 **Batch Report{name_tag} ({total} games):** {wins}W - {losses}L ({wr:.0f}% WR){elo_str}\n"
        msg += f"**Overall Record:** {self.wins}W - {self.losses}L\n\n"

        # List results — suppress Discord embeds on non-loss replays with <url>
        for opp, result, replay in self.batch_results:
            emoji = "✅" if result == "won" else "💀" if result == "lost" else "🤝"
            if replay:
                if result == "lost":
                    # Losses get full embed (bare URL on its own line)
                    replay_link = f" — [replay]({replay})"
                else:
                    # Wins/ties get suppressed embed (angle brackets)
                    replay_link = f" — [replay](<{replay}>)"
            else:
                replay_link = ""
            msg += f"{emoji} vs {opp}{replay_link}\n"

        # Loss analysis summary
        if self.batch_losses:
            msg += f"\n🔍 **Analyzing {len(self.batch_losses)} loss(es)...**"

        # Suppress all auto-embeds on the batch message; losses get their own embed messages below
        await self.send_discord_message(msg, channel="battles", suppress_embeds=True)

        # Run loss analyses in background
        for replay_url, opponent in self.batch_losses:
            class BattleStateStub:
                def __init__(self, opp):
                    self.battle_id = "batch"
                    self.opponent = opp
            asyncio.create_task(self.analyze_loss_async(replay_url, BattleStateStub(opponent)))

        # Reset batch
        self.batch_results = []
        self.batch_losses = []
        self.batch_wins_count = 0
        self.batch_losses_count = 0

    def record_batch_result(self, opponent, result, replay_url=None):
        """Add a game result to the current batch."""
        self.batch_results.append((opponent, result, replay_url))
        if result == "won":
            self.batch_wins_count += 1
        elif result == "lost":
            self.batch_losses_count += 1
            if replay_url:
                self.batch_losses.append((replay_url, opponent))

    async def analyze_loss_async(self, replay_url, battle_state):
        """Analyze a loss replay in the background (disabled during upgrade)"""
        try:
            # Analysis disabled during upgrade
            print(f"[MONITOR] Analysis disabled during upgrade for battle vs {battle_state.opponent}")
            return
        except Exception as e:
            print(f"[MONITOR] Error in analysis placeholder: {e}")

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

    async def cleanup_stale_battles(self):
        """Remove battles that have been active for >30 minutes (likely orphaned)"""
        now = datetime.now()
        stale_battles = []
        
        for battle_id, state in list(self.active_battles.items()):
            age_minutes = (now - state.start_time).total_seconds() / 60
            if age_minutes > 30:
                stale_battles.append((battle_id, state))
        
        for battle_id, state in stale_battles:
            print(f"[MONITOR] Cleaning up stale battle vs {state.opponent}")
            del self.active_battles[battle_id]
    
    async def monitor_output(self, stream):
        """Monitor bot output line by line"""
        last_cleanup = datetime.now()
        
        async for line in stream:
            # Periodic cleanup of stale battles (every 5 minutes)
            if (datetime.now() - last_cleanup).total_seconds() > 300:
                await self.cleanup_stale_battles()
                last_cleanup = datetime.now()
            
            line = line.decode('utf-8', errors='replace').strip()
            logging.debug(f"Line: {line}")
            try:
                print(line)  # Echo to stdout
            except UnicodeEncodeError:
                print(line.encode("ascii", "replace").decode("ascii"))

            # Track which battle is currently outputting messages
            # Pattern: "Received for battle battle-gen9ou-XXXXX:" or "DEBUG    Received for battle..."
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
                total = wins + losses
                win_rate = (wins / total * 100) if total > 0 else 0
                
                # Track stats silently -- batch report handles Discord
                base_was_none = self.session_rebase_enabled and self.session_base_wins is None
                if self.session_rebase_enabled:
                    raw_wins, raw_losses = wins, losses
                    if self.session_base_wins is None:
                        self.session_base_wins = raw_wins
                        self.session_base_losses = raw_losses
                    elif raw_wins < self.session_base_wins or raw_losses < self.session_base_losses:
                        # Bot reset or stats rolled over; rebase again.
                        self.session_base_wins = raw_wins
                        self.session_base_losses = raw_losses
                        base_was_none = True
                    wins = max(0, raw_wins - self.session_base_wins)
                    losses = max(0, raw_losses - self.session_base_losses)

                allow_update = (
                    base_was_none
                    or (self.wins == 0 and self.losses == 0)
                    or (wins >= self.wins and losses >= self.losses)
                )
                if allow_update and (wins != self.wins or losses != self.losses or base_was_none):
                    self.wins = wins
                    self.losses = losses
                    active_battle_ids = [
                        bid for bid, b in self.active_battles.items() if b.result is None
                    ]
                    battle_info = ", ".join(
                        f"vs {self.active_battles[bid].opponent}" for bid in active_battle_ids
                    ) if active_battle_ids else "Waiting..."
                    await update_stream_status(
                        wins=self.wins,
                        losses=self.losses,
                        status="Battling" if active_battle_ids else "Idle",
                        battle_info=battle_info,
                    )

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

                # Aggressive cleaning of battle_id
                raw_id = raw_id.strip()
                # Ensure it starts with battle- if it doesn't already
                if not raw_id.startswith('battle-'):
                    raw_id = f"battle-{raw_id}"
                
                # Strip ALL hidden chars, ANSI, and spaces
                battle_id = re.sub(r'[^a-zA-Z0-9-]', '', raw_id)
                
                opponent = re.sub(r'[^\w\s-]', '', raw_opp).strip()

                # If we've already seen this battle, update opponent if it was Unknown
                if battle_id in self.seen_battle_ids:
                    if opponent and opponent != "Unknown" and battle_id in self.active_battles:
                        if self.active_battles[battle_id].opponent == "Unknown":
                            self.active_battles[battle_id].opponent = opponent
                            logging.info(f"Updated opponent for {battle_id}: {opponent}")
                    continue

                # Mark as seen
                self.seen_battle_ids.add(battle_id)

                # Create battle state
                battle_state = BattleState(battle_id, opponent, datetime.now())
                self.active_battles[battle_id] = battle_state
                self.last_battle_id = battle_id
                logging.info(f"Battle started: {battle_id} vs {opponent}")
# Stream integration: go live on first battle (disabled during upgrade)
                # active_count = sum(1 for b in self.active_battles.values() if b.result is None)
                # if active_count == 1:
                #     await start_stream()

                # # Update stream overlay with battle info
                # active_battle_ids = [bid for bid, b in self.active_battles.items() if b.result is None]
                # battle_info = ", ".join(f"vs {self.active_battles[bid].opponent}" for bid in active_battle_ids)
                # await update_stream_status(
                #     wins=self.wins, losses=self.losses,
                #     status="Battling", battle_info=battle_info
                # )

            # Detect winner - associate with correct battle
            match = WINNER_PATTERN.search(line)
            if match:
                # Group 1 is battle_id (new format), Group 2 is winner
                # If old format, Group 1 is None and Group 2 is winner
                detected_id = match.group(1)
                winner = match.group(2).strip()
                self.last_winner = winner

                # Associate with battle_id
                battle_id = detected_id
                if not battle_id:
                    battle_id = self.find_battle_for_event(line)
                
                # If still not found, use last_battle_id or single active
                if not battle_id:
                    if self.last_battle_id and self.last_battle_id in self.active_battles:
                        battle_id = self.last_battle_id
                    elif len(self.active_battles) == 1:
                        battle_id = list(self.active_battles.keys())[0]

                # Load our username from env for comparison
                # Normalize both sides (Showdown strips spaces/special chars)
                def _norm_user(n): return re.sub(r'[^a-z0-9]', '', n.lower()) if n else ""
                our_username = os.getenv("PS_USERNAME", "ALL CHUNG")
                showdown_accts = os.getenv("SHOWDOWN_ACCOUNTS", our_username).split(",")
                normalized_accts = [_norm_user(a) for a in showdown_accts if a.strip()]
                if _norm_user(winner) in normalized_accts:
                    if not self.session_rebase_enabled or self.session_base_wins is not None:
                        self.wins += 1
                    update_daily_stats(wins_delta=1)  # Track daily totals
                    emoji = "🎉"
                    result = "Won"
                    result_key = "won"
                elif winner == "None":
                    result = "Tie"
                    emoji = "🤝"
                    result_key = "tie"
                else:
                    if not self.session_rebase_enabled or self.session_base_wins is not None:
                        self.losses += 1
                    update_daily_stats(losses_delta=1)  # Track daily totals
                    emoji = "💀"
                    result = "Lost"
                    result_key = "lost"

                    # Increment loss counter for improvement pipeline
                    # Increment loss counter for improvement pipeline
                    import tempfile
                    counter_file = Path(tempfile.gettempdir()) / "fp-losses-since-deploy"
                    try:
                        count = int(counter_file.read_text().strip()) if counter_file.exists() else 0
                        counter_file.write_text(str(count + 1))
                    except (ValueError, OSError):
                        counter_file.write_text("1")

                # Update battle state if found
                if battle_id and battle_id in self.active_battles:
                    opponent = self.active_battles[battle_id].opponent

                    # Move to finished_battles and remove from active immediately
                    self.finished_battles[battle_id] = (opponent, result_key)
                    del self.active_battles[battle_id]
                    # Record for batch report (replay URL added later when detected)
                    self.record_batch_result(opponent, result_key)

                else:
                    # Couldn't associate with a battle - still record it
                    self.record_batch_result("Unknown", result_key)

                # Always update stream overlay with remaining battles/stats
                active_count = len(self.active_battles)
                active_battle_ids = [bid for bid, b in self.active_battles.items() if b.result is None]
                battle_info = ", ".join(
                    f"vs {self.active_battles[bid].opponent}" for bid in active_battle_ids
                ) if active_battle_ids else "Waiting..."
                await update_stream_status(
                    wins=self.wins, losses=self.losses,
                    status="Battling" if active_battle_ids else "Idle",
                    battle_info=battle_info
                )

                # # Stop stream if no more active battles
                # if active_count == 0:
                #     await stop_stream()

            # Detect battle end with team
            match = BATTLE_END_PATTERN.search(line)
            if match:
                result = match.group(1)
                team = match.group(2)
                # Additional info if we want team name

            # Detect replay link - associate with battle
            match = REPLAY_PATTERN.search(line)
            if match:
                replay_id = match.group(1)
                # Strip any spectator hash from replay ID (format: gen9ou-NUMBER or gen9ou-NUMBER-HASH)
                # Keep only format-number portions
                parts = replay_id.split("-")
                if len(parts) >= 3:
                    # Remove hash suffix (everything after the second dash)
                    replay_id = f"{parts[0]}-{parts[1]}"
                replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"

                # Extract battle_id from replay_id
                # Replay URLs contain the battle tag, e.g. gen9ou-2529712238
                # But battle_id has "battle-" prefix, e.g. battle-gen9ou-2529712238
                battle_id = None
                replay_suffix = replay_url.split('/')[-1]  # "gen9ou-2529712238"
                
                # Check finished_battles (battles that have completed)
                # Try exact match first, then prefix match for hash-suffixed IDs
                for bid in self.finished_battles:
                    bid_suffix = bid.replace("battle-", "", 1)
                    if bid_suffix == replay_suffix or replay_suffix.startswith(bid_suffix) or bid_suffix.startswith(replay_suffix):
                        battle_id = bid
                        break
                
                # Last resort: use find_battle_for_event
                if not battle_id:
                    battle_id = self.find_battle_for_event(line)

                # Attach replay URL to the most recent batch result for this battle
                if replay_url not in self.posted_replays:
                    self.posted_replays.add(replay_url)

                    if battle_id and battle_id in self.finished_battles:
                        opponent, result = self.finished_battles[battle_id]

                        # Update the batch entry with the replay URL
                        for i in range(len(self.batch_results) - 1, -1, -1):
                            opp, res, url = self.batch_results[i]
                            if opp == opponent and res == result and url is None:
                                self.batch_results[i] = (opp, res, replay_url)
                                break

                        # Track loss replays for batch analysis
                        if result == "lost":
                            # Update batch_losses with actual URL
                            self.batch_losses = [(u, o) if u != replay_url else (u, o) for u, o in self.batch_losses]
                            if not any(u == replay_url for u, _ in self.batch_losses):
                                self.batch_losses.append((replay_url, opponent))

                        del self.finished_battles[battle_id]

                    # Flush batch if we've hit BATCH_SIZE
                    if len(self.batch_results) >= self.BATCH_SIZE:
                        await self.flush_batch_report()

    async def run_bot(self):
        """Run the bot process and monitor it
        
        Configuration:
        - Uses 3 concurrent battle workers (MAX_CONCURRENT_BATTLES=3 in run.py)
        - Rotates through 3 fat teams: stall, pivot, dondozo
        - Target: 1700 ELO
        - Runs indefinitely (--run-count 999999)
        """
        # Load credentials from environment
        ps_username = os.getenv("PS_USERNAME", "ALL CHUNG")
        ps_password = os.getenv("PS_PASSWORD", "ALLCHUNG")
        ps_websocket_uri = os.getenv("PS_WEBSOCKET_URI", "wss://sim3.psim.us/showdown/websocket")
        ps_format = os.getenv("PS_FORMAT", "gen9ou")
        spectator_username = os.getenv("SPECTATOR_USERNAME")
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
            "--search-time-ms", "3000",  # Reduced to stay under turn timer
            "--run-count", "999999",  # Run indefinitely until 1700 ELO
            "--save-replay", "always",
            "--log-level", bot_log_level  # Adjustable via BOT_LOG_LEVEL
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

        if spectator_username:
            cmd.extend(["--spectator-username", spectator_username])

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
        
        # Only post startup message if enough time has passed since last startup
        if should_post_startup_message():
            name_tag = f" [{BOT_DISPLAY_NAME}]" if BOT_DISPLAY_NAME else ""
            startup_msg = f"🚀 **Fouler Play bot{name_tag} starting...**"
            if username:
                user_page = f"https://pokemonshowdown.com/users/{username.lower().replace(' ', '')}"
                startup_msg += f"\n📊 **Account:** [{username}]({user_page})"
                startup_msg += "\n⏳ *ELO stats will be posted once ladder data loads*"
            
            await self.send_discord_message(
                startup_msg,
                channel="battles"
            )
            record_startup_message()
        else:
            print(f"[MONITOR] Skipping startup message (throttled)")

        # Echo to logs (not Discord)
        name_tag = f" [{BOT_DISPLAY_NAME}]" if BOT_DISPLAY_NAME else ""
        print(f"[MONITOR] Fouler Play bot{name_tag} starting...")

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
        print("\n[MONITOR] Drain requested. Press Ctrl+C again to force stop.")
        if monitor.process:
            await monitor.request_drain("KeyboardInterrupt")
            try:
                await monitor.wait_for_drain()
            except KeyboardInterrupt:
                print("\n[MONITOR] Force stopping...")
                try:
                    monitor.process.terminate()
                except Exception:
                    pass
                await monitor.process.wait()
    finally:
        await monitor.shutdown("monitor exit")

if __name__ == "__main__":
    asyncio.run(main())
