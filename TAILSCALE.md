# WalkieClaw: Tailscale Networking Guide

## Overview

The ESP32 can't run Tailscale natively, so we rely on your existing subnet
router to bridge the gap. This guide covers two deployment options:

```
OPTION A: Bridge on VPS (simplest — everything lives on the VPS)
══════════════════════════════════════════════════════════════════

┌──────────┐  UDP/HTTP   ┌───────────────┐  Tailscale    ┌──────────────────────────┐
│ WalkieClaw│ ──────────> │ Subnet Router │ ═══════════>  │ VPS (100.x.x.x)         │
│ ESP32-S3 │ <────────── │ (your LAN)    │ <═══════════  │  ├─ bridge.py            │
│          │             └───────────────┘               │  ├─ OpenClaw/OpenClaw        │
│ 192.168.x│                                             │  └─ Whisper + TTS        │
└──────────┘                                             └──────────────────────────┘

  - ESP32 sends UDP audio to VPS Tailscale IP (100.x.x.x)
  - VPS runs bridge.py + OpenClaw on the same box
  - Subnet router handles routing ESP32 ↔ Tailscale
  - Zero local infrastructure needed


OPTION B: Bridge on Local Machine (lower latency, local STT)
══════════════════════════════════════════════════════════════

┌──────────┐  UDP/HTTP   ┌──────────────────┐  Tailscale   ┌──────────────────┐
│ WalkieClaw│ ──────────> │ Local Machine    │ ═══════════> │ VPS (100.x.x.x) │
│ ESP32-S3 │ <────────── │ (Tailscale node) │ <══════════  │  └─ OpenClaw/OpenClaw│
│          │             │  ├─ bridge.py    │              └──────────────────┘
│ 192.168.x│             │  ├─ Whisper STT  │
└──────────┘             │  └─ TTS engine   │
                         └──────────────────┘

  - ESP32 sends UDP audio to local machine's LAN IP
  - bridge.py runs locally (faster STT, no WAN latency on audio)
  - Only the text query goes over Tailscale to OpenClaw on VPS
  - Best for: slower internet, GPU-accelerated Whisper locally
```

---

## Option A: Bridge on VPS

Everything runs on your VPS. The WalkieClaw sends audio over your subnet
router → Tailscale → VPS. Simplest setup, but audio traverses the WAN.

### Network Requirements

- Your subnet router advertises the WalkieClaw's LAN (e.g., `192.168.1.0/24`)
- Your VPS accepts the subnet routes in Tailscale ACLs
- UDP port 12345 and HTTP port 8080 open on the VPS Tailscale interface

### ESPHome YAML Changes

In `walkieclaw.yaml`, set the bridge host to your **VPS Tailscale IP**:

```yaml
substitutions:
  device_name: "walkieclaw"
  friendly_name: "WalkieClaw Terminal"
  # Bridge host is now set at runtime via the device's web UI.
  # Visit http://<device_ip>/ after flashing to configure.
```

### VPS .env Configuration

```bash
# --- Network ---
# Listen on Tailscale interface (or 0.0.0.0 for all)
UDP_HOST=0.0.0.0
UDP_PORT=12345
HTTP_HOST=0.0.0.0
HTTP_PORT=8080

# --- ESPHome Device ---
# WalkieClaw's LAN IP — reachable from VPS via subnet routing
ESPHOME_HOST=192.168.1.50
ESPHOME_PORT=6053

# --- OpenClaw ---
# OpenClaw is on the SAME machine, so use localhost
OPENCLAW_URL=http://127.0.0.1:3000/webhook
OPENCLAW_TOKEN=your-token

# --- STT/TTS ---
WHISPER_MODEL=base
TTS_ENGINE=gtts
```

### VPS Tailscale Firewall

Make sure your VPS allows inbound on the Tailscale interface:

