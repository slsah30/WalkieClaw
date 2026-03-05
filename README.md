# WalkieClaw - AIPI Lite Voice Terminal

A walkie-talkie style voice assistant built on the AIPI Lite (ESP32-S3) that connects to an [OpenClaw](https://github.com/openclaw) AI agent via a VPS bridge. Push-to-talk, fully wireless, works on home WiFi or phone hotspot.

## Architecture (Direct-to-VPS)

```
Press Talk Button (GPIO42)
    |
    v
ESP32 captures mic audio (I2S, ES8311 codec)
    |
    v
Raw PCM streams over UDP:12345 --> VPS bridge (public IP)
    |                                   |
    |   (ESP32 polls HTTP every 2s)     v
    |                             Whisper STT transcribes
    |                                   |
    |                                   v
    |                             OpenClaw/Blam AI agent
    |                                   |
    |                                   v
    |                             Edge TTS synthesizes speech
    |                                   |
    v                                   v
ESP32 <-- HTTP GET /api/response -- Bridge stores result
    |
    v
ESP32 fetches WAV, plays via media_player
    |
    v
128x128 LCD shows scrolling response text
```

All connections are ESP32-initiated (outbound only). No tunnels, no relay, no port forwarding needed.

## Hardware

- **Board**: AIPI Lite (ESP32-S3-WROOM-1, 16MB flash, 8MB PSRAM octal)
- **Audio**: ES8311 codec (I2C control + I2S data), MEMS mic, 8ohm 0.8W speaker
- **Display**: 128x128 TFT LCD (SPI, ST7735)
- **LED**: WS2812 addressable RGB (GPIO46)
- **Buttons**: Right (GPIO42) = Talk, Left (GPIO1) = Volume cycle
- **Power**: GPIO10 latch (must be driven HIGH for battery operation)

## Files

| File | Location | Description |
|------|----------|-------------|
| `aipi-openclaw-direct.yaml` | Local (ESPHome) | Main firmware config (direct-to-VPS) |
| `udp_stream.h` | Local (ESPHome) | C++ UDP sender with keyed auth |
| `wifi_connect.h` | Local (ESPHome) | WiFi network switching helper |
| `secrets.yaml` | Local (ESPHome) | WiFi creds, API keys (gitignored) |
| `beep.wav` | Local (ESPHome) | Audio confirmation beep |
| `bridge.py` | VPS `/opt/aipi-openclaw-bridge/` | Voice bridge (STT + OpenClaw + TTS) |
| `.env` | VPS `/opt/aipi-openclaw-bridge/` | Bridge config (incl. `BRIDGE_API_KEY`) |
| `aipi-bridge.service` | VPS `/etc/systemd/system/` | Systemd service unit |

### OpenClaw Skills (VPS)

| Skill | Path | Description |
|-------|------|-------------|
| `walkieclaw-wifi` | `/root/.openclaw/skills/walkieclaw-wifi/` | Voice-triggered WiFi switching |

## Network

```
ESP32 (home WiFi or phone hotspot)
  ├── UDP:12345 --> VPS bridge.py      (mic audio + START/STOP markers)
  ├── HTTP GET  --> VPS:8080/api/response  (poll for AI response)
  ├── HTTP GET  --> VPS:8080/audio/*.wav   (fetch TTS audio)
  └── HTTP GET  --> VPS:8080/health        (30s health check + WiFi commands)
```

### WiFi

- **Home**: "Ranch Hill Resident" (priority 10, auto-connect)
- **Hotspot**: Phone hotspot "Travis" (priority 0, fallback)
- WiFi can be switched via voice command through OpenClaw

## Security

All endpoints are protected with a shared API key (`BRIDGE_API_KEY`):

| Endpoint | Auth | Rate Limit | Notes |
|----------|------|------------|-------|
| `GET /health` | X-API-Key header | - | Also delivers pending WiFi commands |
| `GET /api/response` | X-API-Key header | 30/min/IP | ESP32 poll endpoint |
| `GET /audio/*.wav` | Open | - | Filenames are unguessable (hash+timestamp) |
| `POST /api/connect_wifi` | X-API-Key header | - | Queue WiFi command for ESP32 |
| UDP:12345 | `START:<key_prefix>` | - | 8-char key prefix on START marker |

### UFW Rules (VPS)

| Rule | Port | Scope |
|------|------|-------|
| SSH | 22/tcp | Tailscale only (100.64.0.0/10) |
| OpenClaw | 18789 | Tailscale only |
| Bridge UDP | 12345/udp | Anywhere (auth'd by key) |
| Bridge HTTP | 8080/tcp | Anywhere (auth'd by key) |

## State Machine

```
IDLE (0) --> RECORDING (1) --> POLLING (2) --> READY_TO_PLAY (4) --> PLAYING (3) --> IDLE
                                  |
                            (timeout 45 polls = 90s)
                                  |
                                  v
                                IDLE
```

## Setup

### ESP32 (ESPHome)

1. Copy `secrets.yaml.example` to `secrets.yaml` and fill in:
   - `wifi_ssid`, `wifi_password` (home WiFi)
   - `hotspot_ssid`, `hotspot_password` (phone hotspot)
   - `bridge_api_key` (must match VPS `.env`)
   - `api_encryption_key`, `ota_password`
2. Install ESPHome: `pip install esphome`
3. Flash: `esphome run aipi-openclaw-direct.yaml`

### VPS Bridge

1. Create `/opt/aipi-openclaw-bridge/.env` with:
   ```
   UDP_PORT=12345
   HTTP_PORT=8080
   OPENCLAW_URL=http://127.0.0.1:18789
   OPENCLAW_TOKEN=<your_token>
   OPENCLAW_MODE=chat
   HTTP_ADVERTISE_HOST=<VPS_PUBLIC_IP>
   WHISPER_MODEL=small
   TTS_ENGINE=edge
   EDGE_TTS_VOICE=en-GB-RyanNeural
   BRIDGE_API_KEY=<same_key_as_secrets.yaml>
   ```
2. Install: `pip install aioesphomeapi faster-whisper edge-tts pydub aiohttp`
3. Run: `systemctl start aipi-bridge`

## Key Learnings

- **GPIO0 is unusable** on ESP32-S3 (strapping/boot pin). Power latch = GPIO10, left button = GPIO1.
- **ES8311 register 0x12** (ADC/mic) must NOT be written — it corrupts DAC output.
- **I2S bus is shared** between mic and speaker — need 300-500ms delay between mic stop and speaker start to avoid `ESP_ERR_INVALID_STATE`.
- **ESPHome `headers`** was renamed to **`request_headers`** in recent versions.
- **LVGL font montserrat_12 doesn't exist** — sizes jump from 10 to 14.
- **LVGL label updates** can silently fail — use direct C API (`lv_label_set_text`) for reliability.
- **Unicode crashes LVGL** — sanitize all AI text to ASCII before display.
- **ESP32 I2S sends 32-bit samples** even when configured for 16-bit — bridge extracts high 16 bits.
- **media_player can't send custom HTTP headers** — WAV endpoint must be open (no API key).
- **First request after bridge restart is slow** (~90s) due to ESPHome API timeout on unreachable hosts.
