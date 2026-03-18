# WalkieClaw Bridge

Voice bridge that connects WalkieClaw hardware (ESP32-S3) to your [OpenClaw](https://github.com/openclaw) AI agent. Press a button, speak, get a spoken response.

```
ESP32 mic --> UDP audio --> Bridge --> GPU Whisper STT --> OpenClaw Agent --> Edge TTS --> WAV --> ESP32 speaker
                                         (~0.5s)            (~2-3s)          (~0.5s)
```

**Total response time: ~3-4 seconds.**

## Prerequisites

### 1. Node.js (v18+)
```bash
node --version  # must be 18.0.0 or higher
```

### 2. Python 3.10+ with faster-whisper (for GPU speech recognition)
```bash
pip install faster-whisper
```

If you have an NVIDIA GPU, this gives you sub-second speech-to-text. Requires [CUDA Toolkit](https://developer.nvidia.com/cuda-toolkit) installed first. Without it, the bridge falls back to CPU-based whisper (slower but still works).

### 3. OpenClaw
The AI agent framework that provides the brains. OpenClaw is an open-source AI agent framework - a self-hosted AI assistant you fully control.

```bash
npm install -g openclaw
openclaw configure          # Set up your model provider
openclaw config set gateway.mode local
```

**Important:** Enable the chat completions API. Add this to `~/.openclaw/openclaw.json` inside the `"gateway"` section:

```json
"http": {
  "endpoints": {
    "chatCompletions": {
      "enabled": true
    }
  }
}
```

## Quick Start

```bash
# From the WalkieClaw repo root:
cd walkieclaw-bridge
npm install
npm run build
npm link
cd ..

# Now start the bridge (auto-starts OpenClaw gateway + whisper server)
walkieclaw-bridge
```

On first run, the bridge will:
- Auto-generate an API key
- Start the OpenClaw gateway (if not already running)
- Start the GPU whisper server (if faster-whisper is installed)
- Download the Whisper speech model on first transcription (~150MB, cached after)
- Print a banner with your Bridge Host IP and API Key

### Connect Your Device

1. Flash your ESP32-S3 with the WalkieClaw firmware via ESPHome
2. On first boot, connect to the `WalkieClaw-Fallback` WiFi AP and enter your WiFi credentials
3. Visit `http://<device-ip>/` in a browser
4. Enter the **Bridge Host** (your computer's LAN IP, shown in the bridge banner)
5. Enter the **API Key** (shown in the bridge banner)
6. Press the button on the device and talk!

## What the Bridge Auto-Manages

The bridge starts and manages two child processes automatically:

| Process | What | Port | Restart |
|---------|------|------|---------|
| **OpenClaw Gateway** | Your AI agent | :18789 | Auto-restart on crash |
| **Whisper GPU Server** | Speech-to-text | :8787 | Auto-restart on crash |

Both run hidden (no visible windows). Both stop when the bridge stops. If either is already running externally, the bridge detects it and skips launching its own.

## CLI Options

```
walkieclaw-bridge                Start the bridge
walkieclaw-bridge config         Show current configuration
walkieclaw-bridge reset          Delete config and start fresh

Options:
  --model <name>          Whisper model (default: base)
  --port <port>           HTTP port (default: 8080)
  --udp-port <port>       UDP port (default: 12345)
  --openclaw-url <url>    OpenClaw gateway URL (default: http://127.0.0.1:18789)
  --voice <voice>         Edge TTS voice (default: en-GB-RyanNeural)
  --api-key <key>         Set API key (auto-generated if not set)
  --advertise-host <ip>   IP to advertise in WAV URLs
  --max-turns <n>         Conversation history turns to keep (default: 10)
  --language <code>       Language code (en, es, fr, de, ja, zh, etc.)
  -h, --help              Show help
```

## Features

### Conversation Memory

The bridge remembers conversation context per device. Each device keeps the last 10 turns (20 messages) by default, so the AI can reference what you said earlier.

```bash
# Change the number of turns to keep
walkieclaw-bridge --max-turns 20

# History is per-device and resets when the device disconnects (10 min idle)
```

View or clear history via the API:
```bash
# View conversation history for a device
curl http://localhost:8080/api/history/172.20.9.89 -H "X-API-Key: YOUR_KEY"

# Clear history
curl -X DELETE http://localhost:8080/api/history/172.20.9.89 -H "X-API-Key: YOUR_KEY"
```

### Timers and Reminders

Set timers that speak a message on the device when they fire. Great for cooking timers, medication reminders, or meeting alerts.

```bash
# Set a 5-minute timer
curl -X POST http://localhost:8080/api/timer \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"text": "Your 5 minute timer is up!", "delay_seconds": 300}'

# Set a timer for a specific device
curl -X POST http://localhost:8080/api/timer \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"text": "Time to stretch!", "delay_seconds": 1800, "device": "172.20.9.89"}'

# List active timers
curl http://localhost:8080/api/timers -H "X-API-Key: YOUR_KEY"

# Cancel a timer
curl -X DELETE http://localhost:8080/api/timer/timer_abc123 -H "X-API-Key: YOUR_KEY"
```

Your OpenClaw agent can also set timers -- just say "remind me in 10 minutes to check the oven" and the WalkieClaw skill handles it automatically. See `walkieclaw-skill/SKILL.md` for the full skill docs.

**Note:** Timers are stored in memory and lost if the bridge restarts. Delivery has up to a 5-second delay (device health poll interval).

### Multi-language Support

The bridge supports 16 languages out of the box. Set the language and both speech recognition (Whisper) and text-to-speech (Edge TTS) are configured automatically.

```bash
# Start the bridge in Spanish
walkieclaw-bridge --language es

# Start in Japanese
walkieclaw-bridge --language ja

# Override just the voice while keeping a specific language
walkieclaw-bridge --language fr --voice fr-FR-DeniseNeural
```

**Supported languages:**

| Code | Language | Default Voice |
|------|----------|---------------|
| `en` | English | en-GB-RyanNeural |
| `es` | Spanish | es-ES-AlvaroNeural |
| `fr` | French | fr-FR-HenriNeural |
| `de` | German | de-DE-ConradNeural |
| `it` | Italian | it-IT-DiegoNeural |
| `pt` | Portuguese | pt-BR-AntonioNeural |
| `ja` | Japanese | ja-JP-KeitaNeural |
| `ko` | Korean | ko-KR-InJoonNeural |
| `zh` | Chinese | zh-CN-YunxiNeural |
| `ar` | Arabic | ar-SA-HamedNeural |
| `hi` | Hindi | hi-IN-MadhurNeural |
| `ru` | Russian | ru-RU-DmitryNeural |
| `nl` | Dutch | nl-NL-MaartenNeural |
| `pl` | Polish | pl-PL-MarekNeural |
| `sv` | Swedish | sv-SE-MattiasNeural |
| `tr` | Turkish | tr-TR-AhmetNeural |

You can use any [Edge TTS voice](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support) with `--voice`.

### Web Dashboard

A built-in web dashboard for monitoring and controlling your devices from a browser.

**Open:** `http://localhost:8080/dashboard`

The dashboard shows:
- **Bridge status** -- online/offline, connected device count, active timers
- **Connected devices** -- IP, status, conversation length, pending notifications
- **Conversation history** -- browse and clear per-device chat history
- **Send notification** -- type a message and send it to any device (or all)
- **Timer management** -- set new timers, view countdown, cancel active timers
- **Device setup** -- push bridge host and API key to devices remotely via ESPHome REST API
- **Walkie-talkie controls** -- pair/unpair devices directly from the dashboard

**Access:** Visit `http://localhost:8080/` (redirects to dashboard). The API key is auto-injected so no manual auth is needed from localhost.

### Streaming Response

AI responses are streamed sentence-by-sentence for faster perceived response time. Instead of waiting for the full response before generating speech, the bridge:

1. Streams the OpenClaw response via SSE
2. TTS generates audio for the first sentence immediately
3. Sends the first chunk to the device while generating the next
4. Device polls and plays each chunk sequentially

The `/api/response` endpoint includes a `has_more` field so the device knows to keep polling for additional chunks.

### Walkie-Talkie Mode

Pair two WalkieClaw devices for direct voice communication -- no AI, just person-to-person audio like a real walkie-talkie.

```bash
# Pair two devices
curl -X POST http://localhost:8080/api/pair \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"device_a": "172.20.9.89", "device_b": "172.20.8.222"}'

# Now when either device records, the audio goes directly to the other device

# List active pairs
curl http://localhost:8080/api/pairs -H "X-API-Key: YOUR_KEY"

# Unpair
curl -X POST http://localhost:8080/api/unpair \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"device": "172.20.9.89"}'
```

In walkie-talkie mode, the audio pipeline skips Whisper and OpenClaw entirely -- raw audio is converted to WAV and pushed to the paired device's notification queue. Both devices are automatically unpaired when either one is unpaired.

When paired, both devices show a **"Walkie-Talkie"** display with a cyan LED and the paired device IP. After recording, the sender sees "Sent!" confirmation. The receiver plays the audio and the display resets automatically. You can also pair/unpair from the web dashboard.

**Note:** Delivery has up to a 5-second delay due to the health poll interval.

### OTA Firmware Updates

Push firmware updates to your ESP32 devices remotely without plugging in a USB cable. Requires ESPHome installed on the bridge machine.

```bash
# Set the ESPHome config directory (where walkieclaw.yaml lives)
export ESPHOME_CONFIG_DIR=/path/to/aipi-openclaw

# Trigger OTA update to a device
curl -X POST http://localhost:8080/api/ota \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"device": "172.20.9.89"}'

# Check OTA status
curl http://localhost:8080/api/ota/status -H "X-API-Key: YOUR_KEY"
```

Only one OTA update can run at a time. Progress is logged to the bridge console.

### Push Notifications

Send spoken messages to the device at any time -- alerts, reminders, or just fun messages.

```bash
# Send to all devices
curl -X POST http://localhost:8080/api/notify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"text": "Dinner is ready!"}'

# Send to a specific device
curl -X POST http://localhost:8080/api/notify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"text": "Your package arrived.", "device": "172.20.9.89"}'
```

## Firmware Features

These features are built into the ESP32 firmware (`walkieclaw.yaml`) and require flashing.

### Wake Word Detection

Say a wake word to start recording hands-free -- no button press needed. Four wake words are supported simultaneously:

- **"Hey Jarvis"**
- **"Okay Nabu"**
- **"Hey Mycroft"**
- **"Alexa"**

**How to enable:**
1. Visit `http://<device-ip>/` in your browser
2. Toggle **"Wake Word Enabled"** to ON
3. The device now listens for any of the four wake words continuously

When a wake word is detected, the device starts recording and auto-stops after 10 seconds. Wake word detection is properly re-armed after each conversation completes. The push-to-talk button still works as a manual override.

**Power note:** Wake word detection keeps the microphone running continuously, which uses more battery. It defaults to OFF. Enable it when the device is plugged in for best results.

### Battery Calibration

The firmware uses a LiPo discharge lookup table for accurate battery percentage:

| Voltage | Percentage |
|---------|-----------|
| 4.20V | 100% |
| 4.10V | 90% |
| 3.95V | 70% |
| 3.80V | 50% |
| 3.70V | 30% |
| 3.50V | 10% |
| 3.30V | 0% |

If your battery percentage seems off, calibrate the voltage multiplier:

1. Check the ESPHome logs: `esphome logs walkieclaw.yaml`
2. Look for `ADC voltage after multiply: X.XXXV`
3. Compare with a multimeter reading on the battery
4. Adjust `battery_voltage_multiplier` in `walkieclaw.yaml` substitutions (default: 2.5)

Example: If the log shows 3.90V but your multimeter reads 4.10V, change the multiplier to `2.5 * (4.10 / 3.90) = 2.63`.

### Volume Persistence

Volume settings persist across reboots. Change the volume with the left button (cycles Off -> Low -> Med -> Normal -> Loud) and it will be the same after a power cycle.

## API Reference

All endpoints except `/audio/*` and `/dashboard` require an `X-API-Key` header.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Device health check, delivers pending commands |
| `/api/response` | GET | Poll for AI response (idle/processing/ready + has_more) |
| `/api/devices` | GET | List connected devices with conversation stats |
| `/api/history/:ip` | GET | Get conversation history for a device |
| `/api/history/:ip` | DELETE | Clear conversation history for a device |
| `/api/notify` | POST | Queue a TTS notification |
| `/api/timer` | POST | Schedule a timer/reminder |
| `/api/timers` | GET | List active timers |
| `/api/timer/:id` | DELETE | Cancel a timer |
| `/api/pair` | POST | Pair two devices for walkie-talkie mode |
| `/api/unpair` | POST | Unpair a device |
| `/api/pairs` | GET | List active device pairs |
| `/api/connect_wifi` | POST | Queue a WiFi switch command |
| `/api/ota` | POST | Trigger ESPHome OTA update |
| `/api/ota/status` | GET | Check OTA progress |
| `/dashboard` | GET | Web dashboard (auth via `?key=` or login prompt) |
| `/audio/*` | GET | Serve generated WAV files (no auth) |

## Security

- **API Key authentication** on HTTP and UDP
- **Rate limiting** (30 req/min per IP)
- Audio file URLs use unguessable names
- UDP requires keyed START marker before accepting audio

## Troubleshooting

**Bridge shows wrong IP address:**
Use `--advertise-host <ip>` to override. The bridge filters out VPN, VirtualBox, Docker, and WSL adapters.

**"Whisper server not reachable":**
Make sure Python and faster-whisper are installed: `pip install faster-whisper`. The bridge will auto-start the whisper server if it finds `whisper-server.py`.

**ESP32 gets 401 errors:**
Make sure the API key on the device matches the bridge. Visit `http://<device-ip>/` to update it.

**Port 8080 already in use:**
The bridge auto-increments to find a free port, but the ESP32 firmware expects 8080. Kill whatever is using 8080, or use `--port <other>` and reflash.

**OpenClaw gateway not starting:**
Make sure OpenClaw is installed (`npm install -g openclaw`) and configured (`openclaw configure`). Check that `chatCompletions` is enabled in `~/.openclaw/openclaw.json`.

**Slow transcription (30+ seconds):**
You're probably running CPU-only whisper. Install `faster-whisper` with CUDA support for sub-second GPU transcription. Requires an NVIDIA GPU with CUDA toolkit.

**ESPHome build fails with out-of-memory:**
The wake word feature (micro_wake_word + TFLite) is memory-hungry to compile. Build with single-threaded compilation:
```bash
PLATFORMIO_SETTING_JOBS=1 esphome compile walkieclaw.yaml
```

## License

MIT
