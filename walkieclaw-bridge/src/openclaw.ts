import { spawn } from "child_process";
import { join } from "path";
import { homedir, platform } from "os";
import { existsSync } from "fs";
import type { BridgeConfig } from "./config.js";

function findOpenclawScript(): string {
  const npmGlobal = join(homedir(), "AppData", "Roaming", "npm");
  const candidates = [
    join(npmGlobal, "node_modules", "openclaw", "openclaw.mjs"),
    join(npmGlobal, "node_modules", "openclaw", "openclaw.js"),
    "/usr/local/lib/node_modules/openclaw/openclaw.mjs",
    "/usr/lib/node_modules/openclaw/openclaw.mjs",
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return "";
}

function runOpenclaw(args: string[], timeoutMs: number): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    let proc;
    const script = findOpenclawScript();

    if (script) {
      // Run node directly — bypasses cmd.exe arg parsing issues on Windows
      proc = spawn(process.execPath, [script, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    } else {
      // Fallback for Unix where openclaw is on PATH
      proc = spawn("openclaw", args, { stdio: ["ignore", "pipe", "pipe"] });
    }

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => { stdout += d; });
    proc.stderr.on("data", (d) => { stderr += d; });

    const timer = setTimeout(() => {
      proc.kill();
      reject(Object.assign(new Error("Timed out"), { killed: true }));
    }, timeoutMs);

    proc.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) {
        resolve({ stdout, stderr });
      } else {
        reject(new Error(stderr || `openclaw exited with code ${code}`));
      }
    });
    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

/**
 * Send text to OpenClaw and get a response.
 * Uses the `openclaw agent` CLI which talks to the local gateway via WebSocket.
 */
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
    const sessionId = `walkieclaw-${deviceIp.replace(/\./g, "-")}`;
    const { stdout, stderr } = await runOpenclaw([
      "agent",
      "-m", text,
      "--agent", agentId,
      "--session-id", sessionId,
      "--json",
    ], 300_000);

    if (stderr) {
      // openclaw agent prints diagnostics to stderr, ignore non-fatal ones
      const fatal = stderr.split("\n").filter(
        (l) => !l.includes("[diagnostic]") && l.trim()
      );
      if (fatal.length > 0) {
        console.warn(`[openclaw] stderr: ${fatal.join(" ").slice(0, 200)}`);
      }
    }

    const data = JSON.parse(stdout);

    if (data.status !== "ok") {
      console.error(`[openclaw] Agent status: ${data.status} - ${data.summary}`);
      return "Sorry, I had trouble thinking about that.";
    }

    const payloads = data.result?.payloads ?? [];
    let content = payloads.map((p: any) => p.text ?? "").join("\n").trim();

    // Strip <think> tags
    content = content.replace(/<think>[\s\S]*?<\/think>/g, "").trim();

    if (!content) {
      content = "I processed that but had nothing to say.";
    }

    const model = data.result?.meta?.agentMeta?.model ?? "unknown";
    console.log(`[openclaw] Response [${model}]: ${content.slice(0, 100)}...`);
    return content;
  } catch (err: any) {
    if (err.killed) {
      console.error("[openclaw] Request timed out");
      return "Sorry, the request timed out.";
    }
    console.error(`[openclaw] Error: ${err.message}`);
    return "Sorry, I encountered an error.";
  }
}
