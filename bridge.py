#!/usr/bin/env python3
"""
WalkieClaw Voice Bridge (Multi-Device)
=======================================

Architecture:
  [ESP32] --UDP audio--> [This Bridge] --HTTP--> [OpenClaw VPS]
  [ESP32] --HTTP poll--> [This Bridge] <--text-- [OpenClaw VPS]
  [ESP32] <--HTTP GET--- [This Bridge] (fetch WAV)

The bridge handles:
  1. Receiving raw I2S audio via UDP from the ESP32
  2. Transcribing speech to text (faster-whisper, local)
  3. Sending the text to your OpenClaw agent's HTTP API
  4. Converting the response to speech (Edge TTS)
  5. Storing result for HTTP polling by ESP32
  6. Serving the WAV file over HTTP for the ESP32 to stream

Multi-device: Each device is keyed by source IP. Per-device state
(audio buffer, poll status, notifications) is isolated so multiple
WalkieClaw devices can share one bridge without cross-talk.

Dependencies:
  pip install aioesphomeapi faster-whisper gtts pydub requests aiohttp edge-tts
"""

import asyncio
import hashlib
import io
import json
import logging
import math
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
# CONFIG
# =============================================================================

CONFIG = {
    # --- Network ---
    "UDP_LISTEN_HOST": os.getenv("UDP_HOST", "0.0.0.0"),
    "UDP_LISTEN_PORT": int(os.getenv("UDP_PORT", "12345")),
    "HTTP_HOST": os.getenv("HTTP_HOST", "0.0.0.0"),
    "HTTP_PORT": int(os.getenv("HTTP_PORT", "8080")),

    # --- ESPHome Device (best-effort, for display updates) ---
    "ESPHOME_HOST": os.getenv("ESPHOME_HOST", "192.168.1.50"),
    "ESPHOME_PORT": int(os.getenv("ESPHOME_PORT", "6053")),
    "ESPHOME_PASSWORD": os.getenv("ESPHOME_PASSWORD", ""),
    "ESPHOME_NOISE_PSK": os.getenv("ESPHOME_NOISE_PSK", ""),

    # --- OpenClaw VPS ---
    "OPENCLAW_URL": os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789"),
    "OPENCLAW_TOKEN": os.getenv("OPENCLAW_TOKEN", ""),
    "OPENCLAW_MODE": os.getenv("OPENCLAW_MODE", "chat"),
    "OPENCLAW_AGENT_ID": os.getenv("OPENCLAW_AGENT_ID", "main"),

    # --- STT ---
    "WHISPER_MODEL": os.getenv("WHISPER_MODEL", "base"),
    "WHISPER_LANGUAGE": os.getenv("WHISPER_LANGUAGE", "en"),
    "WHISPER_DEVICE": os.getenv("WHISPER_DEVICE", "cpu"),

    # --- TTS ---
    "TTS_ENGINE": os.getenv("TTS_ENGINE", "edge"),
    "ELEVENLABS_API_KEY": os.getenv("ELEVENLABS_API_KEY", ""),
    "ELEVENLABS_VOICE_ID": os.getenv("ELEVENLABS_VOICE_ID", ""),

    # --- Audio ---
    "SAMPLE_RATE": 16000,
    "OUTPUT_SAMPLE_RATE": 16000,
    "CHANNELS": 1,
    "SAMPLE_WIDTH": 2,
    "VOLUME_REDUCTION_DB": 0,

    # --- Network ---
    "HTTP_ADVERTISE_HOST": os.getenv("HTTP_ADVERTISE_HOST", ""),

    # --- Paths ---
    "AUDIO_DIR": os.getenv("AUDIO_DIR", "/tmp/walkieclaw-audio"),
    "BEEP_FILE": os.getenv("BEEP_FILE", "beep.wav"),

    # --- Security ---
    "BRIDGE_API_KEY": os.getenv("BRIDGE_API_KEY", ""),
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
# Per-Device State
# =============================================================================
class DeviceState:
    """State for a single WalkieClaw device, keyed by IP address."""
    def __init__(self, ip: str):
        self.ip = ip
        self.audio_buffer = bytearray()
        self.is_processing = False
        # --- Poll state (for HTTP polling by this device) ---
        self.poll_status = "idle"        # "idle" | "processing" | "ready"
        self.poll_stage = ""             # "transcribing" | "thinking" | "speaking"
        self.poll_wav_url = ""
        self.poll_text = ""
        self.poll_transcript = ""
        self.poll_wav_duration = 0.0
        self.poll_ready_time = 0.0
        # --- Pending commands for this device (delivered via poll/health) ---
        self.pending_wifi_ssid = ""
        self.pending_wifi_password = ""
        # --- Pending push notifications for this device ---
        self.pending_notifications: list = []
        # --- Timing ---
        self.last_activity = time.time()
        self.silence_timer = None


# =============================================================================
# Global State (shared across all devices)
# =============================================================================
class BridgeState:
    def __init__(self):
        self.esphome_client = None
        self.esphome_services = {}
        self.whisper_model = None
        # Per-device state map: IP -> DeviceState
        self.devices: dict[str, DeviceState] = {}

    def get_device(self, ip: str) -> DeviceState:
        """Get or create DeviceState for an IP address."""
        if ip not in self.devices:
            log.info(f"New device connected: {ip}")
            self.devices[ip] = DeviceState(ip)
        device = self.devices[ip]
        device.last_activity = time.time()
        return device

state = BridgeState()

# =============================================================================
# Security: API Key validation + Rate limiting
# =============================================================================
_rate_limit_store: dict = {}  # ip -> list of timestamps
RATE_LIMIT_MAX = 30           # requests per window
RATE_LIMIT_WINDOW = 60        # seconds

def _check_api_key(request: web.Request) -> bool:
    """Validate X-API-Key header against configured BRIDGE_API_KEY."""
    expected = CONFIG.get("BRIDGE_API_KEY", "")
    if not expected:
        return True  # no key configured = open (backwards compat)
    provided = request.headers.get("X-API-Key", "")
    return provided == expected

def _rate_limit_ok(ip: str) -> bool:
    """Return True if the IP is within rate limits."""
    now = time.time()
    timestamps = _rate_limit_store.get(ip, [])
    # Prune old entries
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= RATE_LIMIT_MAX:
        _rate_limit_store[ip] = timestamps
        return False
    timestamps.append(now)
    _rate_limit_store[ip] = timestamps
    return True

# Track IPs that sent a valid keyed START in current session
_authenticated_udp_ips: set = set()

# =============================================================================
# STT
# =============================================================================
def init_whisper():
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
    if not state.whisper_model:
        log.error("Whisper model not initialized!")
        return ""

    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(CONFIG["CHANNELS"])
        wf.setsampwidth(CONFIG["SAMPLE_WIDTH"])
        wf.setframerate(CONFIG["SAMPLE_RATE"])
        wf.writeframes(audio_bytes)
    wav_buf.seek(0)

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
        os.unlink(tmp_path)


# =============================================================================
# OpenClaw Agent Communication
# =============================================================================
async def send_to_openclaw(text: str, device_ip: str = "") -> str:
    if not text.strip():
        return "I didn't catch that. Could you say it again?"

    mode = CONFIG.get("OPENCLAW_MODE", "chat")
    log.info(f"Sending to OpenClaw ({mode}) [device={device_ip}]: {text}")

    try:
        if mode == "chat":
            return await _openclaw_chat_completions(text, device_ip)
        else:
            return await _openclaw_hooks_agent(text)
    except asyncio.TimeoutError:
        log.error("OpenClaw request timed out")
        return "Sorry, the request timed out."
    except Exception as e:
        log.error(f"OpenClaw error: {e}")
        return "Sorry, I encountered an error."


def _extract_tts_from_session(device_ip: str = "") -> str:
    """Extract TTS text from the most recent OpenClaw session log.

    When the agent uses a TTS tool call instead of plain text,
    the chat completions endpoint returns 'No response from OpenClaw.'
    This reads the session log to find the spoken text.
    """
    sessions_dir = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
    sessions_index = sessions_dir / "sessions.json"
    if not sessions_index.exists():
        return ""
    try:
        with open(sessions_index) as f:
            idx = json.load(f)
        user_key = f"walkieclaw-{device_ip}" if device_ip else "walkieclaw"
        session_info = idx.get(f"agent:main:openai-user:{user_key}", {})
        if not session_info:
            # Fallback to legacy key
            session_info = idx.get("agent:main:openai-user:walkieclaw", {})
        sid = session_info.get("sessionId", "")
        if not sid:
            return ""
        session_file = sessions_dir / f"{sid}.jsonl"
        if not session_file.exists():
            return ""
        # Read last 20 lines looking for TTS tool calls
        lines = session_file.read_text().strip().split("\n")[-20:]
        tts_texts = []
        for line in reversed(lines):
            try:
                entry = json.loads(line)
                msg = entry.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for item in content:
                        if item.get("type") == "toolCall" and item.get("name") == "tts":
                            args = item.get("arguments", {})
                            if isinstance(args, str):
                                args = json.loads(args)
                            txt = args.get("text", "")
                            if txt:
                                tts_texts.append(txt)
                # Stop at first user message (don't go further back)
                role = msg.get("role", "")
                if role == "user":
                    break
            except (json.JSONDecodeError, KeyError):
                continue
        # Return combined TTS texts in order
        tts_texts.reverse()
        return " ".join(tts_texts) if tts_texts else ""
    except Exception as e:
        logging.getLogger("bridge").warning(f"TTS extraction failed: {e}")
        return ""


async def _openclaw_chat_completions(text: str, device_ip: str = "") -> str:
    base_url = CONFIG["OPENCLAW_URL"].rstrip("/")
    url = f"{base_url}/v1/chat/completions"

    headers = {"Content-Type": "application/json"}
    token = CONFIG.get("OPENCLAW_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    agent_id = CONFIG.get("OPENCLAW_AGENT_ID", "main")
    model = f"openclaw:{agent_id}"

    # Per-device user ID for separate conversation histories
    user_id = f"walkieclaw-{device_ip}" if device_ip else "walkieclaw"

    payload = {
        "model": model,
        "user": user_id,
        "stream": False,
        "messages": [
            {"role": "user", "content": text}
        ],
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                else:
                    content = str(data)
                content = re.sub(
                    r"<think>.*?</think>", "", content, flags=re.DOTALL
                ).strip()
                # If gateway returned "No response" it may have used TTS tool
                # instead of text -- extract spoken text from session log
                if not content or content == "No response from OpenClaw.":
                    tts_text = _extract_tts_from_session(device_ip)
                    if tts_text:
                        content = tts_text
                        log.info(f"Extracted TTS text: {content[:100]}...")
                log.info(f"OpenClaw response [{user_id}]: {content[:100]}...")
                return content or "I processed that but had nothing to say."
            else:
                body = await resp.text()
                log.error(f"OpenClaw chat HTTP {resp.status}: {body[:200]}")
                return "Sorry, I had trouble connecting to my brain."


async def _openclaw_hooks_agent(text: str) -> str:
    base_url = CONFIG["OPENCLAW_URL"].rstrip("/")
    url = f"{base_url}/hooks/agent"

    headers = {"Content-Type": "application/json"}
    token = CONFIG.get("OPENCLAW_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "message": text,
        "name": "WalkieClaw Voice",
        "sessionKey": "hook:walkieclaw-voice",
        "wakeMode": "now",
        "timeoutSeconds": 30,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=300),
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
# TTS
# =============================================================================
async def synthesize_speech(text: str) -> str:
    os.makedirs(CONFIG["AUDIO_DIR"], exist_ok=True)

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

    wav_path = _postprocess_audio(wav_path)

    log.info(f"TTS generated: {wav_path}")
    return wav_path


async def _tts_edge(text: str, output_path: str):
    import edge_tts
    from pydub import AudioSegment

    mp3_path = output_path.replace(".wav", ".mp3")
    voice = CONFIG.get("EDGE_TTS_VOICE", "en-US-GuyNeural")
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(mp3_path)

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
    from gtts import gTTS

    loop = asyncio.get_event_loop()
    def _generate():
        tts = gTTS(text=text, lang="en", slow=False)
        mp3_path = output_path.replace(".wav", ".mp3")
        tts.save(mp3_path)
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(mp3_path)
        audio = audio.set_frame_rate(CONFIG["OUTPUT_SAMPLE_RATE"])
        audio = audio.set_channels(1)
        audio = audio.set_sample_width(2)
        audio.export(output_path, format="wav")
        os.unlink(mp3_path)

    await loop.run_in_executor(None, _generate)


async def _tts_elevenlabs(text: str, output_path: str):
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
                await _tts_gtts(text, output_path)


def _postprocess_audio(wav_path: str) -> str:
    from pydub import AudioSegment

    audio = AudioSegment.from_wav(wav_path)
    audio = audio.set_frame_rate(CONFIG["OUTPUT_SAMPLE_RATE"])
    audio = audio.set_channels(1)
    audio = audio.set_sample_width(2)
    audio = audio + CONFIG["VOLUME_REDUCTION_DB"]

    # Generate a simple 800Hz tone beep
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
    ) - 12

    silence_tail = AudioSegment.silent(
        duration=200,
        frame_rate=CONFIG["OUTPUT_SAMPLE_RATE"],
    )

    final = beep + AudioSegment.silent(duration=100) + audio + silence_tail
    final.export(wav_path, format="wav")
    return wav_path


# =============================================================================
# Helper: get WAV URL for a file path
# =============================================================================
def _wav_url(wav_path: str) -> str:
    filename = os.path.basename(wav_path)
    advertise_host = CONFIG.get("HTTP_ADVERTISE_HOST", "")
    if advertise_host:
        lan_ip = advertise_host
    elif CONFIG["HTTP_HOST"] == "0.0.0.0":
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
        finally:
            s.close()
    else:
        lan_ip = CONFIG["HTTP_HOST"]
    return f"http://{lan_ip}:{CONFIG['HTTP_PORT']}/audio/{filename}?t={int(time.time() * 1000)}"


def _wav_duration(wav_path: str) -> float:
    try:
        with wave.open(wav_path, "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 3.0


# =============================================================================
# UDP Audio Receiver
# =============================================================================
class UDPAudioProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        log.info(f"UDP listener ready on "
                 f"{CONFIG['UDP_LISTEN_HOST']}:{CONFIG['UDP_LISTEN_PORT']}")

    def datagram_received(self, data: bytes, addr):
        sender_ip = addr[0]

        # Handle markers
        if data.startswith(b'ESP_IP:'):
            esp_ip = data.decode().split(':', 1)[1]
            if esp_ip != CONFIG.get('ESPHOME_HOST') or not state.esphome_client:
                log.info(f'ESP32 detected at {esp_ip}')
                CONFIG['ESPHOME_HOST'] = esp_ip
                if not state.esphome_client:
                    asyncio.ensure_future(init_esphome())
            return

        # Handle keyed START marker: START:<first_8_chars_of_api_key>
        if data.startswith(b'START'):
            api_key = CONFIG.get("BRIDGE_API_KEY", "")
            if api_key:
                expected_prefix = f"START:{api_key[:8]}".encode()
                if data == expected_prefix:
                    _authenticated_udp_ips.add(sender_ip)
                    log.info(f"UDP authenticated START from {sender_ip}")
                elif data == b'START':
                    log.warning(f"UDP rejected unauthenticated START from {sender_ip}")
                    return
                else:
                    log.warning(f"UDP rejected bad START key from {sender_ip}")
                    return
            else:
                # No key configured, accept plain START
                _authenticated_udp_ips.add(sender_ip)
                log.debug(f"UDP marker: START from {sender_ip} (no auth)")
            # Touch device state on START
            state.get_device(sender_ip)
            return

        if data == b'STOP':
            log.debug(f'UDP marker: STOP from {sender_ip}')
            return

        # Drop PCM data from unauthenticated IPs
        api_key = CONFIG.get("BRIDGE_API_KEY", "")
        if api_key and sender_ip not in _authenticated_udp_ips:
            return

        # Get per-device state
        device = state.get_device(sender_ip)

        # Block new audio only if THIS device is busy
        if device.is_processing or device.poll_status == "ready":
            return

        device.audio_buffer.extend(data)

        if device.silence_timer:
            device.silence_timer.cancel()

        loop = asyncio.get_event_loop()
        device.silence_timer = loop.call_later(
            1.5, lambda ip=sender_ip: asyncio.ensure_future(_process_buffer(ip))
        )

    def connection_lost(self, exc):
        log.info("UDP transport closed")


async def _process_buffer(device_ip: str):
    """Process audio buffer for a specific device."""
    device = state.devices.get(device_ip)
    if not device or not device.audio_buffer or device.is_processing:
        return

    device.is_processing = True
    device.poll_status = "processing"
    device.poll_stage = "transcribing"
    audio_data = bytes(device.audio_buffer)
    device.audio_buffer.clear()

    # ESP32 I2S sends 32-bit samples; extract high 16-bit of each
    if len(audio_data) >= 4:
        n_samples_32 = len(audio_data) // 4
        samples_32 = struct.unpack(f"<{n_samples_32}i", audio_data[:n_samples_32*4])
        samples_16 = [((s >> 16) & 0xFFFF) for s in samples_32]
        audio_data = struct.pack(f"<{len(samples_16)}h",
            *[s if s < 32768 else s - 65536 for s in samples_16])

    log.info(f"Processing {len(audio_data)} bytes of audio from {device_ip}...")

    try:
        # 1. Transcribe
        device.poll_stage = "transcribing"
        await command_update_display("WalkieClaw", "Transcribing...")
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, transcribe_audio, audio_data)

        if not text.strip():
            log.info(f"No speech detected from {device_ip}, skipping.")
            await command_update_display("WalkieClaw", "Ready", "")
            device.is_processing = False
            device.poll_status = "idle"
            device.poll_stage = ""
            return

        device.poll_transcript = text
        device.poll_stage = "thinking"
        await command_update_display("WalkieClaw", "Thinking...", sanitize_for_display(f'"{text[:60]}"'))

        # 2. Send to OpenClaw (per-device user ID)
        response = await send_to_openclaw(text, device_ip)

        # 3. Generate TTS
        device.poll_stage = "speaking"
        wav_path = await synthesize_speech(response)

        # 4. Update display (best-effort via ESPHome API)
        await command_update_display("WalkieClaw", "Speaking...", sanitize_for_display(response[:200]))

        # 5. Store result for HTTP polling by this device
        wav_url = _wav_url(wav_path)
        duration = _wav_duration(wav_path)
        device.poll_wav_url = wav_url
        device.poll_text = sanitize_for_display(response[:200])
        device.poll_wav_duration = duration
        device.poll_status = "ready"
        device.poll_ready_time = time.time()
        log.info(f"Response ready for {device_ip}: {wav_url} ({duration:.1f}s)")

    except Exception as e:
        log.error(f"Pipeline error for {device_ip}: {e}", exc_info=True)
        device.poll_status = "idle"
        device.poll_stage = ""
    finally:
        device.is_processing = False


# =============================================================================
# HTTP Server
# =============================================================================
async def handle_audio_request(request: web.Request) -> web.Response:
    # No API key check -- media_player can't send custom headers.
    # WAV filenames contain hash+timestamp so they're unguessable.
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
    if not _check_api_key(request):
        return web.Response(status=401, text="Unauthorized")
    device_ip = request.remote or "unknown"
    device = state.get_device(device_ip)
    resp = {
        "status": "ok",
        "device": device_ip,
        "processing": device.is_processing,
        "poll_status": device.poll_status,
        "uptime": time.time() - device.last_activity,
        "connected_devices": len(state.devices),
    }
    # Deliver pending WiFi command for this device
    if device.pending_wifi_ssid:
        resp["wifi_ssid"] = device.pending_wifi_ssid
        resp["wifi_password"] = device.pending_wifi_password
        log.info(f"Delivering WiFi command to {device_ip}: SSID={device.pending_wifi_ssid}")
        device.pending_wifi_ssid = ""
        device.pending_wifi_password = ""
    # Deliver pending push notification for this device
    if device.pending_notifications and not device.is_processing and device.poll_status == "idle":
        notif = device.pending_notifications.pop(0)
        resp["notify_text"] = notif["text"]
        resp["notify_wav_url"] = notif["wav_url"]
        resp["notify_duration"] = notif["duration"]
        log.info(f"Delivering notification to {device_ip}: {notif['text'][:60]}")
    return web.json_response(resp)


async def handle_poll_response(request: web.Request) -> web.Response:
    """ESP32 polls this to check for processed responses."""
    if not _check_api_key(request):
        return web.Response(status=401, text="Unauthorized")
    ip = request.remote or "unknown"
    if not _rate_limit_ok(ip):
        return web.Response(status=429, text="Too many requests")

    device = state.get_device(ip)

    if device.poll_status == "idle":
        return web.json_response({"status": "idle"})
    elif device.poll_status == "processing":
        resp = {
            "status": "processing",
            "stage": device.poll_stage,
        }
        if device.poll_transcript:
            resp["transcript"] = device.poll_transcript
        return web.json_response(resp)
    elif device.poll_status == "ready":
        result = {
            "status": "ready",
            "wav_url": device.poll_wav_url,
            "text": device.poll_text,
            "transcript": device.poll_transcript,
            "duration": round(device.poll_wav_duration, 1),
        }
        # Auto-acknowledge: transition back to idle
        device.poll_status = "idle"
        device.poll_stage = ""
        device.poll_wav_url = ""
        device.poll_text = ""
        device.poll_transcript = ""
        device.poll_wav_duration = 0.0
        device.poll_ready_time = 0.0
        log.info(f"Poll response served to {ip} and acknowledged")
        return web.json_response(result)
    else:
        return web.json_response({"status": "idle"})


async def handle_notify(request: web.Request) -> web.Response:
    """Queue a push notification. Optional 'device' field targets one device; omit to broadcast."""
    if not _check_api_key(request):
        return web.Response(status=401, text="Unauthorized")
    try:
        data = await request.json()
        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"error": "text is required"}, status=400)

        target_ip = data.get("device", "").strip()
        display_text = sanitize_for_display(text[:200])

        # Generate TTS once
        log.info(f"Generating push notification TTS: {text[:60]}...")
        wav_path = await synthesize_speech(text)
        wav_url = _wav_url(wav_path)
        duration = _wav_duration(wav_path)

        notif = {
            "text": display_text,
            "wav_url": wav_url,
            "duration": round(duration, 1),
        }

        if target_ip:
            # Target specific device
            device = state.get_device(target_ip)
            device.pending_notifications.append(notif)
            log.info(f"Notification queued for {target_ip} ({len(device.pending_notifications)} pending)")
            return web.json_response({
                "status": "queued",
                "device": target_ip,
                "text": display_text,
                "pending": len(device.pending_notifications),
                "message": "Notification queued. Device will pick it up within 30s.",
            })
        else:
            # Broadcast to all known devices
            count = 0
            for ip, dev in state.devices.items():
                dev.pending_notifications.append(dict(notif))
                count += 1
            log.info(f"Notification broadcast to {count} device(s)")
            return web.json_response({
                "status": "broadcast",
                "text": display_text,
                "devices": count,
                "message": f"Notification broadcast to {count} device(s). Each will pick it up within 30s.",
            })
    except Exception as e:
        log.error(f"notify error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_connect_wifi(request: web.Request) -> web.Response:
    """Queue a WiFi command. Optional 'device' field targets one device; omit to broadcast."""
    if not _check_api_key(request):
        return web.Response(status=401, text="Unauthorized")
    try:
        data = await request.json()
        ssid = data.get("ssid", "").strip()
        password = data.get("password", "").strip()

        if not ssid:
            return web.json_response({"error": "ssid is required"}, status=400)

        target_ip = data.get("device", "").strip()

        if target_ip:
            device = state.get_device(target_ip)
            device.pending_wifi_ssid = ssid
            device.pending_wifi_password = password
            log.info(f"Queued WiFi command for {target_ip}: SSID={ssid}")
            return web.json_response({
                "status": "queued",
                "device": target_ip,
                "ssid": ssid,
                "message": f"WiFi command queued for {target_ip}. Will deliver within 30s.",
            })
        else:
            count = 0
            for ip, dev in state.devices.items():
                dev.pending_wifi_ssid = ssid
                dev.pending_wifi_password = password
                count += 1
            log.info(f"WiFi command broadcast to {count} device(s): SSID={ssid}")
            return web.json_response({
                "status": "broadcast",
                "ssid": ssid,
                "devices": count,
                "message": f"WiFi command broadcast to {count} device(s). Each will pick up within 30s.",
            })
    except Exception as e:
        log.error(f"connect_wifi error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_devices(request: web.Request) -> web.Response:
    """List connected devices and their status."""
    if not _check_api_key(request):
        return web.Response(status=401, text="Unauthorized")
    devices = []
    for ip, device in state.devices.items():
        devices.append({
            "ip": ip,
            "poll_status": device.poll_status,
            "is_processing": device.is_processing,
            "last_activity": round(time.time() - device.last_activity, 1),
            "pending_notifications": len(device.pending_notifications),
        })
    return web.json_response({"devices": devices, "count": len(devices)})


def create_http_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/response", handle_poll_response)
    app.router.add_get("/api/devices", handle_devices)
    app.router.add_get("/audio/{filename}", handle_audio_request)
    app.router.add_post("/api/connect_wifi", handle_connect_wifi)
    app.router.add_post("/api/notify", handle_notify)
    return app


# =============================================================================
# ESPHome Native API (best-effort for display updates)
# =============================================================================
async def init_esphome():
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
    replacements = {
        chr(0x2014): chr(45), chr(0x2013): chr(45),
        chr(0x2018): chr(39), chr(0x2019): chr(39),
        chr(0x201c): chr(34), chr(0x201d): chr(34),
        chr(0x2026): "...",
        chr(0x2022): "*",
        chr(0x00a0): " ",
    }
    for old_char, new_char in replacements.items():
        text = text.replace(old_char, new_char)
    # Replace newlines/tabs with spaces, collapse multiple spaces
    text = " ".join(text.split())
    text = text.encode("ascii", "replace").decode("ascii")
    return text


async def command_update_display(status: str, action: str, response: str = ""):
    try:
        if state.esphome_client:
            svc = state.esphome_services.get("update_display")
            if svc:
                log.info(f"Display update: action={action!r}, response={response[:40]!r}")
                await state.esphome_client.execute_service(svc, {"status_text": status, "action_text": action, "response_text": response})
            else:
                log.debug("update_display service not found in esphome_services")
        else:
            log.debug("update_display skipped: no esphome_client")
    except Exception as e:
        log.warning(f"update_display failed: {e}")
        state.esphome_client = None


# =============================================================================
# Background Tasks
# =============================================================================
async def cleanup_audio_files():
    while True:
        await asyncio.sleep(300)
        audio_dir = CONFIG["AUDIO_DIR"]
        if not os.path.exists(audio_dir):
            continue
        cutoff = time.time() - 600
        for f in os.listdir(audio_dir):
            fpath = os.path.join(audio_dir, f)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.unlink(fpath)
            except Exception:
                pass


async def stale_cleanup():
    """Reset stale poll state and remove inactive devices."""
    while True:
        await asyncio.sleep(10)
        now = time.time()
        stale_ips = []
        for ip, device in state.devices.items():
            # Reset stale ready responses (>60s uncollected)
            if device.poll_status == "ready" and device.poll_ready_time > 0:
                if now - device.poll_ready_time > 60:
                    log.warning(f"Stale poll response for {ip} (>60s), resetting to idle")
                    device.poll_status = "idle"
                    device.poll_stage = ""
                    device.poll_wav_url = ""
                    device.poll_text = ""
                    device.poll_transcript = ""
                    device.poll_wav_duration = 0.0
                    device.poll_ready_time = 0.0
            # Mark devices inactive for 10+ minutes for removal
            if (now - device.last_activity > 600
                    and not device.is_processing
                    and device.poll_status == "idle"
                    and not device.pending_notifications):
                stale_ips.append(ip)
        for ip in stale_ips:
            log.info(f"Removing stale device: {ip} (inactive >10min)")
            del state.devices[ip]
            _authenticated_udp_ips.discard(ip)


async def esphome_reconnect_loop():
    while True:
        await asyncio.sleep(10)
        try:
            if state.esphome_client:
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


# =============================================================================
# Main
# =============================================================================
async def main():
    log.info("=" * 60)
    log.info("  WalkieClaw Voice Bridge (Multi-Device)")
    log.info("=" * 60)
    log.info(f"  UDP listener:  {CONFIG['UDP_LISTEN_HOST']}:{CONFIG['UDP_LISTEN_PORT']}")
    log.info(f"  HTTP server:   {CONFIG['HTTP_HOST']}:{CONFIG['HTTP_PORT']}")
    log.info(f"  OpenClaw:      {CONFIG['OPENCLAW_URL']}")
    log.info(f"  STT engine:    faster-whisper ({CONFIG['WHISPER_MODEL']})")
    log.info(f"  TTS engine:    {CONFIG['TTS_ENGINE']}")
    log.info(f"  Poll endpoint: GET /api/response")
    log.info(f"  Devices:       GET /api/devices")
    log.info("=" * 60)

    os.makedirs(CONFIG["AUDIO_DIR"], exist_ok=True)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, init_whisper)

    # ESPHome connect (best-effort -- voice pipeline works without it)
    await init_esphome()

    transport, protocol = await loop.create_datagram_endpoint(
        UDPAudioProtocol,
        local_addr=(CONFIG["UDP_LISTEN_HOST"], CONFIG["UDP_LISTEN_PORT"]),
    )

    app = create_http_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, CONFIG["HTTP_HOST"], CONFIG["HTTP_PORT"])
    await site.start()

    asyncio.create_task(cleanup_audio_files())
    asyncio.create_task(stale_cleanup())
    asyncio.create_task(esphome_reconnect_loop())

    log.info("Bridge is running! Press Ctrl+C to stop.")

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
