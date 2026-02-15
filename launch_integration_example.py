"""
Example integration of pre_flight_check.py into launch.py

Add this to launch.py right before starting the bot:
"""

import sys
from pre_flight_check import run_pre_flight_check


def main():
    # ... existing argparse code ...
    parser = argparse.ArgumentParser(description="Launch fouler-play bot")
    parser.add_argument("battles", nargs="?", type=int, default=0)
    parser.add_argument("concurrent", nargs="?", type=int, default=1)
    # ... etc ...
    
    args = parser.parse_args()
    
    # Load .env as usual
    load_env()
    kill_stale_procs()
    
    # ==================== PRE-FLIGHT CHECK ====================
    # MUST pass before bot can start
    print("\n" + "="*60)
    print("Running pre-flight verification...")
    print("="*60)
    
    battles = 0 if args.battles == 0 else args.battles
    concurrent = args.concurrent
    
    if not run_pre_flight_check(run_count=battles, num_workers=concurrent):
        print("\n" + "="*60)
        print("[ERROR] Pre-flight check FAILED")
        print("Fix configuration issues above before launching batch")
        print("="*60)
        sys.exit(1)
    
    print("\n" + "="*60)
    print("[OK] Pre-flight verification passed")
    print("="*60 + "\n")
    # ==========================================================
    
    # ... rest of launch.py (OBS server, bot args, subprocess.run) ...


# Alternatively, for minimal integration, add just this line before subprocess.run():
# if not run_pre_flight_check(run_count=args.battles, num_workers=args.concurrent):
#     sys.exit(1)
