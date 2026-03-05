#!/usr/bin/env python3
"""
AIPI Lite → OpenClaw VPS Voice Bridge
======================================

This bridge script connects your AIPI Lite ESP32-S3 hardware to your
OpenClaw (Blam) agent running on your VPS.

Architecture:
  [AIPI Lite] --UDP audio--> [This Bridge] --HTTP--> [OpenClaw VPS]
  [AIPI Lite] <--HTTP WAV--- [This Bridge] <--text-- [OpenClaw VPS]

The bridge handles:
  1. Receiving raw I2S audio via UDP from the ESP32
  2. Transcribing speech to text (faster-whisper, local)
  3. Sending the text to your OpenClaw agent's HTTP API
  4. Converting the response to speech (gTTS or ElevenLabs)
  5. Serving the WAV file over HTTP for the ESP32 to stream
  6. Commanding the ESP32 via ESPHome native API to play it

Dependencies:
  pip install aioesphomeapi faster-whisper gtts pydub requests aiohttp

Usage:
  python3 bridge.py

Configuration:
  Edit the CONFIG section below, or use environment variables.
"""

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import struct
import tempfile
import time
import wave
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web

# =============================================================================
# CONFIG — Edit these for your setup
# =============================================================================

CONFIG = {
    # --- Network ---
    "UDP_LISTEN_HOST": os.getenv("UDP_HOST", "0.0.0.0"),
    "UDP_LISTEN_PORT": int(os.getenv("UDP_PORT", "12345")),
    "HTTP_HOST": os.getenv("HTTP_HOST", "0.0.0.0"),
    "HTTP_PORT": int(os.getenv("HTTP_PORT", "8080")),

    # --- ESPHome Device ---
    # IP of your AIPI Lite on your local network
    "ESPHOME_HOST": os.getenv("ESPHOME_HOST", "192.168.1.50"),
    "ESPHOME_PORT": int(os.getenv("ESPHOME_PORT", "6053")),
    "ESPHOME_PASSWORD": os.getenv("ESPHOME_PASSWORD", ""),
    "ESPHOME_NOISE_PSK": os.getenv("ESPHOME_NOISE_PSK", ""),

    # --- OpenClaw VPS ---
    # Base URL of your OpenClaw gateway (just the origin, no path)
    "OPENCLAW_URL": os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789"),
    "OPENCLAW_TOKEN": os.getenv("OPENCLAW_TOKEN", ""),
    # "chat" = /v1/chat/completions (recommended for voice, synchronous)
    # "hooks" = /hooks/agent (async webhook style)
    "OPENCLAW_MODE": os.getenv("OPENCLAW_MODE", "chat"),
    # Which agent to route to (default "main")
    "OPENCLAW_AGENT_ID": os.getenv("OPENCLAW_AGENT_ID", "main"),

    # --- STT (Speech-to-Text) ---
    # Model: "tiny", "base", "small", "medium", "large-v3"
    # Smaller = faster, larger = more accurate
    "WHISPER_MODEL": os.getenv("WHISPER_MODEL", "base"),
    "WHISPER_LANGUAGE": os.getenv("WHISPER_LANGUAGE", "en"),
    "WHISPER_DEVICE": os.getenv("WHISPER_DEVICE", "cpu"),  # or "cuda"

    # --- TTS (Text-to-Speech) ---
    # "gtts" for Google TTS (free), "elevenlabs" for ElevenLabs
    "TTS_ENGINE": os.getenv("TTS_ENGINE", "gtts"),
    "ELEVENLABS_API_KEY": os.getenv("ELEVENLABS_API_KEY", ""),
    "ELEVENLABS_VOICE_ID": os.getenv("ELEVENLABS_VOICE_ID", ""),

    # --- Audio ---
    "SAMPLE_RATE": 16000,       # Input sample rate from ESP32
    "OUTPUT_SAMPLE_RATE": 16000, # Output to ESP32 speaker
    "CHANNELS": 1,
    "SAMPLE_WIDTH": 2,          # 16-bit = 2 bytes
    "VOLUME_REDUCTION_DB": 0,  # Reduce TTS volume to protect tiny speaker

    # --- Tailscale / Network ---
    # When running on VPS behind Tailscale, set this to your VPS Tailscale IP
    # so the ESP32 fetches WAV files from the right address.
    # Leave empty for auto-detection (works for LAN-only setups).
    "HTTP_ADVERTISE_HOST": os.getenv("HTTP_ADVERTISE_HOST", ""),

    # --- Paths ---
    "AUDIO_DIR": os.getenv("AUDIO_DIR", "/tmp/aipi-bridge-audio"),
    "BEEP_FILE": os.getenv("BEEP_FILE", "beep.wav"),
}

