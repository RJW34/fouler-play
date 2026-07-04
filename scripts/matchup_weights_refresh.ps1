# HERMES matchup-weights refresher launcher (2026-06-23, claude)
# Runs the deterministic loss->weights refresher so the live policy bias keeps
# learning from new losses. No LLM, no code-gen. Single-shot, exits fast.
$ErrorActionPreference = 'SilentlyContinue'
$repo   = 'D:\Projects\fouler-play'
$python = 'D:\Projects\fouler-play\.venv\Scripts\python.exe'
$script = 'D:\Projects\fouler-play\scripts\refresh_matchup_weights.py'
Set-Location $repo
& $python $script 500 *> (Join-Path $repo 'logs\matchup_weights_refresh.launch.log')
exit 0
