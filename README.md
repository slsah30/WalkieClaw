# WalkieClaw

**A tiny AI walkie-talkie you can build yourself.**

Push a button. Talk. Get a spoken AI response from the little speaker. That's it. No app, no cloud subscription, no Alexa — just a $15 dev board, a Python script, and your own AI agent.

Built on the [AIPI Lite](https://github.com/xorigin-ai) (ESP32-S3) and powered by [OpenClaw](https://github.com/openclaw) for the AI brain, [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for speech-to-text, and [Edge TTS](https://github.com/rany2/edge-tts) for text-to-speech.

Works on home WiFi or your phone's hotspot. Take it anywhere.

## What It Does

```
You press the button and say:     "Hey, what's the weather like today?"

The display shows:                 Listening... → Transcribing... → Thinking... → Speaking...

The speaker says:                  "It's sunny and 72 degrees — perfect day to be outside!"

The LED changes color:             Red → Blue → Orange → Green (ready again)
```

## How It Works

```
  +--------------+         UDP audio        +------------------+
  |  AIPI Lite   | -----------------------> |                  |
  |  (ESP32-S3)  |                          |   bridge.py      |
  |              |    HTTP poll (2s)        |   (your PC/VPS)  |
  |  Mic ------> | -----------------------> |                  |
  |  Speaker <-- | <--- HTTP GET WAV ------- |  Whisper STT     |
  |  128x128 LCD |                          |  OpenClaw AI     |
  |  WS2812 LED  |                          |  Edge TTS        |
  |  Two buttons |                          +------------------+
  +--------------+
```

All connections are ESP32-initiated (outbound only). No port forwarding, no tunnels, no cloud relay. The device just talks to your bridge over plain HTTP and UDP.

## Hardware

You need one thing: an **AIPI Lite** board (~$15). Everything else is built in.

| Component | Details |
|-----------|---------|
| Board | AIPI Lite — ESP32-S3-WROOM-1, 16MB flash, 8MB PSRAM |
| Audio | ES8311 codec, MEMS mic, 8-ohm 0.8W speaker |
| Display | 128x128 TFT LCD (ST7735, SPI) |
| LED | WS2812 addressable RGB |
| Buttons | Right (GPIO42) = Talk, Left (GPIO1) = Volume |
| Power | USB-C or LiPo battery (GPIO10 power latch) |

The AIPI Lite board was designed by [xorigin AI](https://github.com/xorigin-ai) — thanks for making such a fun little piece of hardware! Board schematics and pinout are available in their repo.

## Quick Start

### 1. Set Up the Bridge (your PC or a VPS)

The bridge is a Python script that does the heavy lifting — speech recognition, AI chat, and text-to-speech. It runs on any machine with Python 3.10+.

```bash
git clone https://github.com/slsah30/WalkieClaw.git
cd aipi-openclaw

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Pick your deployment mode
cp local.env.example .env   # Bridge on your PC (same network as ESP32)
# OR
cp vps.env.example .env     # Bridge on a remote server

# Edit .env — fill in your IPs and API key
# Then run it:
python bridge.py
```

You'll also need [OpenClaw](https://github.com/openclaw) running (that's your AI agent). The bridge talks to it via the chat completions API.

### 2. Flash the ESP32

```bash
pip install esphome

# Set up your WiFi credentials
cp secrets.yaml.example secrets.yaml
# Edit secrets.yaml — WiFi SSID/password, OTA password

# Flash!
esphome run aipi-openclaw-direct.yaml
```

### 3. First-Time Device Setup

No YAML editing needed for the bridge connection — it's all done on-device:

1. **Power on.** If no WiFi is configured, it creates a `WalkieClaw-Fallback` AP.
2. **Connect** to the AP from your phone. Enter your WiFi credentials in the captive portal.
3. **Visit** `http://<device_ip>/` in a browser once it joins your network.
4. **Set** two fields: **Bridge Host** (your PC/VPS IP) and **Bridge API Key** (must match your `.env`).
5. **Done.** Settings persist across reboots. Start talking!

### 4. Use It!

| Action | What Happens |
|--------|-------------|
| **Hold right button** | Records your voice (LED turns red) |
| **Release button** | Sends to AI, processes response (LED turns blue) |
| **Response ready** | Plays through speaker (LED turns orange) |
| **Done** | Ready for next question (LED turns green) |
| **Click left button** | Cycles volume: Off → Low → Med → Normal → Loud |

## Deployment Modes

**Local mode** — bridge runs on your PC (same LAN):
- Lowest latency
- Great for development and tinkering
- PC must be on while using the device

**VPS mode** — bridge runs on a remote server:
- Works from anywhere with internet
- Requires firewall rules for UDP:12345 and TCP:8080
- Set it up once and forget about it

Both use the same `bridge.py` — only the `.env` config differs.

## Display & State Machine

The 128x128 LCD shows everything at a glance:

```
+---------------------------+
|       WalkieClaw          |   ← Title (pink, Inter font)
| [====== IP bar =========] |   ← Blue banner with IP + WiFi signal
| Bridge: 1.2.3.4    75%   |   ← Connection status + battery
| Ready                     |   ← Current state (green)
| Hello from your AI!       |   ← Last response (scrolls if long)
|            [====]         |   ← VU meter (animates during audio)
+---------------------------+
```

State machine:
```
IDLE → RECORDING → POLLING → READY_TO_PLAY → PLAYING → IDLE
                      |
                (timeout 90s)
                      ↓
                    IDLE
```

## Display Simulator

Want to tweak colors and layout without flashing? Open `simulator.html` in your browser — it's a pixel-perfect replica of the 128x128 display with interactive controls, color pickers, and YAML export. Change a color, see it instantly.

## Security

Everything is protected with a shared API key:

| Endpoint | Auth | Notes |
|----------|------|-------|
| `GET /health` | X-API-Key header | Health check + command delivery |
| `GET /api/response` | X-API-Key header | Poll for AI response (rate limited) |
| `GET /audio/*.wav` | Open | Filenames are unguessable hashes |
| UDP:12345 | `START:<key_prefix>` | 8-char key prefix on audio start |

## Files

| File | What It Does |
|------|-------------|
| `aipi-openclaw-direct.yaml` | ESPHome firmware — the main config |
| `bridge.py` | Voice bridge (Whisper + OpenClaw + Edge TTS) |
| `udp_stream.h` | C++ UDP audio sender with auth |
| `wifi_connect.h` | WiFi network switching helper |
| `config.html` | On-device setup page |
| `simulator.html` | Browser-based display simulator |
| `upd_relay.py` | UDP relay (for indirect routing setups) |
| `secrets.yaml.example` | Template for WiFi/OTA credentials |
| `local.env.example` | Bridge config for local mode |
| `vps.env.example` | Bridge config for VPS mode |
| `setup.sh` | Step-by-step setup reference |
| `requirements.txt` | Python dependencies |

## Things We Learned the Hard Way

So you don't have to:

- **GPIO0 is a trap** on ESP32-S3 — it's a strapping pin, don't use it
- **ES8311 register 0x12** (ADC/mic) — writing to it corrupts the DAC. Just don't.
- **I2S bus is shared** between mic and speaker — need a 300ms delay when switching
- **ST7735 bar widgets have inverted colors** — set the XOR of what you actually want
- **LVGL montserrat_12 doesn't exist** — font sizes jump from 10 to 14
- **Unicode will crash LVGL** — sanitize all AI text to ASCII before display
- **ESP32 I2S sends 32-bit frames** even when you configure 16-bit
- **ESPHome uses `request_headers`** not `headers` for HTTP requests
- **`media_player` can't send custom headers** — keep WAV endpoints open but unguessable

## Credits & Thanks

This project wouldn't exist without these amazing open-source tools and hardware:

- **[Robert Lipe](https://www.robertlipe.com/449-2/)** — reverse engineered the AIPI Lite hardware, mapped the GPIO pinout, identified the ES8311 codec and display controller, and documented everything so the rest of us could build on it. This project literally starts with his work.
- **[AIPI Lite](https://github.com/xorigin-ai)** by xorigin AI — the ESP32-S3 dev board with built-in mic, speaker, LCD, and LED. A fantastic little board for voice projects.
- **[ESPHome](https://esphome.io/)** — the firmware framework that makes ESP32 development actually enjoyable.
- **[LVGL](https://lvgl.io/)** — the graphics library powering the 128x128 display UI.
- **[OpenClaw](https://github.com/openclaw)** — the AI agent platform that gives WalkieClaw its brain.
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** by SYSTRAN — blazing fast speech-to-text, runs great even on a CPU.
- **[Edge TTS](https://github.com/rany2/edge-tts)** by rany2 — free, high-quality text-to-speech with dozens of voices.
- **[Espressif](https://www.espressif.com/)** — for the ESP32-S3 chip that makes this all possible.

## Contributing

Found a bug? Have an idea? Want to add support for a different AI backend? Open an issue or PR — this project is meant to be built on.

Some ideas for contributors:
- Support for other AI backends (Ollama, local LLMs, etc.)
- Wake word detection (hands-free mode)
- Multi-language support
- Custom LVGL themes and animations
- Battery optimization tricks
- 3D-printable case designs

## License

MIT — do whatever you want with it. Build one, mod it, sell it, teach with it. Just have fun.