# =============================================================================
# Logging
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bridge")

# =============================================================================
# Global State
# =============================================================================
class BridgeState:
    """Tracks the current state of the voice bridge."""
    def __init__(self):
        self.is_listening = False
        self.is_processing = False
        self.is_speaking = False
        self.audio_buffer = bytearray()
        self.current_wav_path: Optional[str] = None
        self.last_activity = time.time()
        self.esphome_client = None
        self.esphome_services = {}
        self.whisper_model = None

state = BridgeState()

# =============================================================================
# STT — Speech to Text via faster-whisper
# =============================================================================
def init_whisper():
    """Load the Whisper model (done once at startup)."""
    from faster_whisper import WhisperModel
    log.info(f"Loading Whisper model: {CONFIG['WHISPER_MODEL']} "
             f"on {CONFIG['WHISPER_DEVICE']}...")
    state.whisper_model = WhisperModel(
        CONFIG["WHISPER_MODEL"],
        device=CONFIG["WHISPER_DEVICE"],
        compute_type="int8" if CONFIG["WHISPER_DEVICE"] == "cpu" else "float16",
    )
    log.info("Whisper model loaded.")


def transcribe_audio(audio_bytes: bytes) -> str:
    """Transcribe raw PCM audio bytes to text."""
    if not state.whisper_model:
        log.error("Whisper model not initialized!")
        return ""

    # Wrap raw PCM in a WAV container for Whisper
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(CONFIG["CHANNELS"])
        wf.setsampwidth(CONFIG["SAMPLE_WIDTH"])
        wf.setframerate(CONFIG["SAMPLE_RATE"])
        wf.writeframes(audio_bytes)
    wav_buf.seek(0)

    # Save to temp file (faster-whisper needs a file path)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_buf.read())
        tmp_path = tmp.name

    try:
        segments, info = state.whisper_model.transcribe(
            tmp_path,
            language=CONFIG["WHISPER_LANGUAGE"],
            beam_size=5,
            vad_filter=False,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        log.info(f"STT result ({info.duration:.1f}s audio): {text}")
        return text
    except Exception as e:
        log.error(f"Transcription error: {e}")
        return ""
    finally:
        import shutil; shutil.copy(tmp_path, "/tmp/aipi_debug_last.wav"); os.unlink(tmp_path)


# =============================================================================
# OpenClaw Agent Communication
# =============================================================================
async def send_to_openclaw(text: str) -> str:
    """
    Send transcribed text to your OpenClaw agent and get the response.

    Supports two OpenClaw endpoint modes (set via OPENCLAW_MODE env var):

    "hooks"  — POST /hooks/agent  (webhook style, async by default)
               Gateway must have hooks.enabled=true in config.
               Response comes back as the agent run result.

    "chat"   — POST /v1/chat/completions  (OpenAI-compatible endpoint)
               Gateway must have gateway.http.endpoints.chatCompletions.enabled=true.
               Uses a stable session key so Blam remembers conversation context.
               This is the RECOMMENDED mode for voice — it's synchronous and
               returns the full response text directly.
    """
    if not text.strip():
        return "I didn't catch that. Could you say it again?"

    mode = CONFIG.get("OPENCLAW_MODE", "chat")
    log.info(f"Sending to OpenClaw ({mode}): {text}")

    try:
        if mode == "chat":
            return await _openclaw_chat_completions(text)
        else:
            return await _openclaw_hooks_agent(text)
    except asyncio.TimeoutError:
        log.error("OpenClaw request timed out")
        return "Sorry, the request timed out."
    except Exception as e:
        log.error(f"OpenClaw error: {e}")
        return "Sorry, I encountered an error."


async def _openclaw_chat_completions(text: str) -> str:
    """
    Use OpenClaw's OpenAI-compatible /v1/chat/completions endpoint.
    This gives synchronous responses and maintains conversation context
    via the 'user' field as a stable session key.
    """
    base_url = CONFIG["OPENCLAW_URL"].rstrip("/")
    url = f"{base_url}/v1/chat/completions"

    headers = {"Content-Type": "application/json"}
    token = CONFIG.get("OPENCLAW_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Agent ID: use OPENCLAW_AGENT_ID or default to "main"
    agent_id = CONFIG.get("OPENCLAW_AGENT_ID", "main")
    model = f"openclaw:{agent_id}"

    payload = {
        "model": model,
        # 'user' field = stable session key for multi-turn conversation
        "user": "aipi-lite-voice",
        "stream": False,
        "messages": [
            {"role": "user", "content": text}
        ],
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                # Standard OpenAI chat completions response format
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                else:
                    content = str(data)
                # Strip <think> tags (DeepSeek/R1 models)
                content = re.sub(
                    r"<think>.*?</think>", "", content, flags=re.DOTALL
                ).strip()
                log.info(f"OpenClaw response: {content[:100]}...")
                return content or "I processed that but had nothing to say."
            else:
                body = await resp.text()
                log.error(f"OpenClaw chat HTTP {resp.status}: {body[:200]}")
                return "Sorry, I had trouble connecting to my brain."


async def _openclaw_hooks_agent(text: str) -> str:
    """
    Use OpenClaw's /hooks/agent webhook endpoint.
    Returns 202 (async run started). The response text may come back
    via the configured channel (WhatsApp, Telegram, etc.) rather than
    in the HTTP response body. Best for fire-and-forget triggers.

    For voice, prefer "chat" mode instead — it returns text synchronously.
    """
    base_url = CONFIG["OPENCLAW_URL"].rstrip("/")
    url = f"{base_url}/hooks/agent"

    headers = {"Content-Type": "application/json"}
    token = CONFIG.get("OPENCLAW_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "message": text,
        "name": "AIPI Voice",
        "sessionKey": "hook:aipi-voice",
        "wakeMode": "now",
        "timeoutSeconds": 30,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status in (200, 202):
                data = await resp.json()
                response_text = (
                    data.get("response")
                    or data.get("text")
                    or data.get("message")
                    or data.get("content")
                    or data.get("result", {}).get("text", "")
                    or "Got it. Processing."
                )
                response_text = re.sub(
                    r"<think>.*?</think>", "", response_text, flags=re.DOTALL
                ).strip()
                log.info(f"OpenClaw response: {response_text[:100]}...")
                return response_text
            else:
                body = await resp.text()
                log.error(f"OpenClaw hooks HTTP {resp.status}: {body[:200]}")
                return "Sorry, I had trouble connecting to my brain."


# =============================================================================
# TTS — Text to Speech
# =============================================================================
async def synthesize_speech(text: str) -> str:
    """Convert text to a WAV file and return the file path."""
    os.makedirs(CONFIG["AUDIO_DIR"], exist_ok=True)

    # Generate unique filename based on content hash + timestamp
    text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
    timestamp = int(time.time() * 1000)
    wav_path = os.path.join(CONFIG["AUDIO_DIR"], f"tts_{text_hash}_{timestamp}.wav")

    engine = CONFIG["TTS_ENGINE"]

    if engine == "elevenlabs" and CONFIG["ELEVENLABS_API_KEY"]:
        await _tts_elevenlabs(text, wav_path)
    elif engine == "edge":
        await _tts_edge(text, wav_path)
    else:
        await _tts_gtts(text, wav_path)

    # Post-process: resample to output rate and reduce volume
    wav_path = _postprocess_audio(wav_path)

    state.current_wav_path = wav_path
    log.info(f"TTS generated: {wav_path}")
    return wav_path


async def _tts_edge(text: str, output_path: str):
    """Generate TTS using Microsoft Edge TTS (free, natural voices)."""
    import edge_tts
    from pydub import AudioSegment

    mp3_path = output_path.replace(".wav", ".mp3")
    voice = CONFIG.get("EDGE_TTS_VOICE", "en-US-GuyNeural")
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(mp3_path)

    # Convert MP3 to WAV
    loop = asyncio.get_event_loop()
    def _convert():
        audio = AudioSegment.from_mp3(mp3_path)
        audio = audio.set_frame_rate(CONFIG["OUTPUT_SAMPLE_RATE"])
        audio = audio.set_channels(1)
        audio = audio.set_sample_width(2)
        audio.export(output_path, format="wav")
        os.unlink(mp3_path)
    await loop.run_in_executor(None, _convert)


async def _tts_gtts(text: str, output_path: str):
    """Generate TTS using Google Text-to-Speech (free)."""
    from gtts import gTTS

    loop = asyncio.get_event_loop()
    def _generate():
        tts = gTTS(text=text, lang="en", slow=False)
        # gTTS outputs MP3, we need to convert to WAV
        mp3_path = output_path.replace(".wav", ".mp3")
        tts.save(mp3_path)
        # Convert MP3 to WAV using pydub
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(mp3_path)
        audio = audio.set_frame_rate(CONFIG["OUTPUT_SAMPLE_RATE"])
        audio = audio.set_channels(1)
        audio = audio.set_sample_width(2)
        audio.export(output_path, format="wav")
        os.unlink(mp3_path)

    await loop.run_in_executor(None, _generate)


async def _tts_elevenlabs(text: str, output_path: str):
    """Generate TTS using ElevenLabs API."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{CONFIG['ELEVENLABS_VOICE_ID']}"
    headers = {
        "xi-api-key": CONFIG["ELEVENLABS_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "audio/wav",
    }
    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                with open(output_path, "wb") as f:
                    f.write(await resp.read())
            else:
                log.error(f"ElevenLabs error: {resp.status}")
                # Fall back to gTTS
                await _tts_gtts(text, output_path)


def _postprocess_audio(wav_path: str) -> str:
    """Resample, adjust volume, and add silence tail for hardware flush."""
    from pydub import AudioSegment

    audio = AudioSegment.from_wav(wav_path)

    # Ensure correct output format
    audio = audio.set_frame_rate(CONFIG["OUTPUT_SAMPLE_RATE"])
    audio = audio.set_channels(1)
    audio = audio.set_sample_width(2)

    # Reduce volume to protect the tiny speaker
    audio = audio + CONFIG["VOLUME_REDUCTION_DB"]

    # Create a short beep prefix (wake indicator)
    beep = AudioSegment.silent(duration=50, frame_rate=CONFIG["OUTPUT_SAMPLE_RATE"])
    # Generate a simple 800Hz tone beep
    import math
    beep_samples = []
    for i in range(int(CONFIG["OUTPUT_SAMPLE_RATE"] * 0.08)):
        sample = int(4000 * math.sin(2 * math.pi * 800 * i / CONFIG["OUTPUT_SAMPLE_RATE"]))
        beep_samples.append(struct.pack("<h", sample))
    beep_raw = b"".join(beep_samples)
    beep = AudioSegment(
        data=beep_raw,
        sample_width=2,
        frame_rate=CONFIG["OUTPUT_SAMPLE_RATE"],
        channels=1,
    ) - 12  # Quiet beep

    # Add 200ms silence tail — flushes ESP32 I2S DMA buffers cleanly
    silence_tail = AudioSegment.silent(
        duration=200,
        frame_rate=CONFIG["OUTPUT_SAMPLE_RATE"],
    )

    # Stitch: beep + short pause + speech + silence tail
    final = beep + AudioSegment.silent(duration=100) + audio + silence_tail

    # Overwrite the file
    final.export(wav_path, format="wav")
    return wav_path


# =============================================================================
# UDP Audio Receiver
# =============================================================================
class UDPAudioProtocol(asyncio.DatagramProtocol):
    """Receives raw I2S PCM audio from the AIPI Lite over UDP."""

    def __init__(self):
        self.transport = None
        self._silence_timer = None

    def connection_made(self, transport):
        self.transport = transport
        log.info(f"UDP listener ready on "
                 f"{CONFIG['UDP_LISTEN_HOST']}:{CONFIG['UDP_LISTEN_PORT']}")

    def datagram_received(self, data: bytes, addr):
        if state.is_processing or state.is_speaking:
            return  # Don't buffer while processing/speaking

        # Handle markers from ESP32 and relay
        if data.startswith(b'ESP_IP:'):
            esp_ip = data.decode().split(':', 1)[1]
            if esp_ip != CONFIG.get('ESPHOME_HOST') or not state.esphome_client:
                log.info(f'ESP32 detected at {esp_ip}')
                CONFIG['ESPHOME_HOST'] = esp_ip
                if not state.esphome_client:
                    asyncio.ensure_future(init_esphome())
            return
        if data in (b'START', b'STOP'):
            log.debug(f'UDP marker: {data.decode()}')
            return

        state.is_listening = True
        state.audio_buffer.extend(data)
        state.last_activity = time.time()

        # Cancel existing silence timer
        if self._silence_timer:
            self._silence_timer.cancel()

        # After 1.5s of silence, process the buffered audio
        loop = asyncio.get_event_loop()
        self._silence_timer = loop.call_later(
            1.5, lambda: asyncio.ensure_future(self._process_buffer())
        )

    async def _process_buffer(self):
        """Process accumulated audio buffer through the full pipeline."""
        if not state.audio_buffer or state.is_processing:
            return

        state.is_listening = False
        state.is_processing = True
        audio_data = bytes(state.audio_buffer)
        state.audio_buffer.clear()

        # ESP32 I2S sends 32-bit samples; extract high 16-bit of each
        # (every other int16 sample in little-endian is the actual audio)
        if len(audio_data) >= 4:
            import struct as _struct
            n_samples_32 = len(audio_data) // 4
            samples_32 = _struct.unpack(f"<{n_samples_32}i", audio_data[:n_samples_32*4])
            samples_16 = [((s >> 16) & 0xFFFF) for s in samples_32]
            # Pack back as signed int16
            audio_data = _struct.pack(f"<{len(samples_16)}h",
                *[s if s < 32768 else s - 65536 for s in samples_16])

        log.info(f"Processing {len(audio_data)} bytes of audio...")

        try:
            # 1. Transcribe
            await command_update_display("WalkieClaw", "Transcribing...")
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, transcribe_audio, audio_data)

            if not text.strip():
                log.info("No speech detected, skipping.")
                await command_update_display("WalkieClaw", "Ready", "")
                state.is_processing = False
                return

            await command_update_display("WalkieClaw", "Thinking...", sanitize_for_display(f'"{text[:60]}"'))

            # 2. Send to OpenClaw
            response = await send_to_openclaw(text)

            # 3. Generate TTS
            wav_path = await synthesize_speech(response)

            # 4. Update display with response text
            await command_update_display("WalkieClaw", "Speaking...", sanitize_for_display(response[:200]))

            # 5. Tell the ESP32 to play it
            await command_play_tts(wav_path)

        except Exception as e:
            log.error(f"Pipeline error: {e}", exc_info=True)
        finally:
            state.is_processing = False
            state.is_speaking = False
            await command_update_display("WalkieClaw", "Ready")


# =============================================================================
# HTTP Server — Serves WAV files to the ESP32
# =============================================================================
async def handle_audio_request(request: web.Request) -> web.Response:
    """Serve WAV files for the ESP32 media_player to stream."""
    filename = request.match_info.get("filename", "")
    filepath = os.path.join(CONFIG["AUDIO_DIR"], filename)

    if not os.path.exists(filepath):
        return web.Response(status=404, text="Not found")

    with open(filepath, "rb") as f:
        data = f.read()

    return web.Response(
        body=data,
        content_type="audio/wav",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({
        "status": "ok",
        "listening": state.is_listening,
        "processing": state.is_processing,
        "speaking": state.is_speaking,
        "uptime": time.time() - state.last_activity,
    })


def create_http_app() -> web.Application:
    """Create the aiohttp web application."""
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/audio/{filename}", handle_audio_request)
    return app


# =============================================================================
# ESPHome Native API — Command the device
# =============================================================================
async def init_esphome():
    """Connect to the AIPI Lite via ESPHome native API."""
    try:
        from aioesphomeapi import APIClient

        client = APIClient(
            CONFIG["ESPHOME_HOST"],
            CONFIG["ESPHOME_PORT"],
            CONFIG["ESPHOME_PASSWORD"] or None,
            noise_psk=CONFIG["ESPHOME_NOISE_PSK"] or None,
        )
        await client.connect(login=True)
        device_info = await client.device_info()
        log.info(f"Connected to ESPHome device: {device_info.name}")
        state.esphome_client = client
        _, services = await client.list_entities_services()
        state.esphome_services = {s.name: s for s in services}
        log.info(f"ESPHome services: {list(state.esphome_services.keys())}")
        return client
    except Exception as e:
        log.warning(f"ESPHome connection failed: {e}. "
                    "Will retry on next command.")
        return None


def sanitize_for_display(text: str) -> str:
    """Replace non-ASCII chars that LVGL fonts can't render."""
    replacements = {
        '\u2014': '-', '\u2013': '-',  # em/en dash
        '\u2018': "'", '\u2019': "'",  # curly single quotes
        '\u201c': '"', '\u201d': '"',  # curly double quotes
        '\u2026': '...',              # ellipsis
        '\u2022': '*',                # bullet
        '\u00a0': ' ',                # non-breaking space
    }
    for old_char, new_char in replacements.items():
        text = text.replace(old_char, new_char)
    # Strip any remaining non-ASCII
    text = text.encode('ascii', 'replace').decode('ascii')
    return text


async def command_update_display(status: str, action: str, response: str = ""):
    """Update the LVGL display labels on the ESP32."""
    try:
        if state.esphome_client:
            svc = state.esphome_services.get("update_display")
            if svc:
                log.info(f"Display update: action={action!r}, response={response[:40]!r}")
                await state.esphome_client.execute_service(svc, {"status_text": status, "action_text": action, "response_text": response})
            else:
                log.warning("update_display service not found in esphome_services")
        else:
            log.warning("update_display skipped: no esphome_client")
    except Exception as e:
        log.warning(f"update_display failed: {e}")
        state.esphome_client = None


async def command_play_tts(wav_path: str):
    """Tell the ESP32 to play a WAV file from our HTTP server."""
    filename = os.path.basename(wav_path)
    # Use cache-busting timestamp parameter
    url = (f"http://{CONFIG['HTTP_HOST']}:{CONFIG['HTTP_PORT']}"
           f"/audio/{filename}?t={int(time.time() * 1000)}")

    # If bridge runs on same machine, use the machine's LAN IP
    # The ESP32 needs to reach this URL on the network
    advertise_host = CONFIG.get("HTTP_ADVERTISE_HOST", "")
    if advertise_host:
        # Explicit override (e.g., Tailscale IP for VPS deployment)
        lan_ip = advertise_host
    elif CONFIG["HTTP_HOST"] == "0.0.0.0":
        # Auto-detect LAN IP
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
        finally:
            s.close()
    else:
        lan_ip = CONFIG["HTTP_HOST"]

    url = (f"http://{lan_ip}:{CONFIG['HTTP_PORT']}"
           f"/audio/{filename}?t={int(time.time() * 1000)}")

    log.info(f"Commanding ESP32 to play: {url}")
    state.is_speaking = True

    try:
        if state.esphome_client:
            # Call the play_tts service defined in the YAML
            svc = state.esphome_services.get("play_tts")
            if svc:
                await state.esphome_client.execute_service(svc, {"url": url})
        else:
            # Fallback: try to reconnect
            await init_esphome()
            if state.esphome_client:
                svc = state.esphome_services.get("play_tts")
            if svc:
                await state.esphome_client.execute_service(svc, {"url": url})
    except Exception as e:
        log.error(f"ESPHome command failed: {e}")
        state.esphome_client = None

    # Estimate playback duration and wait
    try:
        with wave.open(wav_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration = frames / rate
        await asyncio.sleep(duration + 0.5)
    except Exception:
        await asyncio.sleep(3)

    state.is_speaking = False

    # Skip restore_mic — it corrupts ES8311 DAC state
    # Mic re-activates on next button press anyway
    log.debug("Skipping restore_mic")


# =============================================================================
# Cleanup old audio files
# =============================================================================
async def cleanup_audio_files():
    """Periodically remove old WAV files to prevent disk bloat."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        audio_dir = CONFIG["AUDIO_DIR"]
        if not os.path.exists(audio_dir):
            continue
        cutoff = time.time() - 600  # Files older than 10 minutes
        for f in os.listdir(audio_dir):
            fpath = os.path.join(audio_dir, f)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.unlink(fpath)
            except Exception:
                pass


# =============================================================================
# Main
# =============================================================================
async def esphome_reconnect_loop():
    """Periodically check ESPHome connection and reconnect if needed."""
    while True:
        await asyncio.sleep(10)
        try:
            if state.esphome_client:
                # Test if still connected by getting device info
                await state.esphome_client.device_info()
            else:
                log.info("ESPHome not connected, attempting reconnect...")
                await init_esphome()
        except Exception:
            log.warning("ESPHome connection lost, reconnecting...")
            state.esphome_client = None
            state.esphome_services = {}
            try:
                await init_esphome()
            except Exception as e:
                log.warning(f"Reconnect failed: {e}. Will retry in 10s.")


async def main():
    """Start all bridge components."""
    log.info("=" * 60)
    log.info("  AIPI Lite → OpenClaw VPS Voice Bridge")
    log.info("=" * 60)
    log.info(f"  UDP listener:  {CONFIG['UDP_LISTEN_HOST']}:{CONFIG['UDP_LISTEN_PORT']}")
    log.info(f"  HTTP server:   {CONFIG['HTTP_HOST']}:{CONFIG['HTTP_PORT']}")
    log.info(f"  ESPHome:       {CONFIG['ESPHOME_HOST']}:{CONFIG['ESPHOME_PORT']}")
    log.info(f"  OpenClaw:      {CONFIG['OPENCLAW_URL']}")
    log.info(f"  STT engine:    faster-whisper ({CONFIG['WHISPER_MODEL']})")
    log.info(f"  TTS engine:    {CONFIG['TTS_ENGINE']}")
    log.info("=" * 60)

    # Create audio directory
    os.makedirs(CONFIG["AUDIO_DIR"], exist_ok=True)

    # Initialize Whisper
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, init_whisper)

    # Connect to ESPHome device
    await init_esphome()

    # Start UDP listener
    transport, protocol = await loop.create_datagram_endpoint(
        UDPAudioProtocol,
        local_addr=(CONFIG["UDP_LISTEN_HOST"], CONFIG["UDP_LISTEN_PORT"]),
    )

    # Start HTTP server
    app = create_http_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, CONFIG["HTTP_HOST"], CONFIG["HTTP_PORT"])
    await site.start()

    # Start cleanup task
    asyncio.create_task(cleanup_audio_files())

    # Start ESPHome reconnect watcher
    asyncio.create_task(esphome_reconnect_loop())

    log.info("Bridge is running! Press Ctrl+C to stop.")

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutting down...")
    finally:
        transport.close()
        await runner.cleanup()
        if state.esphome_client:
            await state.esphome_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
