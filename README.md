# WalkieClaw

A walkie-talkie style voice assistant built on the AIPI Lite (ESP32-S3). Push a button, speak, and get an AI response through the speaker. Connects to an [OpenClaw](https://github.com/openclaw) AI agent via a Python bridge that handles speech-to-text (Whisper), AI chat, and text-to-speech (Edge TTS).

Works on home WiFi or phone hotspot. All connections are ESP32-initiated (outbound only) — no tunnels or port forwarding needed on the ESP32 side.

## Architecture

```
Press Talk Button (GPIO42)
        |
        v
ESP32 captures mic audio (I2S, ES8311 codec)
        |
        v
Raw PCM streams over UDP ──> bridge.py (on PC or VPS)
        |                          |
        |  (polls HTTP every 2s)   v
        |                    Whisper STT transcribes
        |                          |
        |                          v
        |                    OpenClaw AI agent
        |                          |
        |                          v
        |                    Edge TTS synthesizes speech
        |                          |
        v                          v
ESP32 <── HTTP GET ────── Bridge stores result + WAV
        |
        v
Speaker plays response, LCD shows text
```

## Deployment Modes

**Local mode** — bridge.py runs on your PC (same LAN as ESP32):
- Lowest latency, no internet dependency for the bridge hop
- Requires PC to be running while using the device

**VPS mode** — bridge.py runs on a remote server:
- Works from anywhere with internet
- Requires firewall rules for UDP:12345 and TCP:8080

Both modes use the exact same `bridge.py` — only the `.env` config differs.

## Hardware

- **Board**: AIPI Lite (ESP32-S3-WROOM-1, 16MB flash, 8MB PSRAM octal)
- **Audio**: ES8311 codec (I2C control + I2S data), MEMS mic, 8ohm 0.8W speaker
- **Display**: 128x128 TFT LCD (SPI, ST7735)
- **LED**: WS2812 addressable RGB (GPIO46)
- **Buttons**: Right (GPIO42) = Talk, Left (GPIO1) = Volume cycle
- **Power**: GPIO10 latch (must be driven HIGH for battery operation)

## Quick Start

### 1. Set up the bridge

**Prerequisites**: Python 3.10+, ffmpeg, Node.js (for OpenClaw)

```bash
# Clone the repo
git clone https://github.com/slsah30/aipi-openclaw.git
cd aipi-openclaw

# Install Python dependencies
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Choose your mode and configure
cp local.env.example .env   # Local mode (PC on same LAN)
# OR
cp vps.env.example .env     # VPS mode (remote server)

# Edit .env — fill in your IPs, API keys, etc.

# Run
python bridge.py
```

### 2. Flash the ESP32

```bash
pip install esphome

# Configure secrets (WiFi + OTA only — bridge settings are done on-device)
cp secrets.yaml.example secrets.yaml
# Edit secrets.yaml — fill in WiFi credentials

# Flash
esphome run aipi-openclaw-direct.yaml
```

### 3. First-Time Device Setup

Bridge host and API key are configured at runtime — no YAML edits needed.

1. **Flash** the firmware. If WiFi isn't configured yet, the device creates a `WalkieClaw-Fallback` AP.
2. **Connect** to the fallback AP from your phone/laptop. A captive portal opens — enter your WiFi credentials.
3. **Device joins WiFi**. The LCD shows `Setup needed` and `http://<ip>/`.
4. **Visit** `http://<device_ip>/` in a browser. You'll see ESPHome's built-in web UI.
5. **Set** the two text fields:
   - **Bridge Host** — your bridge IP (PC LAN IP or VPS public IP)
   - **Bridge API Key** — must match `BRIDGE_API_KEY` in your bridge `.env`
6. **Done.** Settings persist across reboots. The device immediately connects to the bridge.

### 4. Use it

Press the right button, speak, release. The LED shows status:
- **Red** = Listening (recording)
- **Blue** = Processing (waiting for AI)
- **Orange** = Speaking (playing response)
- **Green** = Ready (idle)

## Files

| File | Description |
|------|-------------|
| `aipi-openclaw-direct.yaml` | Main ESPHome firmware config |
| `udp_stream.h` | C++ UDP sender with keyed auth |
| `wifi_connect.h` | WiFi network switching helper |
| `bridge.py` | Voice bridge (Whisper STT + OpenClaw + Edge TTS) |
| `beep.wav` | Boot confirmation beep |
| `upd_relay.py` | UDP relay (legacy, for indirect routing) |
| `aipi-openclaw.yaml` | Legacy firmware (relay mode) |
| `secrets.yaml.example` | Template for ESPHome secrets |
| `local.env.example` | Bridge config template (local mode) |
| `vps.env.example` | Bridge config template (VPS mode) |
| `requirements.txt` | Python dependencies |
| `setup.sh` | Step-by-step setup reference |

## Security

All endpoints are protected with a shared API key (`BRIDGE_API_KEY`):

| Endpoint | Auth | Notes |
|----------|------|-------|
| `GET /health` | X-API-Key header | Health check + WiFi command delivery |
| `GET /api/response` | X-API-Key header | ESP32 poll endpoint (30 req/min/IP) |
| `GET /audio/*.wav` | Open | Filenames are unguessable (hash+timestamp) |
| `POST /api/connect_wifi` | X-API-Key header | Queue WiFi switch command |
| UDP:12345 | `START:<key_prefix>` | 8-char key prefix on START marker |

## State Machine

```
IDLE ──> RECORDING ──> POLLING ──> READY_TO_PLAY ──> PLAYING ──> IDLE
                          |
                    (timeout 90s)
                          |
                          v
                        IDLE
```

## Key Learnings

- **GPIO0 is unusable** on ESP32-S3 (strapping/boot pin)
- **ES8311 register 0x12** (ADC/mic) must NOT be written — it corrupts DAC output
- **I2S bus is shared** — need 300ms delay between mic stop and speaker start
- **ESPHome `headers`** was renamed to **`request_headers`** in recent versions
- **LVGL font montserrat_12 doesn't exist** — sizes jump from 10 to 14
- **Unicode crashes LVGL** — bridge sanitizes all AI text to ASCII
- **ESP32 I2S sends 32-bit samples** even when configured for 16-bit
- **media_player can't send custom HTTP headers** — WAV endpoint stays open

## License

MIT
