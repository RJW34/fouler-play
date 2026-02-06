#!/bin/bash
# =============================================================================
# Install Fouler-Play as a systemd user service (DEKU)
# =============================================================================
# Run once: bash infrastructure/linux/install_service.sh
# After install: systemctl --user start fouler-play
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SERVICE_SRC="${REPO_DIR}/infrastructure/linux/fouler-play.service"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_DEST="${SERVICE_DIR}/fouler-play.service"

echo "Installing fouler-play systemd user service..."
echo "  Repo: ${REPO_DIR}"
echo "  Service file: ${SERVICE_DEST}"

# Create systemd user directory if needed
mkdir -p "$SERVICE_DIR"

# Copy service file
cp "$SERVICE_SRC" "$SERVICE_DEST"

# Reload systemd
systemctl --user daemon-reload

# Enable (start on login)
systemctl --user enable fouler-play.service

# Enable lingering so it runs even when not logged in
loginctl enable-linger "$(whoami)" 2>/dev/null || true

echo ""
echo "Installed successfully."
echo ""
echo "Commands:"
echo "  systemctl --user start fouler-play    # Start now"
echo "  systemctl --user stop fouler-play     # Stop"
echo "  systemctl --user restart fouler-play  # Restart"
echo "  systemctl --user status fouler-play   # Check status"
echo "  journalctl --user -u fouler-play -f   # Follow logs"
echo ""
echo "The service will auto-start on boot (lingering enabled)."
echo "It restarts automatically if it crashes (30s delay)."
