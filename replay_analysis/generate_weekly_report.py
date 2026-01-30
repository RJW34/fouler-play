#!/usr/bin/env python3
"""
Generate weekly improvement report and post to Discord
Run this via cron: 0 0 * * 0 (every Sunday at midnight)
"""

import sys
from pathlib import Path
import requests
import os
from dotenv import load_dotenv

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")

# Import analyzer
sys.path.append(str(Path(__file__).parent.parent))
from replay_analysis.analyzer import ReplayAnalyzer


def post_to_discord(message: str):
    """Post report to Discord"""
    if not DISCORD_WEBHOOK:
        print("No webhook configured")
        return
        
    payload = {"content": message}
    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if response.status_code == 204:
            print("Report posted to Discord")
        else:
            print(f"Failed to post: {response.status_code}")
    except Exception as e:
        print(f"Error posting to Discord: {e}")


def main():
    analyzer = ReplayAnalyzer()
    report = analyzer.generate_report()
    
    # Format for Discord (split into chunks if needed)
    discord_msg = f"📊 **Weekly Improvement Report**\n```\n{report}\n```"
    
    # Discord has 2000 char limit, split if needed
    if len(discord_msg) > 2000:
        # Post summary + link to full report
        summary = report.split("## Recommended Improvements")[0]
        discord_msg = f"📊 **Weekly Improvement Report**\n```\n{summary}\n```\n*Full report saved locally*"
    
    post_to_discord(discord_msg)
    print(report)


if __name__ == "__main__":
    main()
