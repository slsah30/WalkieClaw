# WalkieClaw - AIPI Lite Voice Terminal

A walkie-talkie style voice assistant built on the AIPI Lite (ESP32-S3) that connects to an [OpenClaw](https://github.com/openclaw) AI agent via a VPS bridge.

## How It Works

```
Press Talk Button (GPIO42)
    |
    v
ESP32 captures mic audio (I2S → ES8311)
    |
    v
Raw PCM streams over UDP → MSI relay → VPS bridge
    |
    v
Whisper STT transcribes speech
    |
    v
Text sent to OpenClaw/Blam AI agent
    |
    v
Response synthesized via Edge TTS (en-GB-RyanNeural)
    |
    v
WAV served over HTTP → ESP32 media_player streams it
    |
    v
128x128 LCD shows scrolling response text
```

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
| `aipi-openclaw.yaml` | ESP32 (ESPHome) | Main firmware config |
| `udp_stream.h` | ESP32 (ESPHome) | C++ UDP send helper |
| `beep.wav` | ESP32 (ESPHome) | Audio confirmation beep |
| `bridge.py` | VPS | Voice bridge (STT + OpenClaw + TTS) |
| `.env.example` | VPS | Bridge config template |
| `upd_relay.py` | MSI (Windows) | UDP relay: ESP32 WiFi → VPS Tailscale |
| `start_relay.bat` | MSI (Windows) | Auto-restart wrapper for relay |
| `setup.sh` | VPS | Initial VPS setup script |

## Network Topology

```
ESP32 (172.20.x.x WiFi)
  └─ UDP:12345 ──→ MSI (172.20.5.68) upd_relay.py
                      └─ forwards to VPS (Tailscale) bridge.py
                          ├─ HTTP:8080 serves WAV files
                          └─ ESPHome API:6053 controls display/speaker

Both reach ESP32 via MSI port forwards:
  MSI 172.20.5.68:8080 → VPS:8080
  MSI 172.20.5.68:6053 → ESP32:6053
```

## Setup

### ESP32 (ESPHome)

1. Copy `secrets.yaml.example` to `secrets.yaml` and fill in credentials
2. Install ESPHome: `pip install esphome`
3. Flash: `esphome run aipi-openclaw.yaml`

### VPS Bridge

1. Copy `.env.example` to `/opt/aipi-openclaw-bridge/.env`
2. Install dependencies: `pip install aioesphomeapi faster-whisper edge-tts pydub aiohttp`
3. Run: `python3 bridge.py` (or use the systemd service)

### MSI Relay (Windows)

1. Run `start_relay.bat` or set up the "WalkieClaw UDP Relay" scheduled task
2. Configure port forwards:
   ```
   netsh interface portproxy add v4tov4 listenport=8080 listenaddress=172.20.5.68 connectport=8080 connectaddress=<VPS_TAILSCALE_IP>
   netsh interface portproxy add v4tov4 listenport=6053 listenaddress=172.20.5.68 connectport=6053 connectaddress=<ESP32_IP>
   ```

## Key Learnings

- **GPIO0 is unusable** on ESP32-S3 (strapping/boot pin). Power latch is GPIO10, left button is GPIO1.
- **ES8311 register 0x12** (ADC/mic control) must NOT be written — it corrupts DAC output.
- **LVGL font montserrat_12 doesn't exist** — sizes jump from 10 to 14.
- **LVGL label updates** can silently fail if cached build has corrupted state — rename the label ID to force rebuild.
- **Unicode characters crash LVGL** — sanitize all AI response text to ASCII before display.
- **ESP32 I2S sends 32-bit samples** even when configured for 16-bit — bridge must extract high 16 bits.
