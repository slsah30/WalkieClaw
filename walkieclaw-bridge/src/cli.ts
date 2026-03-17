#!/usr/bin/env node

import { readFileSync, writeFileSync, readdirSync, statSync, unlinkSync, existsSync } from "fs";
import { join, dirname } from "path";
import { homedir, platform } from "os";
import { fileURLToPath } from "url";
import { spawn, type ChildProcess } from "child_process";
import {
  buildConfig,
  isFirstRun,
  saveConfig,
  generateApiKey,
  getLocalIP,
  ensureConfigDir,
  findFreeTcpPort,
  findFreeUdpPort,
  LANGUAGE_VOICE_MAP,
  type BridgeConfig,
} from "./config.js";
import { DeviceManager } from "./devices.js";
import { initWhisper, transcribe } from "./whisper.js";
import { synthesizeSpeech, getWavUrl } from "./tts.js";
import { sendToOpenclaw, streamFromOpenclaw, initOpenclaw } from "./openclaw.js";
import { i2sTo16bitPcm, getWavDuration, pcmToWav } from "./audio.js";
import { sanitizeForDisplay, stripMarkdown } from "./utils.js";
import { createUdpListener } from "./udp.js";
import { createHttpServer } from "./server.js";
import { TimerManager } from "./timers.js";

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
      case "--max-turns":
        overrides.maxConversationTurns = parseInt(args[++i]);
        break;
      case "--language": {
        const lang = args[++i];
        overrides.whisperLanguage = lang;
        if (LANGUAGE_VOICE_MAP[lang] && !overrides.ttsVoice) {
          overrides.ttsVoice = LANGUAGE_VOICE_MAP[lang];
        }
        break;
      }
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
    --max-turns <n>         Conversation history turns to keep (default: 10)
    --language <code>       Language code (en, es, fr, de, ja, zh, etc.)
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

  // Walkie-talkie mode: skip AI, route audio directly to paired device
  if (device.walkieTalkieMode && device.pairedDeviceIp) {
    try {
      const wav = pcmToWav(pcmData, config.sampleRate);
      const filename = `walkie_${Date.now()}.wav`;
      const wavPath = join(config.audioDir, filename);
      writeFileSync(wavPath, wav);

      const wavUrl = getWavUrl(wavPath, config);
      const duration = getWavDuration(wav);
      const partner = devices.getDevice(device.pairedDeviceIp);
      partner.pendingNotifications.push({
        text: sanitizeForDisplay(`Voice from ${deviceIp}`),
        wavUrl,
        duration: Math.round(duration * 10) / 10,
      });

      console.log(`[walkie] Routed audio from ${deviceIp} to ${device.pairedDeviceIp} (${duration.toFixed(1)}s)`);
      device.resetPollState();
    } catch (err: any) {
      console.error(`[walkie] Error: ${err.message}`);
      device.resetPollState();
    } finally {
      device.isProcessing = false;
    }
    return;
  }

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

    // 2. Add to history and send to OpenClaw (streaming)
    device.addToHistory("user", text);
    device.pollStage = "thinking";
    device.pendingResponseChunks = [];

    let chunkIndex = 0;
    let fullResponse = "";

    fullResponse = await streamFromOpenclaw(
      device.conversationHistory,
      deviceIp,
      config,
      async (sentence) => {
        device.pollStage = "speaking";
        const cleaned = stripMarkdown(sentence);
        if (!cleaned) return;

        const wavPath = await synthesizeSpeech(cleaned, config);
        const wavUrl = getWavUrl(wavPath, config);
        const wavBuf = readFileSync(wavPath);
        const duration = getWavDuration(wavBuf);

        if (chunkIndex === 0) {
          // First chunk: set as the immediate response
          device.pollWavUrl = wavUrl;
          device.pollText = sanitizeForDisplay(cleaned.slice(0, 200));
          device.pollWavDuration = duration;
          device.pollStatus = "ready";
          device.pollReadyTime = Date.now();
          console.log(`[pipeline] First chunk ready for ${deviceIp}: ${wavUrl} (${duration.toFixed(1)}s)`);
        } else {
          // Subsequent chunks: queue for sequential delivery
          device.pendingResponseChunks.push({
            wavUrl,
            text: sanitizeForDisplay(cleaned.slice(0, 200)),
            duration,
          });
          console.log(`[pipeline] Chunk ${chunkIndex + 1} queued for ${deviceIp} (${duration.toFixed(1)}s)`);
        }
        chunkIndex++;
      }
    );

    // Save full response to history
    device.addToHistory("assistant", fullResponse);

    // If streaming produced no chunks (e.g., empty response), fall back
    if (chunkIndex === 0) {
      const fallback = stripMarkdown(fullResponse) || "I had nothing to say.";
      const wavPath = await synthesizeSpeech(fallback, config);
      const wavUrl = getWavUrl(wavPath, config);
      const wavBuf = readFileSync(wavPath);
      const duration = getWavDuration(wavBuf);
      device.pollWavUrl = wavUrl;
      device.pollText = sanitizeForDisplay(fallback.slice(0, 200));
      device.pollWavDuration = duration;
      device.pollStatus = "ready";
      device.pollReadyTime = Date.now();
    }

    console.log(`[pipeline] All chunks ready for ${deviceIp} (${chunkIndex} total)`);
  } catch (err: any) {
    console.error(`[pipeline] Error for ${deviceIp}: ${err.message}`);
    device.resetPollState();
  } finally {
    device.isProcessing = false;
  }
}

