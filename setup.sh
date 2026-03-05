#!/bin/bash
# =============================================================================
# AIPI Lite → OpenClaw Bridge: Tailscale + VPS Setup Script
# =============================================================================
# Run the relevant sections on the correct machines.
# This is NOT meant to be run all at once — it's a reference script.
# =============================================================================

echo "=== AIPI Lite → OpenClaw Bridge Setup ==="
echo ""
echo "Your network topology:"
echo "  VPS (srv1333895):   100.79.21.120 (Tailscale)"
echo "  MSI (home):         100.74.80.71  (Tailscale) / 172.20.5.68 (LAN)"
echo "  Home LAN:           172.20.0.0/16 (gateway 172.20.1.1)"
echo "  OpenClaw gateway:   127.0.0.1:18789 (loopback on VPS)"
echo "  AIPI Lite:          172.20.5.XXX (will get IP from DHCP)"
echo ""

# =============================================================================
# STEP 1: MSI — Enable Subnet Routing (run in PowerShell as Admin)
# =============================================================================
cat << 'MSI_COMMANDS'

=== RUN ON MSI (PowerShell, Administrator) ===

# Enable subnet routing so VPS can reach your home LAN
tailscale up --advertise-routes=172.20.0.0/16 --accept-routes

# Then go to Tailscale Admin Console:
#   https://login.tailscale.com/admin/machines
#   → Click on "msi"
#   → Under "Subnets", approve 172.20.0.0/16

MSI_COMMANDS

# =============================================================================
# STEP 2: VPS — Accept Routes + Firewall (run as root on srv1333895)
# =============================================================================
cat << 'VPS_COMMANDS'

=== RUN ON VPS (srv1333895) ===

# Accept subnet routes from MSI
tailscale up --accept-routes

# Verify you can reach the home LAN (after MSI subnet is approved)
# Replace 172.20.5.68 with MSI's LAN IP to test
ping -c 3 172.20.5.68

# Open firewall for bridge services on Tailscale interface
# UDP 12345 = audio from ESP32, TCP 8080 = WAV HTTP server
ufw allow in on tailscale0 to any port 12345 proto udp comment "AIPI audio UDP"
ufw allow in on tailscale0 to any port 8080 proto tcp comment "AIPI WAV HTTP"

# Verify firewall
ufw status numbered

VPS_COMMANDS

# =============================================================================
# STEP 3: VPS — Install Bridge Dependencies
# =============================================================================
cat << 'INSTALL_COMMANDS'

=== RUN ON VPS (srv1333895) ===

# Create project directory
mkdir -p /opt/aipi-openclaw-bridge
cd /opt/aipi-openclaw-bridge

# Copy project files here (from your download)
# cp ~/Downloads/aipi-openclaw-bridge/* /opt/aipi-openclaw-bridge/

# Set up Python venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install ffmpeg (needed by pydub for audio conversion)
apt update && apt install -y ffmpeg

# Copy production env
cp production.env .env

# ** EDIT .env: Update ESPHOME_HOST with AIPI Lite's actual IP after first flash **
nano .env

INSTALL_COMMANDS

# =============================================================================
# STEP 4: VPS — Create Systemd Service
# =============================================================================
cat << 'SERVICE_COMMANDS'

=== RUN ON VPS (srv1333895) ===

cat > /etc/systemd/system/aipi-bridge.service << 'EOF'
[Unit]
Description=AIPI Lite OpenClaw Voice Bridge
After=network.target tailscaled.service
Wants=tailscaled.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/aipi-openclaw-bridge
EnvironmentFile=/opt/aipi-openclaw-bridge/.env
ExecStart=/opt/aipi-openclaw-bridge/venv/bin/python3 bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable aipi-bridge

# Don't start yet — wait until AIPI Lite is flashed and IP is known
# systemctl start aipi-bridge

SERVICE_COMMANDS

# =============================================================================
# STEP 5: Flash AIPI Lite (run on any machine with USB + ESPHome)
# =============================================================================
cat << 'FLASH_COMMANDS'

=== RUN ON YOUR MACHINE WITH USB ACCESS ===

# Install ESPHome if not already
pip install esphome

# Back up stock firmware first!
pip install esptool
esptool -p /dev/ttyACM0 -b5000000 read_flash 0 0x1000000 aipi-original-backup.bin

# Edit secrets.yaml with your WiFi credentials
# Your WiFi network: the one on 172.20.x.x (same as MSI's Wi-Fi)
nano secrets.yaml

# Flash ESPHome firmware
esphome run aipi-openclaw.yaml

# If won't flash: remove 4 back screws, hold BOOT button while plugging USB

# Watch the logs to find the AIPI Lite's IP address
esphome logs aipi-openclaw.yaml
# Look for: [I][wifi:connected] Got IP: 172.20.X.X

FLASH_COMMANDS

# =============================================================================
# STEP 6: Update Bridge Config + Start
# =============================================================================
cat << 'FINAL_COMMANDS'

=== RUN ON VPS (srv1333895) ===

# Update .env with the AIPI Lite's actual IP from Step 5
nano /opt/aipi-openclaw-bridge/.env
# Change: ESPHOME_HOST=172.20.5.XXX  →  ESPHOME_HOST=172.20.X.X (actual IP)

# Verify VPS can reach the AIPI Lite via subnet routing
ping -c 3 172.20.X.X

# Start the bridge
systemctl start aipi-bridge

# Check logs
journalctl -u aipi-bridge -f

# You should see:
#   AIPI Lite → OpenClaw VPS Voice Bridge
#   Loading Whisper model: base on cpu...
#   Whisper model loaded.
#   Connected to ESPHome device: aipi-openclaw
#   Bridge is running!

FINAL_COMMANDS

# =============================================================================
# STEP 7: Test It!
# =============================================================================
cat << 'TEST_COMMANDS'

=== TESTING ===

# Quick test: verify OpenClaw endpoint works from VPS
curl -s -X POST http://127.0.0.1:18789/v1/chat/completions \
  -H "Authorization: Bearer 6b008909312112f1503f7873baf7404e9feae5ea650278f5" \
  -H "Content-Type: application/json" \
  -d '{"model":"openrouter/anthropic/claude-sonnet-4-6","messages":[{"role":"user","content":"say hello in one sentence"}]}'

# Press the right button on the AIPI Lite and speak!
# LED: Red = Listening, Yellow = Thinking, Green = Ready

# Debug: watch bridge logs
journalctl -u aipi-bridge -f

# Debug: check ESP32 logs
esphome logs aipi-openclaw.yaml

TEST_COMMANDS

echo ""
echo "=== Setup complete! ==="
echo "Key reminder: ROTATE YOUR API KEYS that were exposed in this chat session."
echo "  - OpenAI sk-proj-... key"
echo "  - OpenRouter key"  
echo "  - Telegram bot token"
echo "  - Gateway auth token"
echo "  - Google API keys"
