import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import fastifyRateLimit from "@fastify/rate-limit";
import { existsSync, readFileSync } from "fs";
import { join } from "path";
import type { BridgeConfig } from "./config.js";
import type { DeviceManager } from "./devices.js";
import { synthesizeSpeech, getWavUrl } from "./tts.js";
import { getWavDuration } from "./audio.js";
import { sanitizeForDisplay } from "./utils.js";

export async function createHttpServer(
  config: BridgeConfig,
  devices: DeviceManager
) {
  const app = Fastify({ logger: false });

  // Rate limiting
  await app.register(fastifyRateLimit, {
    max: 30,
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

  // GET /health
  app.get("/health", async (request, reply) => {
    if (!checkApiKey(request)) return reply.code(401).send("Unauthorized");

    const deviceIp = request.ip;
    const device = devices.getDevice(deviceIp);
    const resp: any = {
      status: "ok",
      device: deviceIp,
      processing: device.isProcessing,
      poll_status: device.pollStatus,
      uptime: (Date.now() - device.lastActivity) / 1000,
      connected_devices: devices.deviceCount,
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

    const ip = request.ip;
    const device = devices.getDevice(ip);

    if (device.pollStatus === "idle") {
      return { status: "idle" };
    } else if (device.pollStatus === "processing") {
      const resp: any = { status: "processing", stage: device.pollStage };
      if (device.pollTranscript) resp.transcript = device.pollTranscript;
      return resp;
    } else if (device.pollStatus === "ready") {
      const result = {
        status: "ready",
        wav_url: device.pollWavUrl,
        text: device.pollText,
        transcript: device.pollTranscript,
        duration: Math.round(device.pollWavDuration * 10) / 10,
      };
      device.resetPollState();
      console.log(`[http] Poll response served to ${ip}`);
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

  await app.listen({ port: config.httpPort, host: config.httpHost });
  console.log(`[http] Server listening on ${config.httpHost}:${config.httpPort}`);

  return app;
}
