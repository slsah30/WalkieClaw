export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface PendingNotification {
  text: string;
  wavUrl: string;
  duration: number;
}

export class DeviceState {
  ip: string;
  audioBuffer: Buffer[] = [];
  isProcessing = false;

  // Conversation history
  conversationHistory: ChatMessage[] = [];
  maxHistoryLength = 20; // 10 turns (user + assistant)

  // Poll state
  pollStatus: "idle" | "processing" | "ready" = "idle";
  pollStage: "" | "transcribing" | "thinking" | "speaking" = "";
  pollWavUrl = "";
  pollText = "";
  pollTranscript = "";
  pollWavDuration = 0;
  pollReadyTime = 0;

  // Streaming response chunks
  pendingResponseChunks: Array<{ wavUrl: string; text: string; duration: number }> = [];

  // Walkie-talkie mode
  pairedDeviceIp: string | null = null;
  walkieTalkieMode = false;

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

  addToHistory(role: ChatMessage["role"], content: string): void {
    this.conversationHistory.push({ role, content });
    while (this.conversationHistory.length > this.maxHistoryLength) {
      this.conversationHistory.shift();
    }
  }

  clearHistory(): void {
    this.conversationHistory = [];
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
  private defaultMaxHistory: number;

  constructor(defaultMaxHistory = 20) {
    this.defaultMaxHistory = defaultMaxHistory;
  }

  getDevice(ip: string): DeviceState {
    let device = this.devices.get(ip);
    if (!device) {
      console.log(`[devices] New device connected: ${ip}`);
      device = new DeviceState(ip);
      device.maxHistoryLength = this.defaultMaxHistory;
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
        device.clearHistory();
        this.devices.delete(ip);
        this.authenticatedUdpIps.delete(ip);
      }
    }
  }
}
