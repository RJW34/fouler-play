#!/usr/bin/env python3
"""
Fouler Play Bot Monitor - Event-driven Discord notifications
Monitors bot output and instantly posts battle results/replays to Discord
Also automatically analyzes losses for improvement opportunities

Supports concurrent battles - tracks multiple active battles simultaneously.
"""

import asyncio
import re
import sys
import aiohttp
import json
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Process tracking
PID_DIR = Path(__file__).parent / ".pids"
PID_DIR.mkdir(exist_ok=True)

# Discord webhook URLs (from .env)
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")  # For project updates
DISCORD_BATTLES_WEBHOOK = os.getenv("DISCORD_BATTLES_WEBHOOK_URL")  # For battle notifications
DISCORD_FEEDBACK_WEBHOOK = os.getenv("DISCORD_FEEDBACK_WEBHOOK_URL")  # For turn reviews

# Import replay analyzer and turn reviewer
sys.path.append(str(Path(__file__).parent))
from replay_analysis.analyzer import ReplayAnalyzer
from replay_analysis.turn_review import TurnReviewer
from streaming.stream_integration import start_stream, stop_stream, update_stream_status

# Patterns to detect in bot output
BATTLE_START_PATTERN = re.compile(r'Initialized (battle-[\w-]+) against: (.+)')
BATTLE_END_PATTERN = re.compile(r'(Won|Lost) with team: (.+)')
REPLAY_PATTERN = re.compile(r'https://replay\.pokemonshowdown\.com/([\w-]+)')
ELO_PATTERN = re.compile(r'W: (\d+)\s+L: (\d+)')
WINNER_PATTERN = re.compile(r'Winner: (.+)')
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
    def __init__(self):
        self.process = None
        self.wins = 0
        self.losses = 0
        # Track multiple active battles
        self.active_battles = {}  # battle_id -> BattleState
        self.seen_battle_ids = set()  # Track ALL battles ever seen (prevents duplicates)
        self.last_winner = None  # for associating winner with replay
        self.last_battle_id = None  # for associating events
        self.battle_message_map = {}  # Track most recent battle for each message stream
        self.finished_battles = {}  # battle_id -> (opponent, result) for completed battles awaiting replay
        self.analyzer = ReplayAnalyzer()
        self.turn_reviewer = TurnReviewer()
        self.posted_replays = set()  # Track posted replays to avoid duplicates
        self.num_workers = 0

    async def send_discord_message(self, message, channel="project"):
        """Send instant notification via Discord webhook

        Args:
            message: Message content
            channel: "project" (dev updates), "battles" (live feed), or "feedback" (turn reviews)
        """
        if channel == "battles":
            webhook_url = DISCORD_BATTLES_WEBHOOK
        elif channel == "feedback":
            webhook_url = DISCORD_FEEDBACK_WEBHOOK
        else:
            webhook_url = DISCORD_WEBHOOK

        if not webhook_url:
            print(f"[MONITOR] No webhook configured: {message}")
            return

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

    async def analyze_loss_async(self, replay_url, battle_state):
        """Analyze a loss replay in the background"""
        try:
            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()

            # Fetch replay data once
            replay_data = await loop.run_in_executor(
                None,
                self.analyzer.fetch_replay,
                replay_url
            )

            if not replay_data:
                await self.send_discord_message(
                    f"⚠️ **Analysis failed:** Could not fetch replay for battle vs {battle_state.opponent}"
                )
                return

            # Run mistake analysis
            analysis = await loop.run_in_executor(
                None,
                self.analyzer.analyze_loss,
                replay_url
            )

            if analysis and analysis.get("mistakes_found", 0) > 0:
                mistakes = analysis["mistakes_found"]
                msg = f"📊 **Analysis (vs {battle_state.opponent}):** Found {mistakes} issue(s)"

                # List top mistakes
                for mistake in analysis["mistakes"][:3]:  # Top 3
                    msg += f"\n• **{mistake['severity'].upper()}**: {mistake['description']}"

                await self.send_discord_message(msg)
            else:
                # If we lost but found no mistakes, that's itself a problem
                await self.send_discord_message(
                    f"⚠️ **Analysis (vs {battle_state.opponent}):** Couldn't identify specific mistakes"
                )

            # Extract critical turns for review
            await self.send_discord_message(
                f"🔍 **Extracting critical decision points (vs {battle_state.opponent})...**",
                channel="feedback"
            )

            turn_messages = await loop.run_in_executor(
                None,
                self.turn_reviewer.analyze_and_post,
                replay_data,
                replay_url
            )

            # Post each critical turn to FEEDBACK channel as a separate message
            if turn_messages:
                for turn_msg in turn_messages:
                    await self.send_discord_message(turn_msg, channel="feedback")
                    await asyncio.sleep(1)  # Slight delay between messages
            else:
                await self.send_discord_message(
                    f"ℹ️ No critical decision points identified (vs {battle_state.opponent})",
                    channel="feedback"
                )

        except Exception as e:
            print(f"[MONITOR] Error analyzing loss: {e}")
            import traceback
            traceback.print_exc()
            await self.send_discord_message(
                f"⚠️ **Analysis failed (vs {battle_state.opponent}):** {str(e)[:100]}"
            )

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
            await self.send_discord_message(
                f"⚠️ **Cleaning up stale battle vs {state.opponent}** (active for {int((now - state.start_time).total_seconds() / 60)}min)",
                channel="battles"
            )
            del self.active_battles[battle_id]
    
    async def monitor_output(self, stream):
        """Monitor bot output line by line"""
        last_cleanup = datetime.now()
        
        async for line in stream:
            # Periodic cleanup of stale battles (every 5 minutes)
            if (datetime.now() - last_cleanup).total_seconds() > 300:
                await self.cleanup_stale_battles()
                last_cleanup = datetime.now()
            
            line = line.decode('utf-8').strip()
            print(line)  # Echo to stdout

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
                
                # Only report if this is new info (different from last known)
                if wins != self.wins or losses != self.losses:
                    self.wins = wins
                    self.losses = losses
                    
                    await self.send_discord_message(
                        f"📊 **Ladder Stats:** {wins}W - {losses}L ({win_rate:.1f}% win rate)",
                        channel="battles"
                    )

            # Detect worker count and report
            match = WORKER_PATTERN.search(line)
            if match:
                worker_id = int(match.group(1))
                old_num_workers = self.num_workers
                self.num_workers = max(self.num_workers, worker_id + 1)
                
                # Report when we've detected all workers starting
                if old_num_workers == 0 and self.num_workers > 1:
                    await self.send_discord_message(
                        f"⚙️ **Running {self.num_workers} concurrent battle workers**",
                        channel="battles"
                    )

            # Detect battle start
            match = BATTLE_START_PATTERN.search(line)
            if match:
                battle_id = match.group(1)
                opponent = match.group(2)

                # Skip if we've already seen this battle (prevents duplicates from log re-reading)
                if battle_id in self.seen_battle_ids:
                    continue

                # Mark as seen
                self.seen_battle_ids.add(battle_id)

                # Create battle state
                battle_state = BattleState(battle_id, opponent, datetime.now())
                self.active_battles[battle_id] = battle_state
                self.last_battle_id = battle_id

                link = f"https://play.pokemonshowdown.com/{battle_id}"
                # Count only battles that don't have results yet (truly active)
                active_count = sum(1 for b in self.active_battles.values() if b.result is None)

                await self.send_discord_message(
                    f"🎮 **Battle started vs {opponent}** ({active_count} active)\n{link}",
                    channel="battles"
                )

                # Stream integration: go live on first battle, update battle sources
                if active_count == 1:
                    await start_stream()
                
                # Update stream overlay with battle info
                active_battle_ids = [bid for bid, b in self.active_battles.items() if b.result is None]
                battle_info = ", ".join(f"vs {self.active_battles[bid].opponent}" for bid in active_battle_ids)
                await update_stream_status(
                    wins=self.wins, losses=self.losses,
                    status="Battling", battle_info=battle_info
                )

            # Detect winner - need to associate with correct battle
            match = WINNER_PATTERN.search(line)
            if match:
                winner = match.group(1).strip()
                self.last_winner = winner

                # Find the battle this belongs to
                # First try to find battle tag in the line itself
                battle_id = self.find_battle_for_event(line)
                
                # If not found, use the last battle we saw messages from
                if not battle_id and self.last_battle_id and self.last_battle_id in self.active_battles:
                    battle_id = self.last_battle_id
                
                # Last resort: if only one battle is active, use that
                if not battle_id and len(self.active_battles) == 1:
                    battle_id = list(self.active_battles.keys())[0]

                # Load our username from env for comparison
                our_username = os.getenv("PS_USERNAME", "LEBOTJAMESXD003")
                if winner == our_username:
                    self.wins += 1
                    emoji = "🎉"
                    result = "Won"
                    result_key = "won"
                elif winner == "None":
                    result = "Tie"
                    emoji = "🤝"
                    result_key = "tie"
                else:
                    self.losses += 1
                    emoji = "💀"
                    result = "Lost"
                    result_key = "lost"

                    # Increment loss counter for improvement pipeline
                    counter_file = Path("/tmp/fp-losses-since-deploy")
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
                    
                    # Count truly active battles (no result yet)
                    active_count = len(self.active_battles)
                    
                    await self.send_discord_message(
                        f"{emoji} **{result} vs {opponent}!** Record: {self.wins}W - {self.losses}L ({active_count} active)",
                        channel="battles"
                    )

                    # Update stream overlay with remaining battles
                    active_battle_ids = [bid for bid, b in self.active_battles.items() if b.result is None]
                    battle_info = ", ".join(f"vs {self.active_battles[bid].opponent}" for bid in active_battle_ids) if active_battle_ids else "Waiting..."
                    await update_stream_status(
                        wins=self.wins, losses=self.losses,
                        status="Battling" if active_battle_ids else "Idle",
                        battle_info=battle_info
                    )
                    
                    # Stop stream if no more active battles
                    if active_count == 0:
                        await stop_stream()
                else:
                    # Couldn't associate with a battle - still report it
                    await self.send_discord_message(
                        f"{emoji} **{result}!** Record: {self.wins}W - {self.losses}L (battle tracking lost)",
                        channel="battles"
                    )

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
                replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"

                # Extract battle_id from replay_id
                # Replay URLs contain the battle tag, e.g. gen9ou-2529712238
                # But battle_id has "battle-" prefix, e.g. battle-gen9ou-2529712238
                battle_id = None
                replay_suffix = replay_url.split('/')[-1]  # "gen9ou-2529712238"
                
                # Check finished_battles (battles that have completed)
                for bid in self.finished_battles:
                    if bid.replace("battle-", "") == replay_suffix:
                        battle_id = bid
                        break
                
                # Last resort: use find_battle_for_event
                if not battle_id:
                    battle_id = self.find_battle_for_event(line)

                # Only post if we haven't already posted this replay
                if replay_url not in self.posted_replays:
                    self.posted_replays.add(replay_url)

                    # Check if this was a finished battle
                    if battle_id and battle_id in self.finished_battles:
                        opponent, result = self.finished_battles[battle_id]
                        await self.send_discord_message(
                            f"🔗 **Replay (vs {opponent}):** {replay_url}",
                            channel="battles"
                        )

                        # If this was a loss, analyze it automatically
                        if result == "lost":
                            await self.send_discord_message(
                                f"🔍 **Analyzing loss vs {opponent}...**",
                                channel="battles"
                            )
                            # Create a minimal battle state for analysis
                            class BattleStateStub:
                                def __init__(self, bid, opp):
                                    self.battle_id = bid
                                    self.opponent = opp
                            # Run analysis in background
                            asyncio.create_task(self.analyze_loss_async(replay_url, BattleStateStub(battle_id, opponent)))
                        
                        # Clean up from finished_battles
                        del self.finished_battles[battle_id]
                    else:
                        # Battle not found - still post replay
                        await self.send_discord_message(
                            f"🔗 **Replay:** {replay_url}",
                            channel="battles"
                        )

    async def run_bot(self):
        """Run the bot process and monitor it
        
        Configuration:
        - Uses 2 concurrent battle workers (MAX_CONCURRENT_BATTLES=2 in run.py)
        - Rotates through 3 fat teams: stall, pivot, dondozo
        - Target: 1700 ELO
        - Runs indefinitely (--run-count 999999)
        """
        # Load credentials from environment
        ps_username = os.getenv("PS_USERNAME", "LEBOTJAMESXD003")
        ps_password = os.getenv("PS_PASSWORD", "LeBotPassword2026!")
        ps_websocket_uri = os.getenv("PS_WEBSOCKET_URI", "wss://sim3.psim.us/showdown/websocket")
        ps_format = os.getenv("PS_FORMAT", "gen9ou")
        
        cmd = [
            "venv/bin/python", "-u", "run.py",  # -u for unbuffered output
            "--websocket-uri", ps_websocket_uri,
            "--ps-username", ps_username,
            "--ps-password", ps_password,
            "--bot-mode", "search_ladder",
            "--pokemon-format", ps_format,
            "--team-name", "gen9/ou/fat-team-1-stall",  # Single team (team-list has bug)
            "--search-time-ms", "3000",  # Reduced to stay under turn timer
            "--run-count", "999999",  # Run indefinitely until 1700 ELO
            "--save-replay", "always",
            "--log-level", "INFO"  # Reduced verbosity so auto_stream can track battles
        ]

        # Ensure we're in the right directory
        cwd = Path(__file__).parent
        
        # Clean log file to prevent re-reading old battles
        log_file = cwd / "monitor_output.log"
        if log_file.exists():
            log_file.unlink()

        # Activate venv and run
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd
        )

        # Track the spawned bot process
        bot_pid = self.process.pid
        pid_file = PID_DIR / "bot_main.pid"
        with open(pid_file, 'w') as f:
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
            startup_msg += f"\n📊 **Account:** [{username}]({user_page})"
            startup_msg += "\n⏳ *ELO stats will be posted once ladder data loads*"
        
        await self.send_discord_message(
            startup_msg,
            channel="battles"
        )

        # Monitor output
        await self.monitor_output(self.process.stdout)

        # Wait for process to complete
        await self.process.wait()

async def main():
    monitor = BotMonitor()
    try:
        await monitor.run_bot()
    except KeyboardInterrupt:
        print("\n[MONITOR] Shutting down...")
        if monitor.process:
            monitor.process.terminate()
            await monitor.process.wait()

if __name__ == "__main__":
    asyncio.run(main())
