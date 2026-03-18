#!/usr/bin/env python3
"""
Direct autoresearch runner — avoids complex module imports.
Runs competitive analysis + run_cycle in one go.
"""
import sys
import subprocess

fp_root = "/mnt/d/Projects with Claude/fouler-play"

print("=" * 70)
print("FOULER PLAY AUTORESEARCH CYCLE")
print("=" * 70)

# Step 1: Competitive Analysis
print("\n[1/2] Running Competitive Analysis...")
print("-" * 70)
result_comp = subprocess.run(
    [sys.executable, "-X", "utf8", "-m", "infrastructure.autoresearch.matchup_analyzer"],
    cwd=fp_root,
    capture_output=True,
    text=True,
    timeout=60,
)
print(result_comp.stdout)
if result_comp.stderr:
    print("STDERR:", result_comp.stderr)

# Step 2: Standard Cycle
print("\n[2/2] Running Standard Analysis Cycle...")
print("-" * 70)
result_cycle = subprocess.run(
    [sys.executable, "-X", "utf8", "-m", "infrastructure.autoresearch.run_cycle", "--analyze"],
    cwd=fp_root,
    capture_output=True,
    text=True,
    timeout=120,
)
print(result_cycle.stdout)
if result_cycle.stderr:
    print("STDERR:", result_cycle.stderr)

print("\n" + "=" * 70)
print("CYCLE COMPLETE")
print("=" * 70)
