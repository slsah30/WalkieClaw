#!/bin/bash
# =============================================================================
# WalkieClaw Bridge — Setup Reference Script
# =============================================================================
# This is NOT meant to be run all at once — it's a step-by-step reference.
# Run the relevant sections on the correct machines.
# =============================================================================

echo "=== WalkieClaw Bridge Setup ==="
echo ""
echo "Choose a deployment mode:"
echo "  LOCAL:  bridge.py + OpenClaw on your PC (same LAN as ESP32)"
echo "  VPS:    bridge.py + OpenClaw on a remote server"
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
   cp local.env.example .env
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
   cp vps.env.example .env
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

4. Edit walkieclaw.yaml:
   # Set bridge_host to your VPS IP or local PC LAN IP
   # Set the keyed_start prefix to first 8 chars of your bridge_api_key

5. Flash:
   esphome run walkieclaw.yaml

6. Watch logs to verify:
   esphome logs walkieclaw.yaml

FLASH_COMMANDS

echo ""
echo "=== Setup reference complete ==="
echo "See README.md for full documentation."
