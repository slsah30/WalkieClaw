import { randomBytes } from "crypto";
import { createSocket } from "dgram";
import { createServer } from "net";
import { networkInterfaces } from "os";
import { homedir, tmpdir } from "os";
import { join } from "path";
import { mkdirSync, existsSync, readFileSync, writeFileSync } from "fs";

export interface BridgeConfig {
  udpHost: string;
  udpPort: number;
  httpHost: string;
  httpPort: number;
  openclawUrl: string;
  openclawToken: string;
  openclawMode: string;
  openclawAgentId: string;
  whisperModel: string;
  whisperLanguage: string;
  ttsVoice: string;
  sampleRate: number;
  outputSampleRate: number;
  audioDir: string;
  apiKey: string;
  httpAdvertiseHost: string;
}

const CONFIG_DIR = join(homedir(), ".walkieclaw");
const CONFIG_FILE = join(CONFIG_DIR, "config.json");

export function getLocalIP(): string {
  const nets = networkInterfaces();
  const candidates: { ip: string; name: string }[] = [];

  for (const name of Object.keys(nets)) {
    for (const net of nets[name] ?? []) {
      if (net.family === "IPv4" && !net.internal) {
        candidates.push({ ip: net.address, name: name.toLowerCase() });
      }
    }
  }

  // Skip virtual adapters (VirtualBox, VMware, Docker, WSL)
  const real = candidates.filter(
    (c) => !c.name.includes("virtualbox") && !c.name.includes("vmware") &&
           !c.name.includes("vethernet") && !c.name.includes("docker") &&
           !c.ip.startsWith("192.168.56.") && !c.ip.startsWith("172.17.")
  );

  // Prefer WiFi/Ethernet LAN IPs over VPN/Tailscale (100.x.x.x)
  const lan = real.find(
    (c) => c.ip.startsWith("192.168.") || c.ip.startsWith("10.") || c.ip.startsWith("172.")
  );
  return lan?.ip ?? real[0]?.ip ?? candidates[0]?.ip ?? "127.0.0.1";
}

export function generateApiKey(): string {
  return randomBytes(16).toString("hex");
}

export function ensureConfigDir(): void {
  if (!existsSync(CONFIG_DIR)) {
    mkdirSync(CONFIG_DIR, { recursive: true });
  }
}

export function loadSavedConfig(): Partial<BridgeConfig> {
  if (!existsSync(CONFIG_FILE)) return {};
  try {
    return JSON.parse(readFileSync(CONFIG_FILE, "utf-8"));
  } catch {
    return {};
  }
}

export function saveConfig(config: Partial<BridgeConfig>): void {
  ensureConfigDir();
  writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
}

export function isFirstRun(): boolean {
  return !existsSync(CONFIG_FILE);
}

/**
 * Check if a TCP port is available.
 */
function isTcpPortFree(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = createServer();
    srv.once("error", () => resolve(false));
    srv.once("listening", () => {
      srv.close(() => resolve(true));
    });
    srv.listen(port, host);
  });
}

/**
 * Check if a UDP port is available.
 */
function isUdpPortFree(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = createSocket("udp4");
    sock.once("error", () => resolve(false));
    sock.once("listening", () => {
      sock.close(() => resolve(true));
    });
    sock.bind(port, host);
  });
}

/**
 * Find a free TCP port starting from the preferred one.
 */
export async function findFreeTcpPort(preferred: number, host: string): Promise<number> {
  for (let p = preferred; p < preferred + 10; p++) {
    if (await isTcpPortFree(p, host)) return p;
  }
  return 0;
}

/**
 * Find a free UDP port starting from the preferred one.
 */
export async function findFreeUdpPort(preferred: number, host: string): Promise<number> {
  for (let p = preferred; p < preferred + 10; p++) {
    if (await isUdpPortFree(p, host)) return p;
  }
  return 0;
}

export function buildConfig(overrides: Partial<BridgeConfig> = {}): BridgeConfig {
  const saved = loadSavedConfig();
  const env = process.env;

  const audioDir = join(tmpdir(), "walkieclaw-audio");
  if (!existsSync(audioDir)) {
    mkdirSync(audioDir, { recursive: true });
  }

  return {
    udpHost: overrides.udpHost ?? saved.udpHost ?? env.UDP_HOST ?? "0.0.0.0",
    udpPort: overrides.udpPort ?? saved.udpPort ?? parseInt(env.UDP_PORT ?? "12345"),
    httpHost: overrides.httpHost ?? saved.httpHost ?? env.HTTP_HOST ?? "0.0.0.0",
    httpPort: overrides.httpPort ?? saved.httpPort ?? parseInt(env.HTTP_PORT ?? "8080"),
    openclawUrl: overrides.openclawUrl ?? saved.openclawUrl ?? env.OPENCLAW_URL ?? "http://127.0.0.1:18789",
    openclawToken: overrides.openclawToken ?? saved.openclawToken ?? env.OPENCLAW_TOKEN ?? "",
    openclawMode: overrides.openclawMode ?? saved.openclawMode ?? env.OPENCLAW_MODE ?? "chat",
    openclawAgentId: overrides.openclawAgentId ?? saved.openclawAgentId ?? env.OPENCLAW_AGENT_ID ?? "main",
    whisperModel: overrides.whisperModel ?? saved.whisperModel ?? env.WHISPER_MODEL ?? "base",
    whisperLanguage: overrides.whisperLanguage ?? saved.whisperLanguage ?? env.WHISPER_LANGUAGE ?? "en",
    ttsVoice: overrides.ttsVoice ?? saved.ttsVoice ?? env.EDGE_TTS_VOICE ?? "en-GB-RyanNeural",
    sampleRate: 16000,
    outputSampleRate: 16000,
    audioDir,
    apiKey: overrides.apiKey ?? saved.apiKey ?? env.BRIDGE_API_KEY ?? "",
    httpAdvertiseHost: overrides.httpAdvertiseHost ?? saved.httpAdvertiseHost ?? env.HTTP_ADVERTISE_HOST ?? "",
  };
}
