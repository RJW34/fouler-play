#!/bin/bash
# Create Discord webhook for Fouler Play notifications

CHANNEL_ID="1466691161363054840"
BOT_TOKEN=$(grep 'discord\.token' ~/.clawdbot/config.toml | cut -d'"' -f2)

curl -X POST \
  "https://discord.com/api/v10/channels/${CHANNEL_ID}/webhooks" \
  -H "Authorization: Bot ${BOT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "Fouler Play Bot"}' \
  2>&1
