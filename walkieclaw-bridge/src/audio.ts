import { writeFileSync, readFileSync } from "fs";

/**
 * Convert I2S 32-bit samples to 16-bit PCM.
 * ESP32 I2S sends 32-bit signed integers; we extract the high 16 bits.
 */
export function i2sTo16bitPcm(data: Buffer): Buffer {
  if (data.length < 4) return data;

  const numSamples = Math.floor(data.length / 4);
  const output = Buffer.alloc(numSamples * 2);

  for (let i = 0; i < numSamples; i++) {
    const sample32 = data.readInt32LE(i * 4);
    const sample16 = (sample32 >> 16) & 0xffff;
    // Convert unsigned 16-bit to signed
    output.writeInt16LE(sample16 < 32768 ? sample16 : sample16 - 65536, i * 2);
  }

  return output;
}

/**
 * Wrap raw 16-bit PCM data into a WAV file buffer.
 */
export function pcmToWav(
  pcmData: Buffer,
  sampleRate: number,
  channels = 1,
  bitsPerSample = 16
): Buffer {
  const byteRate = sampleRate * channels * (bitsPerSample / 8);
  const blockAlign = channels * (bitsPerSample / 8);
  const dataSize = pcmData.length;
  const headerSize = 44;
  const wav = Buffer.alloc(headerSize + dataSize);

  // RIFF header
  wav.write("RIFF", 0);
  wav.writeUInt32LE(36 + dataSize, 4);
  wav.write("WAVE", 8);

  // fmt chunk
  wav.write("fmt ", 12);
  wav.writeUInt32LE(16, 16); // chunk size
  wav.writeUInt16LE(1, 20); // PCM format
  wav.writeUInt16LE(channels, 22);
  wav.writeUInt32LE(sampleRate, 24);
  wav.writeUInt32LE(byteRate, 28);
  wav.writeUInt16LE(blockAlign, 32);
  wav.writeUInt16LE(bitsPerSample, 34);

  // data chunk
  wav.write("data", 36);
  wav.writeUInt32LE(dataSize, 40);
  pcmData.copy(wav, 44);

  return wav;
}

/**
 * Generate an 800Hz beep tone as 16-bit PCM.
 */
export function generateBeep(
  sampleRate: number,
  durationSec = 0.08,
  amplitude = 4000,
  frequency = 800
): Buffer {
  const numSamples = Math.floor(sampleRate * durationSec);
  const buf = Buffer.alloc(numSamples * 2);

  for (let i = 0; i < numSamples; i++) {
    const sample = Math.floor(
      amplitude * Math.sin((2 * Math.PI * frequency * i) / sampleRate)
    );
    buf.writeInt16LE(Math.max(-32768, Math.min(32767, sample)), i * 2);
  }

  // Reduce volume by ~12dB (factor of ~0.25)
  for (let i = 0; i < numSamples; i++) {
    const sample = buf.readInt16LE(i * 2);
    buf.writeInt16LE(Math.floor(sample * 0.25), i * 2);
  }

  return buf;
}

/**
 * Generate silence as 16-bit PCM.
 */
export function generateSilence(sampleRate: number, durationMs: number): Buffer {
  const numSamples = Math.floor(sampleRate * (durationMs / 1000));
  return Buffer.alloc(numSamples * 2); // zeros = silence
}

/**
 * Prepend a beep + silence to audio, append trailing silence.
 * Returns a complete WAV buffer.
 */
export function postprocessAudio(wavData: Buffer, sampleRate: number): Buffer {
  // Extract PCM from WAV (skip 44-byte header)
  const pcm = extractPcmFromWav(wavData);

  const beep = generateBeep(sampleRate);
  const gapAfterBeep = generateSilence(sampleRate, 100);
  const tailSilence = generateSilence(sampleRate, 200);

  const final = Buffer.concat([beep, gapAfterBeep, pcm, tailSilence]);
  return pcmToWav(final, sampleRate);
}

/**
 * Extract raw PCM data from a WAV buffer (skip header).
 */
export function extractPcmFromWav(wav: Buffer): Buffer {
  // Find "data" chunk
  for (let i = 12; i < wav.length - 8; i++) {
    if (wav.toString("ascii", i, i + 4) === "data") {
      const dataSize = wav.readUInt32LE(i + 4);
      return wav.subarray(i + 8, i + 8 + dataSize);
    }
  }
  // Fallback: assume standard 44-byte header
  return wav.subarray(44);
}

/**
 * Get WAV duration in seconds.
 */
export function getWavDuration(wav: Buffer): number {
  try {
    const sampleRate = wav.readUInt32LE(24);
    const pcm = extractPcmFromWav(wav);
    const numSamples = pcm.length / 2; // 16-bit = 2 bytes per sample
    return numSamples / sampleRate;
  } catch {
    return 3.0;
  }
}

/**
 * Simple resample from one rate to another (linear interpolation).
 * Input/output are 16-bit mono PCM buffers.
 */
export function resample(pcm: Buffer, fromRate: number, toRate: number): Buffer {
  if (fromRate === toRate) return pcm;

  const numInputSamples = pcm.length / 2;
  const ratio = fromRate / toRate;
  const numOutputSamples = Math.floor(numInputSamples / ratio);
  const output = Buffer.alloc(numOutputSamples * 2);

  for (let i = 0; i < numOutputSamples; i++) {
    const srcPos = i * ratio;
    const srcIndex = Math.floor(srcPos);
    const frac = srcPos - srcIndex;

    const s0 = srcIndex < numInputSamples ? pcm.readInt16LE(srcIndex * 2) : 0;
    const s1 =
      srcIndex + 1 < numInputSamples ? pcm.readInt16LE((srcIndex + 1) * 2) : s0;

    const interpolated = Math.round(s0 + frac * (s1 - s0));
    output.writeInt16LE(Math.max(-32768, Math.min(32767, interpolated)), i * 2);
  }

  return output;
}