```bash
# Check that Tailscale is receiving traffic
sudo tailscale status

# If using ufw on the VPS:
sudo ufw allow in on tailscale0 to any port 12345 proto udp
sudo ufw allow in on tailscale0 to any port 8080 proto tcp

# If using iptables directly:
sudo iptables -A INPUT -i tailscale0 -p udp --dport 12345 -j ACCEPT
sudo iptables -A INPUT -i tailscale0 -p tcp --dport 8080 -j ACCEPT
```

### Verify Subnet Routing

From your VPS, confirm you can reach the WalkieClaw's LAN IP:

```bash
# On VPS:
ping 192.168.1.50  # Should work via subnet route

# If not, check that your subnet router is advertising:
# On the subnet router machine:
tailscale status
# Should show: "Subnets: 192.168.1.0/24"

# And the VPS is accepting the routes:
tailscale status --peers
```

### HTTP URL Rewriting

The bridge auto-detects its LAN IP for WAV URLs sent to the ESP32. When
running on VPS, the ESP32 needs to reach the VPS — so the bridge must use
the Tailscale IP in the URL it tells the ESP32 to fetch.

Add this to the top of `bridge.py` CONFIG or set as env var:

```bash
# Force the bridge to advertise this IP in WAV URLs
# (instead of auto-detecting, which would pick the VPS's public IP)
HTTP_ADVERTISE_HOST=100.x.x.x
```

Then in `bridge.py`, update the `command_play_tts` function. Replace the
auto-detect block with:

```python
# In command_play_tts():
advertise_host = os.getenv("HTTP_ADVERTISE_HOST", "")
if advertise_host:
    lan_ip = advertise_host
elif CONFIG["HTTP_HOST"] == "0.0.0.0":
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
    finally:
        s.close()
else:
    lan_ip = CONFIG["HTTP_HOST"]
```

### Latency Considerations

Audio round-trip: ESP32 → subnet router → Tailscale WireGuard tunnel → VPS
→ Whisper STT → OpenClaw → TTS → VPS HTTP → Tailscale → subnet router → ESP32

Expect ~2-5 seconds total depending on:
- Your WAN upload speed (raw PCM audio is ~32KB/sec at 16kHz/16bit/mono)
- Whisper model size (base ≈ 1-2s, small ≈ 2-4s on CPU)
- OpenClaw response time
- gTTS network call (or ElevenLabs API latency)

---

## Option B: Bridge on Local Machine

The bridge runs on a machine on your LAN (could be a Pi, your desktop,
a mini PC, etc). Only text goes over Tailscale to OpenClaw. Audio stays local.

### Network Flow

1. ESP32 sends UDP audio to local bridge (LAN, <1ms)
2. Bridge transcribes locally with Whisper (no network)
3. Bridge sends text to OpenClaw over Tailscale (small payload, fast)
4. Bridge generates TTS locally (no network if using local TTS)
5. Bridge serves WAV to ESP32 over LAN (fast)

### ESPHome YAML Changes

```yaml
substitutions:
  device_name: "walkieclaw"
  friendly_name: "WalkieClaw Terminal"
  # Bridge host is now set at runtime via the device's web UI.
  # Visit http://<device_ip>/ after flashing to configure.
```

### Local Machine .env Configuration

```bash
# --- Network ---
UDP_HOST=0.0.0.0
UDP_PORT=12345
HTTP_HOST=0.0.0.0
HTTP_PORT=8080

# --- ESPHome Device ---
# WalkieClaw LAN IP (same subnet, direct)
ESPHOME_HOST=192.168.1.50
ESPHOME_PORT=6053

# --- OpenClaw ---
# OpenClaw on VPS, reached via Tailscale
OPENCLAW_URL=http://100.x.x.x:3000/webhook
OPENCLAW_TOKEN=your-token

# --- STT/TTS ---
# Can use larger model locally if you have GPU
WHISPER_MODEL=small
WHISPER_DEVICE=cpu  # or "cuda" if you have a GPU
TTS_ENGINE=elevenlabs  # or gtts for free
ELEVENLABS_API_KEY=your-key
ELEVENLABS_VOICE_ID=your-voice-id
```

