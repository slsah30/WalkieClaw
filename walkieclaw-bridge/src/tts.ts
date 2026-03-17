import { join } from "path";
import { writeFileSync } from "fs";
import { createHash } from "crypto";
import { postprocessAudio, pcmToWav, resample } from "./audio.js";
import { getLocalIP, type BridgeConfig } from "./config.js";

/**
 * Synthesize speech from text and return the path to the WAV file.
 * No ffmpeg needed — uses WASM MP3 decoder.
 */
export async function synthesizeSpeech(
  text: string,
  config: BridgeConfig
): Promise<string> {
  const textHash = createHash("md5").update(text).digest("hex").slice(0, 8);
  const timestamp = Date.now();
  const filename = `tts_${textHash}_${timestamp}.wav`;
  const wavPath = join(config.audioDir, filename);

  const { EdgeTTS } = await import("@andresaya/edge-tts");
  const { MPEGDecoder } = await import("mpg123-decoder");

  const tts = new EdgeTTS();
  await tts.synthesize(text, config.ttsVoice);
  const mp3Buf = tts.toBuffer();

  // Decode MP3 → PCM using WASM decoder (no subprocess)
  const decoder = new MPEGDecoder();
  await decoder.ready;
  const decoded = decoder.decode(new Uint8Array(mp3Buf));
  decoder.free();

  // Mix to mono (average channels) + convert Float32 → Int16
  const channels = decoded.channelData;
  const numSamples = channels[0].length;
  const mono = new Int16Array(numSamples);
  for (let i = 0; i < numSamples; i++) {
    let sample = channels[0][i];
    if (channels.length > 1) sample = (sample + channels[1][i]) / 2;
    mono[i] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
  }

  // Resample to target rate if needed (Edge TTS outputs 24kHz)
  // Copy via Uint8Array to avoid TypeScript ArrayBuffer/SharedArrayBuffer issues
  let pcm: Buffer = Buffer.alloc(mono.byteLength);
  Buffer.from(new Uint8Array(mono.buffer, mono.byteOffset, mono.byteLength)).copy(pcm);
  if (decoded.sampleRate !== config.outputSampleRate) {
    pcm = resample(pcm, decoded.sampleRate, config.outputSampleRate);
  }

  // Wrap in WAV + add beep/silence
  const wav = pcmToWav(pcm, config.outputSampleRate);
  const processed = postprocessAudio(wav, config.outputSampleRate);
  writeFileSync(wavPath, processed);

  console.log(`[tts] Generated (${(mp3Buf.length / 1024).toFixed(0)}KB MP3 → ${(processed.length / 1024).toFixed(0)}KB WAV): ${wavPath}`);
  return wavPath;
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