// ---------------------------------------------------------------------------
// GPU Whisper Server (auto-managed child process)
// ---------------------------------------------------------------------------
const WHISPER_PORT = parseInt(process.env.WHISPER_PORT ?? "8787");

async function startWhisperServer(config: BridgeConfig): Promise<ChildProcess | null> {
  // Find whisper-server.py relative to this package
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = dirname(__filename);
  const scriptPaths = [
    join(__dirname, "..", "whisper-server.py"),          // dev: dist/../whisper-server.py
    join(__dirname, "..", "..", "whisper-server.py"),     // installed: node_modules/walkieclaw-bridge/../../whisper-server.py
  ];

  const script = scriptPaths.find(p => existsSync(p));
  if (!script) {
    console.warn("[whisper] whisper-server.py not found — using external whisper server");
    return null;
  }

  // Check if whisper server is already running
  try {
    const resp = await fetch(`http://127.0.0.1:${WHISPER_PORT}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "audio/pcm" },
      body: new Uint8Array(3200),
    });
    if (resp.ok) {
      console.log("[whisper] GPU server already running on :" + WHISPER_PORT);
      return null;
    }
  } catch {
    // Not running — we'll start it
  }

  // Find python
  const pythonCandidates = platform() === "win32"
    ? ["python", "python3", join(homedir(), "AppData", "Local", "Programs", "Miniconda3", "python.exe")]
    : ["python3", "python"];

  let pythonPath = "python";
  for (const p of pythonCandidates) {
    try {
      const test = spawn(p, ["--version"], { stdio: "pipe", windowsHide: true });
      await new Promise<void>((resolve) => {
        test.on("close", (code) => { if (code === 0) pythonPath = p; resolve(); });
        test.on("error", () => resolve());
      });
      if (pythonPath === p) break;
    } catch {}
  }

  console.log(`[whisper] Starting GPU server: ${pythonPath} ${script}`);
  const proc = spawn(pythonPath, [
    script,
    "--model", config.whisperModel,
    "--port", String(WHISPER_PORT),
    "--device", "cuda",
  ], {
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  proc.stdout?.on("data", (d: Buffer) => {
    const line = d.toString().trim();
    if (line) console.log(`[whisper-gpu] ${line}`);
  });
  proc.stderr?.on("data", (d: Buffer) => {
    const line = d.toString().trim();
    if (line) console.log(`[whisper-gpu] ${line}`);
  });
  proc.on("exit", (code) => {
    console.error(`[whisper-gpu] Process exited (code ${code}), restarting in 3s...`);
    setTimeout(() => {
      startWhisperServer(config).catch(() => {});
    }, 3000);
  });

  // Wait for server to become ready
  console.log("[whisper] Waiting for GPU model to load...");
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 1000));
    try {
      const resp = await fetch(`http://127.0.0.1:${WHISPER_PORT}/transcribe`, {
        method: "POST",
        headers: { "Content-Type": "audio/pcm" },
        body: new Uint8Array(3200),
      });
      if (resp.ok) {
        console.log("[whisper] GPU server ready.");
        return proc;
      }
    } catch {}
  }

  console.error("[whisper] GPU server failed to start within 60s");
  return proc;
}

