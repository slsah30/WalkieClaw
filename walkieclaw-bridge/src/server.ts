import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import fastifyRateLimit from "@fastify/rate-limit";
import { existsSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import type { BridgeConfig } from "./config.js";
import type { DeviceManager } from "./devices.js";
import type { TimerManager } from "./timers.js";
import { synthesizeSpeech, getWavUrl } from "./tts.js";
import { getWavDuration } from "./audio.js";
import { sanitizeForDisplay, normalizeIp } from "./utils.js";

export async function createHttpServer(
  config: BridgeConfig,
  devices: DeviceManager,
  timers?: TimerManager
) {
  const app = Fastify({ logger: false });

  // Rate limiting
  await app.register(fastifyRateLimit, {
    max: 200,
    timeWindow: 60_000,
  });

  // Serve audio files
  await app.register(fastifyStatic, {
    root: config.audioDir,
    prefix: "/audio/",
    decorateReply: false,
    setHeaders: (res) => {
      res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
      res.setHeader("Pragma", "no-cache");
    },
  });

  // Auth helper
  function checkApiKey(request: any): boolean {
    if (!config.apiKey) return true;
    return request.headers["x-api-key"] === config.apiKey;
  }

  // Redirect root to dashboard
  app.get("/", async (request, reply) => reply.redirect("/dashboard"));

  // GET /health
  app.get("/health", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");

    const deviceIp = normalizeIp(request.ip);
    const device = devices.getDevice(deviceIp);
    const resp: any = {
      status: "ok",
      device: deviceIp,
      processing: device.isProcessing,
      poll_status: device.pollStatus,
      uptime: (Date.now() - device.lastActivity) / 1000,
      connected_devices: devices.deviceCount,
      walkie_mode: device.walkieTalkieMode,
      paired_with: device.pairedDeviceIp || "",
    };

    // Deliver pending WiFi command
    if (device.pendingWifiSsid) {
      resp.wifi_ssid = device.pendingWifiSsid;
      resp.wifi_password = device.pendingWifiPassword;
      console.log(`[http] Delivering WiFi command to ${deviceIp}: SSID=${device.pendingWifiSsid}`);
      device.pendingWifiSsid = "";
      device.pendingWifiPassword = "";
    }

    // Deliver pending notification
    if (device.pendingNotifications.length > 0 && !device.isProcessing && device.pollStatus === "idle") {
      const notif = device.pendingNotifications.shift()!;
      resp.notify_text = notif.text;
      resp.notify_wav_url = notif.wavUrl;
      resp.notify_duration = notif.duration;
      console.log(`[http] Delivering notification to ${deviceIp}: ${notif.text.slice(0, 60)}`);
    }

    return resp;
  });

  // GET /api/response
  app.get("/api/response", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");

    const ip = normalizeIp(request.ip);
    const device = devices.getDevice(ip);

    if (device.pollStatus === "idle") {
      return { status: "idle" };
    } else if (device.pollStatus === "processing") {
      const resp: any = { status: "processing", stage: device.pollStage };
      if (device.pollTranscript) resp.transcript = device.pollTranscript;
      return resp;
    } else if (device.pollStatus === "ready") {
      const hasMore = device.pendingResponseChunks.length > 0;
      const result = {
        status: "ready",
        wav_url: device.pollWavUrl,
        text: device.pollText,
        transcript: device.pollTranscript,
        duration: Math.round(device.pollWavDuration * 10) / 10,
        has_more: hasMore,
      };

      // If there are more chunks, queue up the next one immediately
      if (hasMore) {
        const next = device.pendingResponseChunks.shift()!;
        device.pollWavUrl = next.wavUrl;
        device.pollText = next.text;
        device.pollWavDuration = next.duration;
        device.pollReadyTime = Date.now();
        // Keep pollStatus as "ready" so next poll gets the next chunk
      } else {
        device.resetPollState();
      }
      console.log(`[http] Poll response served to ${ip}${hasMore ? ` (${device.pendingResponseChunks.length + 1} more)` : ""}`);
      return result;
    }
    return { status: "idle" };
  });

  // GET /api/devices
  app.get("/api/devices", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");

    const deviceList = devices.getAllDevices().map((d) => ({
      ip: d.ip,
      poll_status: d.pollStatus,
      is_processing: d.isProcessing,
      last_activity: Math.round((Date.now() - d.lastActivity) / 1000 * 10) / 10,
      pending_notifications: d.pendingNotifications.length,
      conversation_length: d.conversationHistory.length,
      last_transcript: d.pollTranscript || undefined,
      last_response: d.pollText || undefined,
    }));
    return { devices: deviceList, count: deviceList.length };
  });

  // POST /api/notify
  app.post("/api/notify", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");

    const body = request.body as any;
    const text = (body?.text ?? "").trim();
    if (!text) return reply.code(400).send({ error: "text is required" });

    const targetIp = (body?.device ?? "").trim();
    const displayText = sanitizeForDisplay(text.slice(0, 200));

    console.log(`[http] Generating notification TTS: ${text.slice(0, 60)}...`);
    const wavPath = await synthesizeSpeech(text, config);
    const wavUrl = getWavUrl(wavPath, config);
    const wavBuf = readFileSync(wavPath);
    const duration = Math.round(getWavDuration(wavBuf) * 10) / 10;

    const notif = { text: displayText, wavUrl, duration };

    if (targetIp) {
      const device = devices.getDevice(targetIp);
      device.pendingNotifications.push(notif);
      return { status: "queued", device: targetIp, text: displayText, pending: device.pendingNotifications.length };
    } else {
      let count = 0;
      for (const dev of devices.getAllDevices()) {
        dev.pendingNotifications.push({ ...notif });
        count++;
      }
      return { status: "broadcast", text: displayText, devices: count };
    }
  });

  // POST /api/connect_wifi
  app.post("/api/connect_wifi", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");

    const body = request.body as any;
    const ssid = (body?.ssid ?? "").trim();
    const password = (body?.password ?? "").trim();
    if (!ssid) return reply.code(400).send({ error: "ssid is required" });

    const targetIp = (body?.device ?? "").trim();

    if (targetIp) {
      const device = devices.getDevice(targetIp);
      device.pendingWifiSsid = ssid;
      device.pendingWifiPassword = password;
      console.log(`[http] Queued WiFi command for ${targetIp}: SSID=${ssid}`);
      return { status: "queued", device: targetIp, ssid };
    } else {
      let count = 0;
      for (const dev of devices.getAllDevices()) {
        dev.pendingWifiSsid = ssid;
        dev.pendingWifiPassword = password;
        count++;
      }
      return { status: "broadcast", ssid, devices: count };
    }
  });

  // GET /dashboard
  app.get("/dashboard", async (request, reply) => {
    const __filename = fileURLToPath(import.meta.url);
    const __dirname = dirname(__filename);
    const htmlPaths = [
      join(__dirname, "..", "public", "dashboard.html"),
      join(__dirname, "..", "..", "public", "dashboard.html"),
    ];
    const htmlPath = htmlPaths.find(p => existsSync(p));
    if (!htmlPath) {
      return reply.code(404).send("Dashboard not found");
    }
    let html = readFileSync(htmlPath, "utf-8");
    // Auto-inject API key so the dashboard works without manual auth
    if (config.apiKey) {
      const safeKey = config.apiKey.replace(/[^a-zA-Z0-9]/g, "");
      html = html.replace(
        "let API_KEY = localStorage.getItem('wc_api_key') || '';",
        `let API_KEY = localStorage.getItem('wc_api_key') || '${safeKey}';`
      );
    }
    return reply.type("text/html").send(html);
  });

  // GET /api/history/:ip
  app.get("/api/history/:ip", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    const ip = (request.params as any).ip;
    const device = devices.getDeviceIfExists(ip);
    if (!device) return reply.code(404).send({ error: "Device not found" });
    return { ip, history: device.conversationHistory, count: device.conversationHistory.length };
  });

  // DELETE /api/history/:ip
  app.delete("/api/history/:ip", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    const ip = (request.params as any).ip;
    const device = devices.getDeviceIfExists(ip);
    if (!device) return reply.code(404).send({ error: "Device not found" });
    device.clearHistory();
    return { status: "cleared", ip };
  });

  // POST /api/pair
  app.post("/api/pair", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    const body = request.body as any;
    const deviceA = (body?.device_a ?? "").trim();
    const deviceB = (body?.device_b ?? "").trim();
    if (!deviceA || !deviceB) return reply.code(400).send({ error: "device_a and device_b are required" });
    if (deviceA === deviceB) return reply.code(400).send({ error: "Cannot pair a device with itself" });

    const a = devices.getDevice(deviceA);
    const b = devices.getDevice(deviceB);
    a.pairedDeviceIp = deviceB;
    a.walkieTalkieMode = true;
    b.pairedDeviceIp = deviceA;
    b.walkieTalkieMode = true;

    console.log(`[http] Paired ${deviceA} <-> ${deviceB} (walkie-talkie mode)`);
    return { status: "paired", device_a: deviceA, device_b: deviceB };
  });

  // POST /api/unpair
  app.post("/api/unpair", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    const body = request.body as any;
    const deviceIp = (body?.device ?? "").trim();
    if (!deviceIp) return reply.code(400).send({ error: "device is required" });

    const dev = devices.getDeviceIfExists(deviceIp);
    if (!dev) return reply.code(404).send({ error: "Device not found" });

    const partnerId = dev.pairedDeviceIp;
    dev.pairedDeviceIp = null;
    dev.walkieTalkieMode = false;

    if (partnerId) {
      const partner = devices.getDeviceIfExists(partnerId);
      if (partner) {
        partner.pairedDeviceIp = null;
        partner.walkieTalkieMode = false;
      }
    }

    console.log(`[http] Unpaired ${deviceIp}${partnerId ? ` from ${partnerId}` : ""}`);
    return { status: "unpaired", device: deviceIp, was_paired_with: partnerId || null };
  });

  // GET /api/pairs
  app.get("/api/pairs", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    const pairs: Array<{ device_a: string; device_b: string }> = [];
    const seen = new Set<string>();
    for (const dev of devices.getAllDevices()) {
      if (dev.walkieTalkieMode && dev.pairedDeviceIp && !seen.has(dev.ip)) {
        pairs.push({ device_a: dev.ip, device_b: dev.pairedDeviceIp });
        seen.add(dev.ip);
        seen.add(dev.pairedDeviceIp);
      }
    }
    return { pairs, count: pairs.length };
  });

  // POST /api/timer
  app.post("/api/timer", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    if (!timers) return reply.code(501).send({ error: "Timers not available" });

    const body = request.body as any;
    const text = (body?.text ?? "").trim();
    if (!text) return reply.code(400).send({ error: "text is required" });

    const delaySec = parseInt(body?.delay_seconds ?? "0");
    if (delaySec <= 0) return reply.code(400).send({ error: "delay_seconds must be > 0" });

    const deviceIp = (body?.device ?? "").trim();
    const timer = timers.schedule(text, delaySec * 1000, deviceIp);
    return {
      status: "scheduled",
      id: timer.id,
      text: text.slice(0, 100),
      fires_at: new Date(timer.fireAt).toISOString(),
      delay_seconds: delaySec,
    };
  });

  // GET /api/timers
  app.get("/api/timers", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    if (!timers) return { timers: [], count: 0 };

    const list = timers.list().map(t => ({
      id: t.id,
      text: t.text.slice(0, 100),
      fires_at: new Date(t.fireAt).toISOString(),
      seconds_remaining: Math.max(0, Math.round((t.fireAt - Date.now()) / 1000)),
      device: t.deviceIp || "all",
    }));
    return { timers: list, count: list.length };
  });

  // DELETE /api/timer/:id
  app.delete("/api/timer/:id", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    if (!timers) return reply.code(501).send({ error: "Timers not available" });

    const id = (request.params as any).id;
    const cancelled = timers.cancel(id);
    if (!cancelled) return reply.code(404).send({ error: "Timer not found" });
    return { status: "cancelled", id };
  });

  // POST /api/ota — trigger ESPHome OTA update to a device
  let otaInProgress = false;
  app.post("/api/ota", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    if (otaInProgress) return reply.code(409).send({ error: "OTA already in progress" });

    const body = request.body as any;
    const deviceIp = (body?.device ?? "").trim();
    if (!deviceIp) return reply.code(400).send({ error: "device IP is required" });

    const configDir = config.esphomeConfigDir;
    if (!configDir) {
      return reply.code(501).send({ error: "esphomeConfigDir not configured" });
    }

    const yamlPath = join(configDir, "walkieclaw.yaml");
    if (!existsSync(yamlPath)) {
      return reply.code(404).send({ error: `walkieclaw.yaml not found in ${configDir}` });
    }

    otaInProgress = true;
    console.log(`[ota] Starting OTA to ${deviceIp} from ${yamlPath}`);

    // Run ESPHome OTA in background
    const { spawn } = await import("child_process");
    const proc = spawn("esphome", ["run", yamlPath, "--device", deviceIp], {
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
      cwd: configDir,
    });

    const output: string[] = [];
    proc.stdout?.on("data", (d: Buffer) => {
      const line = d.toString().trim();
      if (line) { output.push(line); console.log(`[ota] ${line}`); }
    });
    proc.stderr?.on("data", (d: Buffer) => {
      const line = d.toString().trim();
      if (line) { output.push(line); console.log(`[ota] ${line}`); }
    });
    proc.on("close", (code) => {
      otaInProgress = false;
      console.log(`[ota] Finished with code ${code}`);
    });

    return { status: "started", device: deviceIp, message: "OTA update started. Check bridge logs for progress." };
  });

  // GET /api/ota/status
  app.get("/api/ota/status", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    return { in_progress: otaInProgress };
  });

  // GET /api/device-config/:ip — read bridge config from a device's ESPHome REST API
  app.get("/api/device-config/:ip", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    const ip = (request.params as any).ip;
    try {
      const [hostResp, keyResp] = await Promise.all([
        fetch(`http://${ip}/text/bridge_host`, { signal: AbortSignal.timeout(5000) }),
        fetch(`http://${ip}/text/bridge_api_key`, { signal: AbortSignal.timeout(5000) }),
      ]);
      const host = hostResp.ok ? (await hostResp.json() as any).state : "";
      const key = keyResp.ok ? (await keyResp.json() as any).state : "";
      return { ip, bridge_host: host, api_key: key };
    } catch (err: any) {
      return reply.code(502).send({ error: `Cannot reach device ${ip}: ${err.message}` });
    }
  });

  // POST /api/device-config/:ip — push bridge config to a device's ESPHome REST API
  app.post("/api/device-config/:ip", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");
    const ip = (request.params as any).ip;
    const body = request.body as any;
    const results: string[] = [];

    if (body?.bridge_host !== undefined) {
      try {
        await fetch(`http://${ip}/text/bridge_host/set?value=${encodeURIComponent(body.bridge_host)}`, { signal: AbortSignal.timeout(5000) });
        results.push(`bridge_host=${body.bridge_host}`);
      } catch (err: any) {
        results.push(`bridge_host FAILED: ${err.message}`);
      }
    }
    if (body?.api_key !== undefined) {
      try {
        await fetch(`http://${ip}/text/bridge_api_key/set?value=${encodeURIComponent(body.api_key)}`, { signal: AbortSignal.timeout(5000) });
        results.push(`api_key=${body.api_key.slice(0, 8)}...`);
      } catch (err: any) {
        results.push(`api_key FAILED: ${err.message}`);
      }
    }

    console.log(`[http] Device config pushed to ${ip}: ${results.join(", ")}`);
    return { status: "ok", ip, results };
  });

  await app.listen({ port: config.httpPort, host: config.httpHost });
  console.log(`[http] Server listening on ${config.httpHost}:${config.httpPort}`);

  return app;
}
