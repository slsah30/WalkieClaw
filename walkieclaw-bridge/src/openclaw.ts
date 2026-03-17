import type { BridgeConfig } from "./config.js";

/**
 * Send text to OpenClaw via the gateway's /v1/chat/completions HTTP API.
 * Same pattern as the VPS bridge — simple fetch, no subprocess, no window.
 */
export async function initOpenclaw(config: BridgeConfig): Promise<void> {
  const url = `${config.openclawUrl.replace(/\/+$/, "")}/v1/chat/completions`;
  console.log(`[openclaw] Using HTTP API: ${url}`);

  // Verify gateway is reachable
  try {
    const resp = await fetch(config.openclawUrl.replace(/\/+$/, "") + "/health");
    if (resp.ok) {
      console.log("[openclaw] Gateway is live.");
    } else {
      console.warn(`[openclaw] Gateway returned ${resp.status}`);
    }
  } catch {
    console.warn("[openclaw] Gateway not reachable. Make sure it's running:");
    console.warn("[openclaw]   openclaw gateway run");
  }
}

export async function sendToOpenclaw(
  text: string,
  deviceIp: string,
  config: BridgeConfig
): Promise<string> {
  if (!text.trim()) {
    return "I didn't catch that. Could you say it again?";
  }

  const agentId = config.openclawAgentId;
  console.log(`[openclaw] Sending [agent=${agentId}, device=${deviceIp}]: ${text}`);

  try {
    const baseUrl = config.openclawUrl.replace(/\/+$/, "");
    const url = `${baseUrl}/v1/chat/completions`;
    const userId = `walkieclaw-${deviceIp}`;

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (config.openclawToken) {
      headers["Authorization"] = `Bearer ${config.openclawToken}`;
    }

    const startTime = Date.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60_000);

    const resp = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model: `openclaw:${agentId}`,
        user: userId,
        stream: false,
        messages: [{ role: "user", content: text }],
      }),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!resp.ok) {
      const errText = await resp.text().catch(() => "");
      console.error(`[openclaw] HTTP ${resp.status}: ${errText.slice(0, 200)}`);
      return "Sorry, I had trouble thinking about that.";
    }

    const data = await resp.json() as any;
    let content = data.choices?.[0]?.message?.content ?? "";
    content = content.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
    if (!content) content = "I processed that but had nothing to say.";

    const model = data.model ?? "unknown";
    const elapsed = Date.now() - startTime;
    console.log(`[openclaw] Response [${model}] (${elapsed}ms): ${content.slice(0, 100)}...`);
    return content;
  } catch (err: any) {
    if (err.name === "AbortError") {
      console.error("[openclaw] Request timed out");
      return "Sorry, the request timed out.";
    }
    console.error(`[openclaw] Error: ${err.message}`);
    return "Sorry, I encountered an error.";
  }
}
