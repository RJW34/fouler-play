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
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Discord webhook URLs (from .env)
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")  # For project updates
DISCORD_BATTLES_WEBHOOK = os.getenv("DISCORD_BATTLES_WEBHOOK_URL")  # For battle notifications
DISCORD_FEEDBACK_WEBHOOK = os.getenv("DISCORD_FEEDBACK_WEBHOOK_URL")  # For turn reviews

# Import replay analyzer and turn reviewer
sys.path.append(str(Path(__file__).parent))
from replay_analysis.analyzer import ReplayAnalyzer
from replay_analysis.turn_review import TurnReviewer

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
        self.last_winner = None  # for associating winner with replay
        self.last_battle_id = None  # for associating events
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

    async def monitor_output(self, stream):
        """Monitor bot output line by line"""
        async for line in stream:
            line = line.decode('utf-8').strip()
            print(line)  # Echo to stdout

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

                # Create battle state
                battle_state = BattleState(battle_id, opponent, datetime.now())
                self.active_battles[battle_id] = battle_state
                self.last_battle_id = battle_id

                link = f"https://play.pokemonshowdown.com/{battle_id}"
                active_count = len(self.active_battles)

                await self.send_discord_message(
                    f"🎮 **Battle started vs {opponent}** ({active_count} active)\n{link}",
                    channel="battles"
                )

            # Detect winner - need to associate with correct battle
            match = WINNER_PATTERN.search(line)
            if match:
                winner = match.group(1).strip()
                self.last_winner = winner

                # Find the battle this belongs to
                battle_id = self.find_battle_for_event(line)
                
                # If we can't find the battle but only one is active, use that
                if not battle_id and len(self.active_battles) == 1:
                    battle_id = list(self.active_battles.keys())[0]

                if winner == "LEBOTJAMESXD002":
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

                # Update battle state if found
                if battle_id and battle_id in self.active_battles:
                    self.active_battles[battle_id].result = result_key
                    opponent = self.active_battles[battle_id].opponent
                    active_count = len(self.active_battles)
                    await self.send_discord_message(
                        f"{emoji} **{result} vs {opponent}!** Record: {self.wins}W - {self.losses}L ({active_count} active)",
                        channel="battles"
                    )
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
                battle_id = None
                
                # First try exact substring match
                for bid in self.active_battles:
                    # Battle IDs often appear in replay URLs
                    if bid in replay_url:
                        battle_id = bid
                        break
                
                # If that fails, try to find most recently completed battle without replay
                if not battle_id:
                    for bid, state in self.active_battles.items():
                        if state.result and not state.replay_url:
                            battle_id = bid
                            break
                
                # Last resort: use find_battle_for_event
                if not battle_id:
                    battle_id = self.find_battle_for_event(line)

                # Only post if we haven't already posted this replay
                if replay_url not in self.posted_replays:
                    self.posted_replays.add(replay_url)

                    battle_state = self.active_battles.get(battle_id)
                    if battle_state:
                        battle_state.replay_url = replay_url
                        opponent = battle_state.opponent
                        await self.send_discord_message(
                            f"🔗 **Replay (vs {opponent}):** {replay_url}",
                            channel="battles"
                        )

                        # If this was a loss, analyze it automatically
                        if battle_state.result == "lost":
                            await self.send_discord_message(
                                f"🔍 **Analyzing loss vs {opponent}...**",
                                channel="battles"
                            )
                            # Run analysis in background to not block other battles
                            asyncio.create_task(self.analyze_loss_async(replay_url, battle_state))

                        # Clean up completed battle
                        del self.active_battles[battle_id]
                    else:
                        await self.send_discord_message(
                            f"🔗 **Replay:** {replay_url}",
                            channel="battles"
                        )

    async def run_bot(self):
        """Run the bot process and monitor it"""
        cmd = [
            "venv/bin/python", "-u", "run.py",  # -u for unbuffered output
            "--websocket-uri", "wss://sim3.psim.us/showdown/websocket",
            "--ps-username", "LEBOTJAMESXD002",
            "--ps-password", "LeBotPassword2026!",
            "--bot-mode", "search_ladder",
            "--pokemon-format", "gen9ou",
            "--team-name", "gen9/ou",
            "--search-time-ms", "1500",  # Faster to avoid timeouts
            "--run-count", "999999",
            "--save-replay", "always",
            "--log-level", "INFO"  # Less verbose, focus on important info
        ]

        # Ensure we're in the right directory
        cwd = Path(__file__).parent

        # Activate venv and run
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd
        )

        await self.send_discord_message(
            "🚀 **Fouler Play bot starting...**",
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
