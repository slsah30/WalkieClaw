#!/usr/bin/env node

import { readFileSync, readdirSync, statSync, unlinkSync, existsSync } from "fs";
import { join, dirname } from "path";
import { homedir } from "os";
import { fileURLToPath } from "url";
import {
  buildConfig,
  isFirstRun,
  saveConfig,
  generateApiKey,
  getLocalIP,
  ensureConfigDir,
  findFreeTcpPort,
  findFreeUdpPort,
  type BridgeConfig,
} from "./config.js";
import { DeviceManager } from "./devices.js";
import { initWhisper, transcribe } from "./whisper.js";
import { synthesizeSpeech, getWavUrl } from "./tts.js";
import { sendToOpenclaw } from "./openclaw.js";
import { i2sTo16bitPcm, getWavDuration } from "./audio.js";
import { sanitizeForDisplay, stripMarkdown } from "./utils.js";
import { createUdpListener } from "./udp.js";
import { createHttpServer } from "./server.js";

// ---------------------------------------------------------------------------
// Parse CLI args
// ---------------------------------------------------------------------------
function parseArgs(): Partial<BridgeConfig> {
  const args = process.argv.slice(2);
  const overrides: Partial<BridgeConfig> = {};

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case "--model":
        overrides.whisperModel = args[++i];
        break;
      case "--port":
        overrides.httpPort = parseInt(args[++i]);
        break;
      case "--udp-port":
        overrides.udpPort = parseInt(args[++i]);
        break;
      case "--openclaw-url":
        overrides.openclawUrl = args[++i];
        break;
      case "--voice":
        overrides.ttsVoice = args[++i];
        break;
      case "--api-key":
        overrides.apiKey = args[++i];
        break;
      case "--advertise-host":
        overrides.httpAdvertiseHost = args[++i];
        break;
      case "config":
        printConfig();
        process.exit(0);
      case "reset":
        resetConfig();
        process.exit(0);
      case "--help":
      case "-h":
        printHelp();
        process.exit(0);
      default:
        if (args[i].startsWith("-")) {
          console.error(`Unknown option: ${args[i]}`);
          process.exit(1);
        }
    }
  }
  return overrides;
}

function printHelp(): void {
  console.log(`
  walkieclaw-bridge - Voice bridge for WalkieClaw devices

  Usage:
    walkieclaw-bridge                Start the bridge
    walkieclaw-bridge config         Show current configuration
    walkieclaw-bridge reset          Delete config and start fresh

  Options:
    --model <name>          Whisper model (default: base)
    --port <port>           HTTP port (default: 8080)
    --udp-port <port>       UDP port (default: 12345)
    --openclaw-url <url>    OpenClaw URL (default: http://127.0.0.1:18789)
    --voice <voice>         Edge TTS voice (default: en-GB-RyanNeural)
    --api-key <key>         Set API key (auto-generated if not set)
    --advertise-host <ip>   IP to advertise in WAV URLs
    -h, --help              Show this help
`);
}

function printConfig(): void {
  const config = buildConfig();
  console.log("\n  WalkieClaw Bridge Configuration:\n");
  console.log(`    Bridge IP:      ${getLocalIP()}`);
  console.log(`    HTTP Port:      ${config.httpPort}`);
  console.log(`    UDP Port:       ${config.udpPort}`);
  console.log(`    API Key:        ${config.apiKey || "(none)"}`);
  console.log(`    OpenClaw:       ${config.openclawUrl}`);
  console.log(`    Whisper Model:  ${config.whisperModel}`);
  console.log(`    TTS Voice:      ${config.ttsVoice}`);
  console.log(`    Audio Dir:      ${config.audioDir}`);
  console.log();
}

function resetConfig(): void {
  const configFile = join(homedir(), ".walkieclaw", "config.json");
  if (existsSync(configFile)) {
    unlinkSync(configFile);
    console.log("  Configuration reset. Run again to set up fresh.");
  } else {
    console.log("  No configuration found.");
  }
}

// ---------------------------------------------------------------------------
// Banner
// ---------------------------------------------------------------------------
function printBanner(config: BridgeConfig): void {
  const localIp = getLocalIP();
  console.log();
  console.log("  WalkieClaw Bridge v0.1.0");
  console.log();
  console.log("  ┌─────────────────────────────────────────────────┐");
  console.log("  │                                                 │");
  console.log("  │  On your device, visit http://<device_ip>/      │");
  console.log("  │                                                 │");
  console.log(`  │  Bridge Host:  ${localIp.padEnd(33)}│`);
  console.log(`  │  API Key:      ${config.apiKey.padEnd(33)}│`);
  console.log("  │                                                 │");
  console.log("  └─────────────────────────────────────────────────┘");
  console.log();
  console.log(`  UDP: :${config.udpPort}  HTTP: :${config.httpPort}  Model: ${config.whisperModel}`);
  console.log(`  OpenClaw: ${config.openclawUrl}`);
  console.log(`  Voice: ${config.ttsVoice}`);
  console.log();
}

