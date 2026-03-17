# WalkieClaw Bridge

Voice bridge that connects WalkieClaw hardware (ESP32-S3) to an [OpenClaw](https://github.com/AiPi-Inc/openclaw) AI agent. Press a button, speak, get a response through the speaker.

```
ESP32 mic --> UDP audio --> Bridge --> Whisper STT --> OpenClaw Agent --> Edge TTS --> WAV --> ESP32 speaker
```

## Prerequisites

Install these before running the bridge:

### 1. Node.js (v18+)
```bash
node --version  # must be 18.0.0 or higher
```

### 2. ffmpeg
Required to convert TTS audio (MP3) to WAV for the ESP32 speaker.

```bash
# Windows
winget install ffmpeg

# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

After installing, **restart your terminal** so `ffmpeg` is on your PATH.

### 3. OpenClaw
The AI agent framework that provides the brains.

```bash
npm install -g openclaw
openclaw configure          # Set up your API provider key (e.g. OpenRouter)
openclaw config set gateway.mode local
```

## Quick Start

```bash
# Install globally
npm install -g walkieclaw-bridge

# Terminal 1: Start OpenClaw gateway
openclaw gateway --port 18789

# Terminal 2: Start the bridge
walkieclaw-bridge
```

On first run, the bridge will:
- Auto-generate an API key
- Download the Whisper speech recognition model (~150MB)
- Print a banner with your Bridge Host IP and API Key

### Connect Your Device

1. Flash your ESP32-S3 with the WalkieClaw firmware via ESPHome
2. On first boot, connect to the `WalkieClaw-Fallback` WiFi AP and enter your WiFi credentials
3. Visit `http://<device-ip>/` in a browser
4. Enter the **Bridge Host** (your computer's LAN IP, shown in the bridge banner)
5. Enter the **API Key** (shown in the bridge banner)
6. Press the button on the device and talk!

## CLI Options

```
walkieclaw-bridge                Start the bridge
walkieclaw-bridge config         Show current configuration
walkieclaw-bridge reset          Delete config and start fresh

Options:
  --model <name>          Whisper model (default: base)
  --port <port>           HTTP port (default: 8080)
  --udp-port <port>       UDP port (default: 12345)
  --openclaw-url <url>    OpenClaw URL (default: http://127.0.0.1:18789)
  --voice <voice>         Edge TTS voice (default: en-GB-RyanNeural)
  --api-key <key>         Set API key (auto-generated if not set)
  --advertise-host <ip>   IP to advertise in WAV URLs
  -h, --help              Show help
```

## How It Works

1. **Button press** on ESP32 starts recording from the onboard microphone
2. **UDP audio** streams raw I2S audio to the bridge on port 12345
3. **Whisper** transcribes the audio to text (runs locally via smart-whisper)
4. **OpenClaw** processes the text through your configured AI agent
5. **Edge TTS** converts the agent's response to speech (MP3 -> WAV via ffmpeg)
6. **HTTP polling** - the ESP32 picks up the audio URL and plays it through the speaker

## Configuration

Config is stored at `~/.walkieclaw/config.json`. Use `walkieclaw-bridge reset` to start fresh.

The bridge auto-detects your LAN IP and finds free ports. If the default ports (8080/12345) are in use, it will try the next available port.

### Setting Up Your OpenClaw Agent

The bridge talks to your `main` OpenClaw agent by default. For the best experience, set up your agent's workspace:

```bash
# Your agent workspace lives at:
# ~/.openclaw/agents/main/

# Key files to create:
# IDENTITY.md  - Who the agent is
# SOUL.md      - Personality and behavior guidelines
```

Without these files, the agent will give generic responses. With them, you get a personalized AI voice assistant.

## API Endpoints

All endpoints except `/audio/*` require an `X-API-Key` header.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Device health check, delivers pending commands |
| `/api/response` | GET | Poll for AI response (idle/processing/ready) |
| `/api/devices` | GET | List connected devices |
| `/api/notify` | POST | Queue a TTS notification |
| `/api/connect_wifi` | POST | Queue a WiFi switch command |
| `/audio/*` | GET | Serve generated WAV files (no auth) |

## Security

- **API Key authentication** on HTTP and UDP
- **Rate limiting** (30 req/min per IP)
- Audio file URLs use unguessable names
- UDP requires keyed START marker before accepting audio

## Troubleshooting

**Bridge shows wrong IP address:**
The bridge filters out VPN, VirtualBox, Docker, and WSL adapters. Use `--advertise-host <ip>` to override.

**"ffmpeg conversion failed":**
Install ffmpeg and restart your terminal. The bridge falls back to silence if ffmpeg is missing.

**ESP32 gets 401 errors:**
Make sure the API key on the device matches the bridge. Visit `http://<device-ip>/` to update it.

**Port 8080 already in use:**
The bridge auto-increments to find a free port, but the ESP32 firmware expects port 8080. Kill whatever is using 8080, or reflash with a different port.

**OpenClaw not responding:**
Make sure the gateway is running: `openclaw gateway --port 18789`

## License

MIT
