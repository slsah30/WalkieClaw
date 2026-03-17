/**
 * Sanitize text for LVGL display (ASCII-safe, single line).
 */
export function sanitizeForDisplay(text: string): string {
  const replacements: Record<string, string> = {
    "\u2014": "-",
    "\u2013": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u2022": "*",
    "\u00a0": " ",
  };

  for (const [from, to] of Object.entries(replacements)) {
    text = text.replaceAll(from, to);
  }

  // Collapse whitespace
  text = text.split(/\s+/).join(" ").trim();

  // Replace non-ASCII with ?
  text = text.replace(/[^\x20-\x7E]/g, "?");

  return text;
}

/**
 * Strip markdown formatting for clean TTS output.
 */
export function stripMarkdown(text: string): string {
  return text
    // Bold/italic: **text**, *text*, __text__, _text_
    .replace(/\*{1,3}(.*?)\*{1,3}/g, "$1")
    .replace(/_{1,3}(.*?)_{1,3}/g, "$1")
    // Headers: # text
    .replace(/^#{1,6}\s+/gm, "")
    // Bullet lists: - item, * item
    .replace(/^[\s]*[-*+]\s+/gm, "")
    // Numbered lists: 1. item
    .replace(/^[\s]*\d+\.\s+/gm, "")
    // Links: [text](url)
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    // Inline code: `code`
    .replace(/`([^`]+)`/g, "$1")
    // Code blocks: ```...```
    .replace(/```[\s\S]*?```/g, "")
    // Collapse multiple newlines
    .replace(/\n{2,}/g, ". ")
    .replace(/\n/g, " ")
    .trim();
}

/**
 * Format bytes as human-readable string.
 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
