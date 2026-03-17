import { randomBytes } from "crypto";
import type { BridgeConfig } from "./config.js";
import type { DeviceManager, PendingNotification } from "./devices.js";
import { synthesizeSpeech, getWavUrl } from "./tts.js";
import { getWavDuration } from "./audio.js";
import { sanitizeForDisplay } from "./utils.js";
import { readFileSync } from "fs";

export interface TimerEntry {
  id: string;
  text: string;
  fireAt: number; // epoch ms
  deviceIp: string; // "" = broadcast
  createdAt: number;
}

export class TimerManager {
  private timers = new Map<string, TimerEntry>();
  private intervalHandle: ReturnType<typeof setInterval> | null = null;
  private config: BridgeConfig;
  private devices: DeviceManager;

  constructor(config: BridgeConfig, devices: DeviceManager) {
    this.config = config;
    this.devices = devices;
  }

  schedule(text: string, delayMs: number, deviceIp = ""): TimerEntry {
    const id = `timer_${randomBytes(4).toString("hex")}`;
    const entry: TimerEntry = {
      id,
      text,
      fireAt: Date.now() + delayMs,
      deviceIp,
      createdAt: Date.now(),
    };
    this.timers.set(id, entry);
    console.log(`[timers] Scheduled ${id}: "${text.slice(0, 40)}" in ${Math.round(delayMs / 1000)}s`);
    return entry;
  }

  cancel(id: string): boolean {
    const existed = this.timers.has(id);
    this.timers.delete(id);
    if (existed) console.log(`[timers] Cancelled ${id}`);
    return existed;
  }

  list(): TimerEntry[] {
    return Array.from(this.timers.values()).sort((a, b) => a.fireAt - b.fireAt);
  }

  get count(): number {
    return this.timers.size;
  }

  /**
   * Start the tick loop. Call once after construction.
   */
  start(): void {
    this.intervalHandle = setInterval(() => this.tick(), 1000);
  }

  stop(): void {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle);
      this.intervalHandle = null;
    }
  }

  private async tick(): Promise<void> {
    const now = Date.now();
    const due: TimerEntry[] = [];

    for (const [id, timer] of this.timers) {
      if (now >= timer.fireAt) {
        due.push(timer);
        this.timers.delete(id);
      }
    }

    for (const timer of due) {
      try {
        await this.fireTimer(timer);
      } catch (err: any) {
        console.error(`[timers] Error firing ${timer.id}: ${err.message}`);
      }
    }
  }

  private async fireTimer(timer: TimerEntry): Promise<void> {
    console.log(`[timers] Firing ${timer.id}: "${timer.text.slice(0, 40)}"`);

    const wavPath = await synthesizeSpeech(timer.text, this.config);
    const wavUrl = getWavUrl(wavPath, this.config);
    const wavBuf = readFileSync(wavPath);
    const duration = Math.round(getWavDuration(wavBuf) * 10) / 10;
    const displayText = sanitizeForDisplay(timer.text.slice(0, 200));

    const notif: PendingNotification = { text: displayText, wavUrl, duration };

    if (timer.deviceIp) {
      const device = this.devices.getDevice(timer.deviceIp);
      device.pendingNotifications.push(notif);
    } else {
      for (const dev of this.devices.getAllDevices()) {
        dev.pendingNotifications.push({ ...notif });
      }
    }
  }
}