// ---------------------------------------------------------------------------
// Audio processing pipeline
// ---------------------------------------------------------------------------
async function processAudio(
  deviceIp: string,
  devices: DeviceManager,
  config: BridgeConfig
): Promise<void> {
  const device = devices.getDeviceIfExists(deviceIp);
  if (!device || device.audioBufferSize === 0 || device.isProcessing) return;

  device.isProcessing = true;
  device.pollStatus = "processing";
  device.pollStage = "transcribing";

  const rawAudio = device.drainAudioBuffer();

  // Convert I2S 32-bit to 16-bit PCM
  const pcmData = i2sTo16bitPcm(rawAudio);
  console.log(`[pipeline] Processing ${pcmData.length} bytes from ${deviceIp}`);

  try {
    // 1. Transcribe
    device.pollStage = "transcribing";
    const text = await transcribe(pcmData, config.sampleRate, config.whisperLanguage);

    if (!text.trim()) {
      console.log(`[pipeline] No speech from ${deviceIp}, skipping`);
      device.isProcessing = false;
      device.resetPollState();
      return;
    }

    device.pollTranscript = text;
    device.pollStage = "thinking";

    // 2. Send to OpenClaw
    const rawResponse = await sendToOpenclaw(text, deviceIp, config);

    // 3. Strip markdown and generate TTS
    const response = stripMarkdown(rawResponse);
    device.pollStage = "speaking";
    const wavPath = await synthesizeSpeech(response, config);

    // 4. Store result for polling
    const wavUrl = getWavUrl(wavPath, config);
    const wavBuf = readFileSync(wavPath);
    const duration = getWavDuration(wavBuf);

    device.pollWavUrl = wavUrl;
    device.pollText = sanitizeForDisplay(response.slice(0, 200));
    device.pollWavDuration = duration;
    device.pollStatus = "ready";
    device.pollReadyTime = Date.now();

    console.log(`[pipeline] Response ready for ${deviceIp}: ${wavUrl} (${duration.toFixed(1)}s)`);
  } catch (err: any) {
    console.error(`[pipeline] Error for ${deviceIp}: ${err.message}`);
    device.resetPollState();
  } finally {
    device.isProcessing = false;
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main(): Promise<void> {
  const overrides = parseArgs();

  // First run setup
  if (isFirstRun()) {
    console.log("\n  First run! Setting up WalkieClaw Bridge...\n");
    ensureConfigDir();

    if (!overrides.apiKey) {
      overrides.apiKey = generateApiKey();
      console.log(`  Generated API key: ${overrides.apiKey}`);
    }

    saveConfig(overrides);
  }

  const config = buildConfig(overrides);

  // Ensure API key exists
  if (!config.apiKey) {
    config.apiKey = generateApiKey();
    saveConfig({ apiKey: config.apiKey });
  }

  // Find free ports (auto-increment if default is taken)
  console.log(`[init] Checking ports...`);
  const httpPort = await findFreeTcpPort(config.httpPort, config.httpHost);
  if (httpPort !== config.httpPort) {
    console.log(`[init] HTTP port ${config.httpPort} in use, using ${httpPort}`);
    config.httpPort = httpPort;
  } else {
    console.log(`[init] HTTP port ${config.httpPort} is free`);
  }
  const udpPort = await findFreeUdpPort(config.udpPort, config.udpHost);
  if (udpPort !== config.udpPort) {
    console.log(`[init] UDP port ${config.udpPort} in use, using ${udpPort}`);
    config.udpPort = udpPort;
  } else {
    console.log(`[init] UDP port ${config.udpPort} is free`);
  }

  printBanner(config);

  // Initialize Whisper
  console.log("[init] Loading Whisper model (this may download on first run)...");
  await initWhisper(config.whisperModel);

  // Create device manager
  const devices = new DeviceManager();

  // Start HTTP server
  await createHttpServer(config, devices);

  // Start UDP listener
  createUdpListener(config, devices, (deviceIp) => {
    processAudio(deviceIp, devices, config);
  });

  // Background cleanup every 10s
  setInterval(() => devices.cleanupStale(), 10_000);

  // Audio file cleanup every 5 minutes
  setInterval(() => {
    // Uses top-level ESM imports
    const cutoff = Date.now() - 600_000;
    try {
      for (const f of readdirSync(config.audioDir)) {
        const fpath = join(config.audioDir, f);
        try {
          if (statSync(fpath).mtimeMs < cutoff) unlinkSync(fpath);
        } catch {}
      }
    } catch {}
  }, 300_000);

  console.log("[init] Bridge is running! Press Ctrl+C to stop.\n");

  // Graceful shutdown
  process.on("SIGINT", () => {
    console.log("\n[shutdown] Shutting down...");
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    console.log("\n[shutdown] Shutting down...");
    process.exit(0);
  });
}

main().catch((err) => {
  console.error(`Fatal error: ${err.message}`);
  process.exit(1);
});
