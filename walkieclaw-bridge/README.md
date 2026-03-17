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
  -h, --help              Show help
```

## How It Works

1. **Button press** on ESP32 starts recording from the onboard microphone
2. **UDP audio** streams raw I2S audio to the bridge on port 12345
3. **faster-whisper** on GPU transcribes the audio to text (~0.5 seconds)
4. **OpenClaw** processes the text through your AI agent via `/v1/chat/completions` (~2-3 seconds)
5. **Edge TTS** converts the response to speech, decoded in-process via WASM (~0.5 seconds)
6. **HTTP polling** - the ESP32 picks up the audio URL and plays it through the speaker

No subprocesses are spawned per request. No ffmpeg needed. No visible windows.

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

## License

MIT