### No Firewall Changes Needed

Everything stays on your LAN or goes through Tailscale's encrypted tunnel.
No ports to open on your router.

### Latency Considerations

Much faster than Option A for the audio path:
- UDP audio: LAN only (~0ms)
- Whisper STT: local (~1-2s CPU, <0.5s GPU)
- OpenClaw query: Tailscale tunnel (~50-200ms for text)
- TTS: local gTTS/ElevenLabs API (~0.5-2s)
- WAV playback: LAN HTTP (~0ms)

Total: ~2-4 seconds, mostly dominated by STT + TTS.

---

## Tailscale ACL Considerations

If you're using Tailscale ACLs (recommended), ensure:

```json
{
  "acls": [
    {
      // Allow VPS to reach LAN devices via subnet router
      "action": "accept",
      "src": ["tag:vps"],
      "dst": ["192.168.1.0/24:*"]
    },
    {
      // Allow LAN subnet router to reach VPS
      "action": "accept", 
      "src": ["tag:subnet-router"],
      "dst": ["tag:vps:*"]
    },
    {
      // If bridge is on a separate local machine
      "action": "accept",
      "src": ["tag:bridge"],
      "dst": ["tag:vps:3000"]
    }
  ],
  "autoApprovers": {
    "routes": {
      "192.168.1.0/24": ["tag:subnet-router"]
    }
  }
}
```

---

## Quick Decision Guide

| Factor                    | Option A (VPS)          | Option B (Local)        |
|---------------------------|-------------------------|-------------------------|
| Setup complexity          | Simpler (1 machine)     | Two machines to manage  |
| Audio latency             | Higher (WAN round-trip) | Lower (LAN only)        |
| STT quality               | CPU-bound on VPS        | Can use GPU locally     |
| Dependency on internet    | Full (audio over WAN)   | Partial (only text)     |
| Cost                      | VPS CPU for Whisper     | Local hardware needed   |
| Best for                  | Quick setup, testing    | Daily use, low latency  |

**Recommendation:** Start with Option A to validate the full pipeline works end-to-end. Migrate to Option B if latency matters -- just move bridge.py to a local machine and change the .env file. The firmware and bridge code are identical either way; only the IPs change.

---

## Testing the Tailscale Path

### From VPS → WalkieClaw (Option A)

```bash
# On VPS, verify subnet route works:
ping 192.168.1.50

# Test ESPHome API reachability:
nc -zv 192.168.1.50 6053

# Start bridge and check UDP:
python3 bridge.py
# Then from another terminal on VPS:
echo "test" | nc -u 100.x.x.x 12345
# Bridge should log: "Processing 5 bytes of audio..."
```

### From WalkieClaw → VPS (verify ESP32 can reach VPS)

After flashing, check the ESPHome logs:
```bash
esphome logs walkieclaw.yaml
```

Look for:
```
[I][wifi:connected] Connected to 'YourSSID'
[I][api:connected] Connected to API
```

The ESP32 will try to send UDP to the bridge_host IP. If subnet routing
is working, packets from 192.168.1.x will reach 100.x.x.x via Tailscale.

---

## Systemd Service (VPS — Option A)

```ini
[Unit]
Description=WalkieClaw OpenClaw Voice Bridge
After=network.target tailscaled.service
Wants=tailscaled.service

[Service]
Type=simple
User=your-user
WorkingDirectory=/opt/aipi-openclaw-bridge
EnvironmentFile=/opt/aipi-openclaw-bridge/.env
ExecStart=/opt/aipi-openclaw-bridge/venv/bin/python3 bridge.py
Restart=always
RestartSec=5
# Ensure Tailscale is up before starting
ExecStartPre=/usr/bin/tailscale status

[Install]
WantedBy=multi-user.target
```
