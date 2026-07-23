export const DISPLAY_SENTENCE_MAX_CHARS = 88;

/** Split transcript text into readable sentence lines without losing words. */
export function splitDisplayLines(
  text: string,
  maxChars = DISPLAY_SENTENCE_MAX_CHARS,
): string[] {
  const normalized = text.replace(/\s+/gu, " ").trim();
  if (!normalized) return [];
  if (maxChars < 1) throw new Error("maxChars must be positive");

  const sentences = normalized.split(/(?<=[.!?…;])\s+/u);
  const lines: string[] = [];
  for (const sentence of sentences) {
    const words = sentence.split(/\s+/u);
    let line = "";
    for (const word of words) {
      if (!line) {
        line = word;
      } else if (line.length + 1 + word.length <= maxChars) {
        line += ` ${word}`;
      } else {
        lines.push(line);
        line = word;
      }
    }
    if (line) lines.push(line);
  }
  return lines;
}

export function formatDisplayLines(text: string): string {
  return splitDisplayLines(text).join("\n");
}
