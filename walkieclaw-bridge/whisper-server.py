#!/usr/bin/env python3
"""Tiny faster-whisper HTTP server for GPU-accelerated speech-to-text.
Stays running — model loaded once in GPU memory. Sub-second transcription.

Usage: python whisper-server.py [--model base] [--port 8787]
"""
import argparse
import io
import struct
import sys
import wave
from http.server import HTTPServer, BaseHTTPRequestHandler

from faster_whisper import WhisperModel

model = None

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/transcribe":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        content_type = self.headers.get("Content-Type", "")

        try:
            if "audio/wav" in content_type or body[:4] == b"RIFF":
                # WAV file
                segments, info = model.transcribe(
                    io.BytesIO(body), language="en", beam_size=1, vad_filter=True
                )
            else:
                # Raw 16-bit PCM at 16kHz mono — wrap in WAV
                wav_buf = io.BytesIO()
                with wave.open(wav_buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(body)
                wav_buf.seek(0)
                segments, info = model.transcribe(
                    wav_buf, language="en", beam_size=1, vad_filter=True
                )

            text = " ".join(s.text.strip() for s in segments).strip()
            response = f'{{"text":"{text}","duration":{info.duration:.1f}}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"error":"{str(e)}"}}'.encode())

    def log_message(self, format, *args):
        print(f"[whisper-gpu] {args[0]}")


def main():
    global model
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="base", help="Whisper model size")
    parser.add_argument("--port", type=int, default=8787, help="HTTP port")
    parser.add_argument("--device", default="cuda", help="Device: cuda or cpu")
    args = parser.parse_args()

    print(f"[whisper-gpu] Loading model '{args.model}' on {args.device}...")
    model = WhisperModel(args.model, device=args.device, compute_type="float16")
    print(f"[whisper-gpu] Model loaded. Listening on :{args.port}")

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[whisper-gpu] Shutting down.")


if __name__ == "__main__":
    main()
