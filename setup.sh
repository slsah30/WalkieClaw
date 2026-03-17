#!/bin/bash
# =============================================================================
# WalkieClaw Bridge — Setup Reference Script
# =============================================================================
# WARNING: This is a REFERENCE — not meant to be run all at once!
# Read through it and run the relevant sections manually on the correct machines.
#
# For the recommended local setup, use the Node.js bridge instead:
#   cd walkieclaw-bridge && npm install && npm run build && npm link
#   walkieclaw-bridge
#
# This script covers the Python (VPS) bridge setup.
# =============================================================================

echo "=== WalkieClaw Bridge Setup Reference ==="
echo ""
echo "WARNING: This is a reference script — do NOT run it blindly!"
echo "Read through it and run the relevant sections manually."
echo ""
echo "For the recommended local setup, use the Node.js bridge instead:"
echo "  See walkieclaw-bridge/README.md"
echo ""
echo "This script covers the Python (VPS) bridge deployment:"
echo ""

# =============================================================================
# OPTION A: LOCAL MODE — Bridge on your PC
# =============================================================================
cat << 'LOCAL_SETUP'

=== LOCAL MODE (bridge.py on your PC) ===

1. Install Python dependencies:
   python3 -m venv venv
   source venv/bin/activate      # or venv\Scripts\activate on Windows
   pip install -r requirements.txt

2. Install ffmpeg:
   # Ubuntu/Debian: apt install ffmpeg
   # macOS: brew install ffmpeg
   # Windows: download from https://ffmpeg.org/download.html

3. Configure:
   cp .env.example .env
   # Edit .env — set HTTP_ADVERTISE_HOST to your PC's LAN IP
   # Set BRIDGE_API_KEY (must match secrets.yaml)
   # Set OPENCLAW_TOKEN

4. Install and run OpenClaw (Node.js):
   # See https://github.com/openclaw for installation
   # OpenClaw must be running on port 18789

5. Run the bridge:
   python bridge.py

LOCAL_SETUP

# =============================================================================
# OPTION B: VPS MODE — Bridge on a remote server
# =============================================================================
cat << 'VPS_SETUP'

=== VPS MODE (bridge.py on remote server) ===

1. On VPS, create project directory:
   mkdir -p /opt/walkieclaw-bridge
   cd /opt/walkieclaw-bridge

2. Copy project files (bridge.py, requirements.txt) to VPS

3. Install dependencies:
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   apt install -y ffmpeg

4. Configure:
   cp .env.example .env
   # Edit .env — set HTTP_ADVERTISE_HOST to your VPS public IP
   # Set BRIDGE_API_KEY (must match secrets.yaml)
   # Set OPENCLAW_TOKEN

5. Open firewall:
   ufw allow 12345/udp comment "WalkieClaw audio"
   ufw allow 8080/tcp comment "WalkieClaw HTTP"

6. Create systemd service:

   cat > /etc/systemd/system/walkieclaw-bridge.service << 'EOF'
[Unit]
Description=WalkieClaw Voice Bridge
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/walkieclaw-bridge
EnvironmentFile=/opt/walkieclaw-bridge/.env
ExecStart=/opt/walkieclaw-bridge/venv/bin/python3 bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

   systemctl daemon-reload
   systemctl enable --now walkieclaw-bridge

VPS_SETUP

# =============================================================================
# ESP32 FIRMWARE (both modes)
# =============================================================================
cat << 'FLASH_COMMANDS'

=== FLASH ESP32 (run on machine with USB access) ===

1. Install ESPHome:
   pip install esphome

2. (Optional) Back up stock firmware:
   pip install esptool
   esptool -p /dev/ttyACM0 -b5000000 read_flash 0 0x1000000 aipi-original-backup.bin

3. Configure secrets:
   cp secrets.yaml.example secrets.yaml
   # Edit secrets.yaml — fill in WiFi, API key, etc.

4. Configure the device:
   # After flashing, visit http://<device_ip>/ in your browser
   # Set Bridge Host to your VPS IP or local PC LAN IP
   # Set Bridge API Key to match your bridge's key

5. Flash:
   esphome run walkieclaw.yaml

6. Watch logs to verify:
   esphome logs walkieclaw.yaml

FLASH_COMMANDS

echo ""
echo "=== Setup reference complete ==="
echo "See README.md for full documentation."
