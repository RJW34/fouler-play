@echo off
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set LOSS_TRIGGERED_DRAIN=0
cd /d "%~dp0"
py -3 run.py ^
  --websocket-uri "wss://sim3.psim.us/showdown/websocket" ^
  --ps-username "claudechamp" ^
  --ps-password "claudechamp" ^
  --bot-mode search_ladder ^
  --pokemon-format gen9ou ^
  --run-count 90 ^
  --max-concurrent-battles 3 ^
  --search-parallelism 1 ^
  --max-mcts-battles 1 ^
  --team-names "gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-pivot,gen9/ou/fat-team-3-dondozo" ^
  --save-replay always ^
  --log-level INFO ^
  --log-to-file ^
  --decision-policy hybrid ^
  --spectator-username CHUNGMIGHT
pause