// ---------------------------------------------------------------------------
// OpenClaw Gateway (auto-managed child process)
// ---------------------------------------------------------------------------
async function startGateway(config: BridgeConfig): Promise<ChildProcess | null> {
  const gatewayUrl = config.openclawUrl.replace(/\/+$/, "");
  const port = new URL(gatewayUrl).port || "18789";

  // Check if gateway is already running
  try {
    const resp = await fetch(`${gatewayUrl}/health`);
    if (resp.ok) {
      console.log(`[gateway] OpenClaw gateway already running on :${port}`);
      return null;
    }
  } catch {
    // Not running — start it
  }

  // Find openclaw.mjs
  const npmGlobal = join(homedir(), "AppData", "Roaming", "npm");
  const candidates = [
    join(npmGlobal, "node_modules", "openclaw", "openclaw.mjs"),
    "/usr/local/lib/node_modules/openclaw/openclaw.mjs",
    "/usr/lib/node_modules/openclaw/openclaw.mjs",
  ];
  const script = candidates.find(p => existsSync(p));

  if (!script) {
    console.warn("[gateway] OpenClaw not found. Install with: npm install -g openclaw");
    return null;
  }

  console.log(`[gateway] Starting OpenClaw gateway on :${port}...`);
  const proc = spawn(process.execPath, [script, "gateway", "--port", port], {
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  proc.stdout?.on("data", (d: Buffer) => {
    const line = d.toString().trim();
    if (line) console.log(`[gateway] ${line}`);
  });
  proc.stderr?.on("data", (d: Buffer) => {
    // Gateway logs go to stderr — filter noise
    const line = d.toString().trim();
    if (line && !line.includes("ExperimentalWarning")) {
      console.log(`[gateway] ${line}`);
    }
  });
  proc.on("exit", (code) => {
    console.error(`[gateway] Process exited (code ${code}), restarting in 3s...`);
    setTimeout(() => {
      startGateway(config).catch(() => {});
    }, 3000);
  });

  // Wait for it to become ready
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 1000));
    try {
      const resp = await fetch(`${gatewayUrl}/health`);
      if (resp.ok) {
        console.log("[gateway] OpenClaw gateway ready.");
        return proc;
      }
    } catch {}
  }

  console.warn("[gateway] Gateway did not become ready within 30s");
  return proc;
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

  // Start OpenClaw gateway (auto-managed, hidden)
  const gatewayProc = await startGateway(config);

  // Start GPU whisper server (auto-managed, hidden)
  const whisperProc = await startWhisperServer(config);

  // Initialize Whisper client
  await initWhisper(config.whisperModel);

  // Initialize OpenClaw connection
  await initOpenclaw(config);

  // Create device manager
  const devices = new DeviceManager(config.maxConversationTurns * 2);

  // Create timer manager
  const timers = new TimerManager(config, devices);
  timers.start();

  // Start HTTP server
  await createHttpServer(config, devices, timers);

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

  // Graceful shutdown — kill child processes
  const shutdown = () => {
    console.log("\n[shutdown] Shutting down...");
    timers.stop();
    if (whisperProc && !whisperProc.killed) {
      whisperProc.kill();
      console.log("[shutdown] Whisper server stopped.");
    }
    if (gatewayProc && !gatewayProc.killed) {
      gatewayProc.kill();
      console.log("[shutdown] OpenClaw gateway stopped.");
    }
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main().catch((err) => {
  console.error(`Fatal error: ${err.message}`);
  process.exit(1);
});
