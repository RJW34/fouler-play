#!/usr/bin/env python3
"""Test Discord and OpenClaw notifications without running full analysis."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import Pipeline

def main():
    pipeline = Pipeline()
    
    # Use existing test report
    report_path = PROJECT_ROOT / "replay_analysis/reports/batch_0002_20260214_183921.md"
    
    if not report_path.exists():
        print(f"❌ Test report not found: {report_path}")
        return
    
    print(f"📄 Using test report: {report_path.name}\n")
    
    # Extract top issues
    content = report_path.read_text()
    analysis_section = content.split("## AI Analysis")[-1] if "## AI Analysis" in content else ""
    top_issues = pipeline._extract_top_issues(analysis_section)
    
    print(f"Top Issues:\n{top_issues}\n")
    
    # Test Discord notification
    print("📤 Testing Discord notification...")
    pipeline.send_discord_notification(report_path)
    
    # Test wake notification
    print("\n📤 Testing wake notification...")
    pipeline.send_wake_notification(report_path, top_issues)
    
    print("\n✅ Test complete!")

if __name__ == "__main__":
    main()
