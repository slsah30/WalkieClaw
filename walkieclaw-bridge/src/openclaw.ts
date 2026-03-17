import type { BridgeConfig } from "./config.js";
import type { ChatMessage } from "./devices.js";

export type StreamChunkCallback = (sentenceText: string) => Promise<void>;

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
  messages: ChatMessage[],
  deviceIp: string,
  config: BridgeConfig
): Promise<string> {
  if (messages.length === 0) {
    return "I didn't catch that. Could you say it again?";
  }

  const lastMsg = messages[messages.length - 1]?.content ?? "";
  const agentId = config.openclawAgentId;
  console.log(`[openclaw] Sending [agent=${agentId}, device=${deviceIp}, turns=${messages.length}]: ${lastMsg.slice(0, 80)}`);

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
        messages,
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

/**
 * Stream response from OpenClaw, calling onChunk for each sentence.
 * Returns the full concatenated response text.
 */
export async function streamFromOpenclaw(
  messages: ChatMessage[],
  deviceIp: string,
  config: BridgeConfig,
  onChunk: StreamChunkCallback
): Promise<string> {
  if (messages.length === 0) {
    await onChunk("I didn't catch that. Could you say it again?");
    return "I didn't catch that. Could you say it again?";
  }

  const agentId = config.openclawAgentId;
  const lastMsg = messages[messages.length - 1]?.content ?? "";
  console.log(`[openclaw] Streaming [agent=${agentId}, device=${deviceIp}]: ${lastMsg.slice(0, 80)}`);

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
        stream: true,
        messages,
      }),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!resp.ok) {
      const errText = await resp.text().catch(() => "");
      console.error(`[openclaw] HTTP ${resp.status}: ${errText.slice(0, 200)}`);
      await onChunk("Sorry, I had trouble thinking about that.");
      return "Sorry, I had trouble thinking about that.";
    }

    // Parse SSE stream
    let fullText = "";
    let buffer = "";
    const reader = resp.body?.getReader();
    const decoder = new TextDecoder();

    if (!reader) {
      // Fallback: non-streaming response
      const data = await resp.json() as any;
      let content = data.choices?.[0]?.message?.content ?? "";
      content = content.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
      if (!content) content = "I processed that but had nothing to say.";
      await onChunk(content);
      return content;
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value, { stream: true });
      const lines = text.split("\n");

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6).trim();
          if (data === "[DONE]") continue;
          try {
            const parsed = JSON.parse(data);
            const delta = parsed.choices?.[0]?.delta?.content ?? "";
            if (delta) {
              fullText += delta;
              buffer += delta;

              // Check for sentence boundary
              const sentenceEnd = buffer.search(/[.!?]\s|[.!?]$/);
              if (sentenceEnd !== -1) {
                const endIdx = sentenceEnd + 1;
                const sentence = buffer.slice(0, endIdx).trim();
                buffer = buffer.slice(endIdx).trim();
                if (sentence) {
                  const cleaned = sentence.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
                  if (cleaned) await onChunk(cleaned);
                }
              }
            }
          } catch {
            // Skip malformed SSE data
          }
        }
      }
    }

    // Flush remaining buffer
    if (buffer.trim()) {
      const cleaned = buffer.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
      if (cleaned) await onChunk(cleaned);
    }

    fullText = fullText.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
    if (!fullText) {
      fullText = "I processed that but had nothing to say.";
      await onChunk(fullText);
    }

    const elapsed = Date.now() - startTime;
    console.log(`[openclaw] Stream complete (${elapsed}ms): ${fullText.slice(0, 100)}...`);
    return fullText;
  } catch (err: any) {
    if (err.name === "AbortError") {
      console.error("[openclaw] Stream timed out");
      await onChunk("Sorry, the request timed out.");
      return "Sorry, the request timed out.";
    }
    console.error(`[openclaw] Stream error: ${err.message}`);
    await onChunk("Sorry, I encountered an error.");
    return "Sorry, I encountered an error.";
  }
}
