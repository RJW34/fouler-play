#!/bin/bash
# Wrapper script for replay analyzer cron job

cd /home/ryan/projects/fouler-play
/home/ryan/projects/fouler-play/venv/bin/python infrastructure/replay_analyzer.py >> infrastructure/replay_analyzer.log 2>&1
