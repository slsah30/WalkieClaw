import { join } from "path";
import { writeFileSync, readFileSync, unlinkSync } from "fs";
import { createHash } from "crypto";
import { postprocessAudio, resample, extractPcmFromWav, pcmToWav } from "./audio.js";
import { getLocalIP, type BridgeConfig } from "./config.js";

/**
 * Synthesize speech from text and return the path to the WAV file.
 */
export async function synthesizeSpeech(
  text: string,
  config: BridgeConfig
): Promise<string> {
  const textHash = createHash("md5").update(text).digest("hex").slice(0, 8);
  const timestamp = Date.now();
  const filename = `tts_${textHash}_${timestamp}.wav`;
  const wavPath = join(config.audioDir, filename);

  await edgeTts(text, wavPath, config);

  console.log(`[tts] Generated: ${wavPath}`);
  return wavPath;
}

async function edgeTts(
  text: string,
  outputPath: string,
  config: BridgeConfig
): Promise<void> {
  const { EdgeTTS } = await import("@andresaya/edge-tts");
  const { randomBytes } = await import("crypto");
  const { tmpdir } = await import("os");

  const tts = new EdgeTTS();
  const tmpMp3 = join(tmpdir(), `walkieclaw_tts_${randomBytes(4).toString("hex")}.mp3`);

  try {
    await tts.synthesize(text, config.ttsVoice);

    // Get audio as buffer and write to temp MP3 file ourselves
    const mp3Buf = tts.toBuffer();
    writeFileSync(tmpMp3, mp3Buf);
    console.log(`[tts] MP3 saved (${mp3Buf.length} bytes): ${tmpMp3}`);

    // Convert MP3 to WAV via ffmpeg
    await convertMp3FileToWav(tmpMp3, outputPath, config);
  } finally {
    try { unlinkSync(tmpMp3); } catch {}
  }
}

/**
 * Convert an MP3 file to WAV via ffmpeg.
 */
async function convertMp3FileToWav(
  mp3Path: string,
  wavPath: string,
  config: BridgeConfig
): Promise<void> {
  try {
    const { execFileSync } = await import("child_process");

    execFileSync("ffmpeg", [
      "-y", "-i", mp3Path,
      "-ar", String(config.outputSampleRate),
      "-ac", "1",
      "-sample_fmt", "s16",
      wavPath,
    ], { timeout: 10000, stdio: "pipe" });

    const wav = readFileSync(wavPath);
    const processed = postprocessAudio(wav, config.outputSampleRate);
    writeFileSync(wavPath, processed);
    console.log(`[tts] WAV converted: ${wavPath}`);
  } catch (err: any) {
    console.error(`[tts] ffmpeg conversion failed: ${err.message}`);
    console.warn("[tts] Please install ffmpeg:");
    console.warn("[tts]   Ubuntu/Debian: sudo apt install ffmpeg");
    console.warn("[tts]   macOS: brew install ffmpeg");
    console.warn("[tts]   Windows: winget install ffmpeg");

    // Write silence as placeholder so pipeline doesn't crash
    const silence = Buffer.alloc(config.outputSampleRate * 2);
    const wav = pcmToWav(silence, config.outputSampleRate);
    writeFileSync(wavPath, wav);
  }
}

/**
 * Get the HTTP URL for a WAV file.
 */
export function getWavUrl(wavPath: string, config: BridgeConfig): string {
  const filename = wavPath.split(/[/\\]/).pop()!;
  const host = config.httpAdvertiseHost || (config.httpHost === "0.0.0.0"
    ? getLocalIP()
    : config.httpHost);
  return `http://${host}:${config.httpPort}/audio/${filename}?t=${Date.now()}`;
}
