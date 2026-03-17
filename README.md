# WalkieClaw

**A tiny AI walkie-talkie you can build yourself.**

Push a button. Talk. Get a spoken AI response from the little speaker. That's it. No app, no cloud subscription, no Alexa — just a $15 dev board and your own [OpenClaw](https://github.com/openclaw) AI agent.

Works on home WiFi or your phone's hotspot. Take it anywhere.

## What It Does

```
You press the button and say:     "Hey, what's the weather like today?"

The display shows:                 Listening... → Processing... → Speaking...

The speaker says:                  "It's sunny and 72 degrees — perfect day to be outside!"

The LED changes color:             Red → Blue → Orange → Green (ready again)
```

You can interrupt at any time — press the button while it's talking to cut it off and ask something new.

## How It Works

```
  +--------------+         UDP audio        +------------------+
  |  ESP32-S3    | -----------------------> |                  |
  |              |                          |  WalkieClaw      |
  |              |    HTTP poll (2s)        |  Bridge           |
  |  Mic ------> | -----------------------> |                  |
  |  Speaker <-- | <--- HTTP GET WAV ------- |  Whisper STT     |
  |  128x128 LCD |                          |  OpenClaw AI     |
  |  WS2812 LED  |                          |  Edge TTS        |
  |  Two buttons |                          +------------------+
  +--------------+
```

All connections are ESP32-initiated (outbound only). No port forwarding, no tunnels, no cloud relay.

## Hardware

You need an **ESP32-S3 board** with a mic and speaker. We recommend the **AIPI Lite** (~$15) which has everything built in.

| Component | Details |
|-----------|---------|
| Board | AIPI Lite — ESP32-S3-WROOM-1, 16MB flash, 8MB PSRAM |
| Audio | ES8311 codec, MEMS mic, 8-ohm 0.8W speaker |
| Display | 128x128 TFT LCD (ST7735, SPI) |
| LED | WS2812 addressable RGB |
| Buttons | Right (GPIO42) = Talk, Left (GPIO1) = Volume |
| Power | USB-C or LiPo battery (GPIO10 power latch) |

