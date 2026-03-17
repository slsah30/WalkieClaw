export interface PendingNotification {
  text: string;
  wavUrl: string;
  duration: number;
}

export class DeviceState {
  ip: string;
  audioBuffer: Buffer[] = [];
  isProcessing = false;

  // Poll state
  pollStatus: "idle" | "processing" | "ready" = "idle";
  pollStage: "" | "transcribing" | "thinking" | "speaking" = "";
  pollWavUrl = "";
  pollText = "";
  pollTranscript = "";
  pollWavDuration = 0;
  pollReadyTime = 0;

  // Pending commands
  pendingWifiSsid = "";
  pendingWifiPassword = "";
  pendingNotifications: PendingNotification[] = [];

  // Timing
  lastActivity = Date.now();
  silenceTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(ip: string) {
    this.ip = ip;
  }

  get audioBufferSize(): number {
    return this.audioBuffer.reduce((sum, b) => sum + b.length, 0);
  }

  drainAudioBuffer(): Buffer {
    const combined = Buffer.concat(this.audioBuffer);
    this.audioBuffer = [];
    return combined;
  }

  resetPollState(): void {
    this.pollStatus = "idle";
    this.pollStage = "";
    this.pollWavUrl = "";
    this.pollText = "";
    this.pollTranscript = "";
    this.pollWavDuration = 0;
    this.pollReadyTime = 0;
  }
}

export class DeviceManager {
  private devices = new Map<string, DeviceState>();
  private authenticatedUdpIps = new Set<string>();

  getDevice(ip: string): DeviceState {
    let device = this.devices.get(ip);
    if (!device) {
      console.log(`[devices] New device connected: ${ip}`);
      device = new DeviceState(ip);
      this.devices.set(ip, device);
    }
    device.lastActivity = Date.now();
    return device;
  }

  getDeviceIfExists(ip: string): DeviceState | undefined {
    return this.devices.get(ip);
  }

  authenticateUdp(ip: string): void {
    this.authenticatedUdpIps.add(ip);
  }

  isUdpAuthenticated(ip: string): boolean {
    return this.authenticatedUdpIps.has(ip);
  }

  getAllDevices(): DeviceState[] {
    return Array.from(this.devices.values());
  }

  get deviceCount(): number {
    return this.devices.size;
  }

  /**
   * Remove devices inactive for more than `maxAgeMs` milliseconds.
   */
  cleanupStale(maxAgeMs = 600_000): void {
    const now = Date.now();
    for (const [ip, device] of this.devices) {
      // Reset stale "ready" responses (>60s uncollected)
      if (device.pollStatus === "ready" && device.pollReadyTime > 0) {
        if (now - device.pollReadyTime > 60_000) {
          console.log(`[devices] Stale poll response for ${ip} (>60s), resetting`);
          device.resetPollState();
        }
      }
      // Remove inactive devices
      if (
        now - device.lastActivity > maxAgeMs &&
        !device.isProcessing &&
        device.pollStatus === "idle" &&
        device.pendingNotifications.length === 0
      ) {
        console.log(`[devices] Removing stale device: ${ip}`);
        this.devices.delete(ip);
        this.authenticatedUdpIps.delete(ip);
      }
    }
  }
}
