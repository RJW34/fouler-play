#!/usr/bin/env python3
"""
Fouler Play Bot Monitor - Event-driven Discord notifications
Monitors bot output and instantly posts battle results/replays to Discord
Also automatically analyzes losses for improvement opportunities
"""

import asyncio
import subprocess
import re
import json
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

class BotMonitor:
    def __init__(self):
        self.process = None
        self.wins = 0
        self.losses = 0
        self.current_battle = None
        self.current_replay = None
        self.last_result = None  # "won" or "lost"
        self.analyzer = ReplayAnalyzer()
        self.turn_reviewer = TurnReviewer()
        self.posted_replays = set()  # Track posted replays to avoid duplicates
        
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
    
    async def analyze_loss_async(self, replay_url):
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
                await self.send_discord_message("⚠️ **Analysis failed:** Could not fetch replay")
                return
            
            # Run mistake analysis
            analysis = await loop.run_in_executor(
                None,
                self.analyzer.analyze_loss,
                replay_url
            )
            
            if analysis and analysis.get("mistakes_found", 0) > 0:
                mistakes = analysis["mistakes_found"]
                msg = f"📊 **Analysis complete:** Found {mistakes} issue(s)"
                
                # List top mistakes
                for mistake in analysis["mistakes"][:3]:  # Top 3
                    msg += f"\n• **{mistake['severity'].upper()}**: {mistake['description']}"
                
                await self.send_discord_message(msg)
            else:
                # If we lost but found no mistakes, that's itself a problem
                await self.send_discord_message(
                    "⚠️ **Analysis:** Couldn't identify specific mistakes (analyzer needs improvement)"
                )
            
            # Extract critical turns for review
            await self.send_discord_message("🔍 **Extracting critical decision points...**", channel="feedback")
            
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
                await self.send_discord_message("ℹ️ No critical decision points identified", channel="feedback")
                
        except Exception as e:
            print(f"[MONITOR] Error analyzing loss: {e}")
            import traceback
            traceback.print_exc()
            await self.send_discord_message(
                f"⚠️ **Analysis failed:** {str(e)[:100]}"
            )
    
    async def monitor_output(self, stream):
        """Monitor bot output line by line"""
        async for line in stream:
            line = line.decode('utf-8').strip()
            print(line)  # Echo to stdout
            
            # Detect battle start
            match = BATTLE_START_PATTERN.search(line)
            if match:
                battle_id = match.group(1)
                opponent = match.group(2)
                self.current_battle = battle_id
                link = f"https://play.pokemonshowdown.com/{battle_id}"
                await self.send_discord_message(
                    f"🎮 **Battle started vs {opponent}**\n{link}",
                    channel="battles"
                )
            
            # Detect winner
            match = WINNER_PATTERN.search(line)
            if match:
                winner = match.group(1).strip()
                if winner == "LEBOTJAMESXD001":
                    self.wins += 1
                    emoji = "🎉"
                    result = "Won"
                    self.last_result = "won"
                elif winner == "None":
                    result = "Tie"
                    emoji = "🤝"
                    self.last_result = "tie"
                else:
                    self.losses += 1
                    emoji = "💀"
                    result = "Lost"
                    self.last_result = "lost"
                
                await self.send_discord_message(
                    f"{emoji} **{result}!** Record: {self.wins}W - {self.losses}L",
                    channel="battles"
                )
            
            # Detect battle end with team
            match = BATTLE_END_PATTERN.search(line)
            if match:
                result = match.group(1)
                team = match.group(2)
                # Additional info if we want team name
            
            # Detect replay link
            match = REPLAY_PATTERN.search(line)
            if match:
                replay_id = match.group(1)
                replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
                
                # Only post if we haven't already posted this replay
                if replay_url not in self.posted_replays:
                    self.posted_replays.add(replay_url)
                    self.current_replay = replay_url
                    
                    await self.send_discord_message(
                        f"🔗 **Replay:** {replay_url}",
                        channel="battles"
                    )
                    
                    # If this was a loss, analyze it automatically
                    if self.last_result == "lost":
                        await self.send_discord_message(
                            "🔍 **Analyzing loss for improvement opportunities...**",
                            channel="battles"
                        )
                        await self.analyze_loss_async(replay_url)
    
    async def run_bot(self):
        """Run the bot process and monitor it"""
        cmd = [
            "venv/bin/python", "-u", "run.py",  # -u for unbuffered output
            "--websocket-uri", "wss://sim3.psim.us/showdown/websocket",
            "--ps-username", "LEBOTJAMESXD002",
            "--ps-password", "LeBotPassword2026!",
            "--bot-mode", "search_ladder",
            "--pokemon-format", "gen9ou",
            "--team-name", "gen9/ou/fat-team-1-stall",
            "--playstyle", "fat",
            "--search-time-ms", "2000",
            "--run-count", "999999",
            "--save-replay", "always",
            "--log-level", "DEBUG"  # Keep DEBUG to see all logs
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
        
        await self.send_discord_message("🚀 **Fouler Play bot starting...**", channel="battles")
        
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
