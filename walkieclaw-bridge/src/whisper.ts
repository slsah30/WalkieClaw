import { Whisper, manager } from "smart-whisper";
import { resample } from "./audio.js";

let whisperInstance: Whisper | null = null;

/**
 * Initialize Whisper model (auto-downloads on first run).
 */
export async function initWhisper(modelName: string): Promise<void> {
  // Download model if not already present
  if (!manager.check(modelName)) {
    console.log(`[whisper] Downloading model: ${modelName}...`);
    await manager.download(modelName);
  }

  const modelPath = manager.resolve(modelName);
  console.log(`[whisper] Loading model: ${modelName} from ${modelPath}...`);
  whisperInstance = new Whisper(modelPath, { gpu: false });
  await whisperInstance.load();
  console.log(`[whisper] Model loaded.`);
}

/**
 * Transcribe 16-bit PCM audio to text.
 * Input: 16-bit signed PCM buffer at the given sampleRate.
 * smart-whisper expects mono 16kHz Float32Array.
 */
export async function transcribe(
  pcmData: Buffer,
  sampleRate: number,
  language = "en"
): Promise<string> {
  if (!whisperInstance) {
    console.error("[whisper] Model not initialized!");
    return "";
  }

  try {
    // Resample to 16kHz if needed
    let pcm16k = sampleRate === 16000 ? pcmData : resample(pcmData, sampleRate, 16000);

    // Convert 16-bit signed PCM to Float32Array normalized to [-1, 1]
    const numSamples = pcm16k.length / 2;
    const floatPcm = new Float32Array(numSamples);
    for (let i = 0; i < numSamples; i++) {
      floatPcm[i] = pcm16k.readInt16LE(i * 2) / 32768.0;
    }

    const task = await whisperInstance.transcribe(floatPcm, {
      language,
      beam_size: 5,
      format: "simple" as const,
    });

    const results = await task.result;
    const text = results
      .map((seg) => seg.text.trim())
      .filter(Boolean)
      .join(" ");

    const duration = (pcmData.length / 2 / sampleRate).toFixed(1);
    console.log(`[whisper] STT result (${duration}s audio): ${text}`);
    return text;
  } catch (err: any) {
    console.error(`[whisper] Transcription error: ${err.message}`);
    return "";
  }
}
