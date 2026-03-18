import { createSocket, type Socket } from "dgram";
import type { BridgeConfig } from "./config.js";
import type { DeviceManager } from "./devices.js";
import { normalizeIp } from "./utils.js";

export type AudioReadyCallback = (deviceIp: string) => void;

export function createUdpListener(
  config: BridgeConfig,
  devices: DeviceManager,
  onAudioReady: AudioReadyCallback
): Socket {
  const socket = createSocket("udp4");

  socket.on("message", (data, rinfo) => {
    const senderIp = normalizeIp(rinfo.address);

    // Handle ESP_IP marker
    if (data.subarray(0, 7).toString() === "ESP_IP:") {
      const espIp = data.toString().split(":")[1];
      console.log(`[udp] ESP32 detected at ${espIp}`);
      return;
    }

    // Handle keyed START marker: START:<first_8_chars_of_api_key>
    if (data.subarray(0, 5).toString() === "START") {
      if (config.apiKey) {
        const expected = `START:${config.apiKey.slice(0, 8)}`;
        const received = data.toString();
        if (received === expected) {
          devices.authenticateUdp(senderIp);
          console.log(`[udp] Authenticated START from ${senderIp}`);
        } else if (received === "START") {
          console.warn(`[udp] Rejected unauthenticated START from ${senderIp}`);
          return;
        } else {
          console.warn(`[udp] Rejected bad START key from ${senderIp}`);
          return;
        }
      } else {
        // No key configured, accept plain START
        devices.authenticateUdp(senderIp);
      }
      devices.getDevice(senderIp);
      return;
    }

    // Handle STOP marker
    if (data.toString() === "STOP") {
      return;
    }

    // Drop PCM from unauthenticated IPs
    if (config.apiKey && !devices.isUdpAuthenticated(senderIp)) {
      return;
    }

    // Get per-device state
    const device = devices.getDevice(senderIp);

    // Block new audio if this device is busy
    if (device.isProcessing || device.pollStatus === "ready") {
      return;
    }

    // Accumulate audio data
    device.audioBuffer.push(Buffer.from(data));

    // Reset silence timer
    if (device.silenceTimer) {
      clearTimeout(device.silenceTimer);
    }

    // Fire processing after 1.5s of silence
    device.silenceTimer = setTimeout(() => {
      onAudioReady(senderIp);
    }, 1500);
  });

  socket.on("listening", () => {
    const addr = socket.address();
    console.log(`[udp] Listening on ${addr.address}:${addr.port}`);
  });

  socket.on("error", (err) => {
    console.error(`[udp] Error: ${err.message}`);
  });

  socket.bind(config.udpPort, config.udpHost);
  return socket;
}