The AIPI Lite board was designed by [xorigin AI](https://github.com/xorigin-ai) — thanks for making such a fun little piece of hardware!

## Quick Start

There are two ways to run the bridge. Pick whichever fits your setup.

### Option A: Local Bridge (Node.js — recommended for getting started)

Runs on your PC on the same network as the ESP32. Uses your GPU for fast speech recognition.

**Requirements:** Node.js 18+, Python 3.10+, NVIDIA GPU (optional but recommended), [OpenClaw](https://github.com/openclaw) (an open-source AI agent framework — a self-hosted AI assistant you control. It manages your agent's personality, memory, and model routing.)

```bash
# 1. Install OpenClaw (your AI agent)
npm install -g openclaw
openclaw configure                          # Set up your model provider
openclaw config set gateway.mode local

# 2. Enable the chat completions API for the bridge
# Open ~/.openclaw/openclaw.json in a text editor and add the "http" block
# inside the existing "gateway" section, like this:
#
#   "gateway": {
#     "mode": "local",
#     "port": 18789,
#     ... (keep existing settings) ...
#     "http": {
#       "endpoints": {
#         "chatCompletions": { "enabled": true }
#       }
#     }
#   }

# 3. Install the bridge (from the cloned repo)
cd walkieclaw-bridge
npm install
npm run build
npm link          # makes 'walkieclaw-bridge' available globally
cd ..

# 4. Install GPU whisper dependencies (optional but makes STT 60x faster)
pip install faster-whisper

# 5. Start the bridge (it auto-starts OpenClaw gateway + whisper server)
walkieclaw-bridge
```

On first run, the bridge will:
- Auto-generate an API key
- Start the OpenClaw gateway (if not already running)
- Start the GPU whisper server (if faster-whisper is installed)
- Print a banner with your **Bridge Host IP** and **API Key**

See [walkieclaw-bridge/README.md](walkieclaw-bridge/README.md) for full docs, CLI options, and troubleshooting.

### Option B: VPS Bridge (Python — for always-on remote setups)

Runs on a remote server. Works from anywhere with internet.

```bash
git clone https://github.com/slsah30/WalkieClaw.git
cd WalkieClaw

python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp vps.env.example .env
# Edit .env — fill in your OpenClaw URL and API key

python bridge.py
```

Requires firewall rules for UDP:12345 and TCP:8080 on your server.

### Flash the ESP32

```bash
pip install esphome

# Set up your WiFi credentials
cp secrets.yaml.example secrets.yaml
# Edit secrets.yaml — WiFi SSID/password, OTA password

# Flash!
esphome run walkieclaw.yaml
```

### First-Time Device Setup

No YAML editing needed for the bridge connection — it's all done on-device:

1. **Power on.** If no WiFi is configured, it creates a `WalkieClaw-Fallback` AP.
2. **Connect** to the AP from your phone. Enter your WiFi credentials in the captive portal.
3. **Visit** `http://<device_ip>/` in a browser once it joins your network.
4. **Set** two fields: **Bridge Host** (your PC/VPS IP) and **Bridge API Key** (from the bridge banner or your `.env`).
5. **Done.** Settings persist across reboots. Start talking!

### Use It!

| Action | What Happens |
|--------|-------------|
| **Hold right button** | Records your voice (LED turns red) |
| **Release button** | Sends to AI, processes response (LED turns blue) |
| **Response ready** | Plays through speaker (LED turns orange) |
| **Done** | Ready for next question (LED turns green) |
| **Press button while speaking** | Interrupts playback, starts new recording |
| **Click left button** | Cycles volume: Off → Low → Med → Normal → Loud |

## Architecture

### Local Bridge (Node.js)

```
ESP32 --UDP:12345--> [walkieclaw-bridge]
                       |
                       +--> faster-whisper GPU (auto-managed, ~0.5s)
                       |
                       +--> OpenClaw /v1/chat/completions (~2-3s)
                       |
                       +--> Edge TTS + WASM MP3 decode (~0.5s)
                       |
ESP32 <--HTTP:8080-- [walkieclaw-bridge]

Total: ~3-4 seconds end-to-end
```

- One command to run: `walkieclaw-bridge`
- Auto-manages OpenClaw gateway and whisper server as hidden child processes
- No visible windows, no ffmpeg dependency
- GPU-accelerated speech recognition via faster-whisper (CUDA)
- Talks to OpenClaw via the standard chat completions HTTP API

### VPS Bridge (Python)

```
ESP32 --UDP:12345--> [bridge.py on VPS]
                       |
                       +--> faster-whisper CPU (in-process)
                       |
                       +--> OpenClaw /v1/chat/completions
                       |
                       +--> Edge TTS (in-process)
                       |
ESP32 <--HTTP:8080-- [bridge.py on VPS]
```

- Single Python process, no Node.js needed
- Works on any Linux VPS (no GPU required)
- Multi-device support (multiple WalkieClaws can share one bridge)

### Running Both

You can run local and VPS bridges simultaneously. Each WalkieClaw device connects to whichever bridge IP you configure on the device. Two devices, two different OpenClaw agents, no conflict.

## Display & State Machine

```
+---------------------------+
|       WalkieClaw          |   <- Title (pink, Inter font)
| [====== IP bar =========] |   <- Blue banner with IP + WiFi signal
| Bridge: 1.2.3.4    75%   |   <- Connection status + battery
| Ready                     |   <- Current state (green)
| Hello from your AI!       |   <- Last response (scrolls if long)
|            [====]         |   <- VU meter (animates during audio)
+---------------------------+
```

State machine:
```
IDLE -> RECORDING -> POLLING -> READY_TO_PLAY -> PLAYING -> IDLE
            ^                                       |
            +----------- (interrupt) ---------------+
```

## Display Simulator

Want to tweak colors and layout without flashing? Open `simulator.html` in your browser — it's a pixel-perfect replica of the 128x128 display with interactive controls, color pickers, and YAML export.

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
| `walkieclaw.yaml` | ESPHome firmware — the main config |
| `walkieclaw-bridge/` | Node.js bridge (npm package, recommended for local) |
| `bridge.py` | Python bridge (for VPS deployments) |
| `udp_stream.h` | C++ UDP audio sender with auth |
| `wifi_connect.h` | WiFi network switching helper |
| `config.html` | On-device setup page |
| `simulator.html` | Browser-based display simulator |
| `udp_relay.py` | UDP relay (for indirect routing setups) |
| `secrets.yaml.example` | Template for WiFi/OTA credentials |
| `vps.env.example` | Bridge config for VPS mode |
| `setup.sh` | Step-by-step VPS setup reference |
| `requirements.txt` | Python dependencies (for VPS bridge) |
| `LICENSE` | MIT license |

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
- **`windowsHide: true` + `detached: true` on Windows** — detached CREATES a console window, don't use it

## Credits & Thanks

- **[Robert Lipe](https://www.robertlipe.com/449-2/)** — reverse engineered the AIPI Lite hardware, mapped the GPIO pinout, identified the ES8311 codec and display controller, and documented everything so the rest of us could build on it.
- **[AIPI Lite](https://github.com/xorigin-ai)** by xorigin AI — the ESP32-S3 dev board with built-in mic, speaker, LCD, and LED.
- **[ESPHome](https://esphome.io/)** — the firmware framework that makes ESP32 development actually enjoyable.
- **[LVGL](https://lvgl.io/)** — the graphics library powering the 128x128 display UI.
- **[OpenClaw](https://github.com/openclaw)** — the AI agent platform that gives WalkieClaw its brain.
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** by SYSTRAN — blazing fast speech-to-text on GPU or CPU.
- **[Edge TTS](https://github.com/rany2/edge-tts)** by rany2 — free, high-quality text-to-speech with dozens of voices.
- **[Espressif](https://www.espressif.com/)** — for the ESP32-S3 chip that makes this all possible.

## Contributing

Found a bug? Have an idea? Open an issue or PR — this project is meant to be built on.

Some ideas for contributors:
- Wake word detection (hands-free mode)
- Multi-language support
- Custom LVGL themes and animations
- Battery optimization tricks
- 3D-printable case designs
- Support for other ESP32-S3 boards

## License

MIT — do whatever you want with it. Build one, mod it, sell it, teach with it. Just have fun.
