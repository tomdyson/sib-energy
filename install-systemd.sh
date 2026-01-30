#!/bin/bash

# Install systemd service and timer for SIB Energy updates
# Run this script once to set up nightly automated updates

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_DIR="$SCRIPT_DIR/systemd"

echo "Installing SIB Energy systemd service and timer..."

# Create log directory
echo "Creating log directory..."
sudo mkdir -p /var/log/sib-energy
sudo chown tom:tom /var/log/sib-energy

# Install systemd units
echo "Installing systemd units..."
sudo cp "$SYSTEMD_DIR/sib-energy-update.service" /etc/systemd/system/
sudo cp "$SYSTEMD_DIR/sib-energy-update.timer" /etc/systemd/system/

# Install logrotate config
echo "Installing logrotate configuration..."
sudo cp "$SYSTEMD_DIR/sib-energy-logrotate.conf" /etc/logrotate.d/sib-energy

# Reload systemd
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable and start timer
echo "Enabling and starting timer..."
sudo systemctl enable sib-energy-update.timer
sudo systemctl start sib-energy-update.timer

echo ""
echo "Installation complete!"
echo ""
echo "Useful commands:"
echo "  View timer status:     systemctl status sib-energy-update.timer"
echo "  View service status:   systemctl status sib-energy-update.service"
echo "  View logs:             journalctl -u sib-energy-update.service"
echo "  View next run time:    systemctl list-timers sib-energy-update.timer"
echo "  Run manually:          sudo systemctl start sib-energy-update.service"
echo "  Disable timer:         sudo systemctl disable sib-energy-update.timer"
echo ""
echo "IMPORTANT: Add your ntfy.sh topic to .env:"
echo "  echo 'NTFY_TOPIC=your-unique-topic-name' >> $SCRIPT_DIR/.env"
echo ""
echo "Then subscribe to notifications at: https://ntfy.sh/your-unique-topic-name"
