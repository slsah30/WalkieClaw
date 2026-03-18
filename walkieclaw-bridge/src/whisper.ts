import { resample } from "./audio.js";

const WHISPER_URL = process.env.WHISPER_URL ?? "http://127.0.0.1:8787";

/**
 * Initialize — verify the GPU whisper server is reachable.
 */
export async function initWhisper(_modelName: string): Promise<void> {
  console.log(`[whisper] Using GPU whisper server at ${WHISPER_URL}`);
  try {
    const resp = await fetch(`${WHISPER_URL}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "audio/pcm" },
      body: new Uint8Array(3200), // 100ms of silence
    });
    if (resp.ok) {
      console.log("[whisper] GPU server is ready.");
    } else {
      console.warn(`[whisper] GPU server returned ${resp.status} — may need to start whisper-server.py`);
    }
  } catch {
    console.warn("[whisper] GPU server not reachable. Start it with:");
    console.warn("[whisper]   python walkieclaw-bridge/whisper-server.py");
  }
}

/**
 * Transcribe 16-bit PCM audio via GPU whisper server.
 * Sends raw PCM, server wraps in WAV and runs faster-whisper on GPU.
 */
export async function transcribe(
  pcmData: Buffer,
  sampleRate: number,
  language = "en"
): Promise<string> {
  try {
    // Resample to 16kHz if needed
    const pcm16k = sampleRate === 16000 ? pcmData : resample(pcmData, sampleRate, 16000);

    const startTime = Date.now();

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30_000);

    const resp = await fetch(`${WHISPER_URL}/transcribe?lang=${encodeURIComponent(language)}`, {
      method: "POST",
      headers: { "Content-Type": "audio/pcm" },
      body: new Uint8Array(pcm16k),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!resp.ok) {
      const err = await resp.text().catch(() => "");
      console.error(`[whisper] Server error ${resp.status}: ${err}`);
      return "";
    }

    const data = await resp.json() as any;
    const text = (data.text ?? "").trim();
    const elapsed = Date.now() - startTime;
    const duration = (pcmData.length / 2 / sampleRate).toFixed(1);
    console.log(`[whisper] STT result (${duration}s audio, ${elapsed}ms GPU): ${text}`);
    return text;
  } catch (err: any) {
    console.error(`[whisper] Error: ${err.message}`);
    console.warn("[whisper] Is whisper-server.py running?");
    return "";
  }
}
