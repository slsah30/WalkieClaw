# Contributing to WalkieClaw

Thanks for your interest! This project is in alpha and contributions are welcome.

## Getting Started

### Prerequisites

- **Hardware:** [AIPI Lite](https://www.amazon.com/AIPI-Lite-Customizable-Character-Real-Time-Interactive/dp/B0FQNNVV36) ESP32-S3 board (~$15)
- **Node.js** 18+ (for the local bridge)
- **Python** 3.10+ (for faster-whisper and the VPS bridge)
- **ESPHome** (`pip install esphome`) for flashing firmware
- **OpenClaw** (`npm install -g openclaw`) as the AI agent backend

### Dev Setup (Local Bridge)

```bash
git clone https://github.com/slsah30/WalkieClaw.git
cd WalkieClaw/walkieclaw-bridge

npm install
npm run dev          # watches src/ and recompiles on change
```

In a separate terminal:

```bash
npm start            # runs the bridge (auto-starts gateway + whisper)
```

### Dev Setup (VPS Bridge)

```bash
git clone https://github.com/slsah30/WalkieClaw.git
cd WalkieClaw

python3 -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your settings

python bridge.py
```

### Flashing Firmware

```bash
cp secrets.yaml.example secrets.yaml
# Edit secrets.yaml with your WiFi credentials

esphome run walkieclaw.yaml
```

## Project Structure

| Path | What It Is |
|------|-----------|
| `walkieclaw.yaml` | ESPHome firmware (the ESP32 side) |
| `walkieclaw-bridge/src/` | Node.js bridge (TypeScript, local deployment) |
| `bridge.py` | Python bridge (VPS deployment) |
| `udp_stream.h`, `wifi_connect.h` | C++ helpers compiled into firmware |
| `walkieclaw-skill/` | OpenClaw skill for device control |

## How to Contribute

1. **Fork** the repo and create a branch from `master`.
2. **Make your changes.** Keep commits focused and descriptive.
3. **Test on real hardware** if your change affects the firmware or audio pipeline.
4. **Open a PR** with a clear description of what you changed and why.

## Guidelines

- Keep it simple. WalkieClaw is a small project -- don't over-engineer.
- Test with the actual ESP32 hardware when possible.
- The Node.js bridge must work on Windows (many users run it on their PC).
- Don't add paid API dependencies to the bridge -- WalkieClaw should be free to operate.
- Don't commit secrets, API keys, or personal WiFi credentials.

## Architecture Notes

- The ESP32 sends raw I2S audio over UDP and polls for responses over HTTP. All connections are ESP32-initiated (outbound only).
- The bridge auto-manages OpenClaw gateway and whisper server as child processes.
- TTS uses Edge TTS with WASM MP3 decoding (no ffmpeg dependency in the Node bridge).
- See the main [README.md](README.md) for architecture diagrams.

## Questions?

Open an issue. We're happy to help.
