/**
 * A command's description as one line of plain words. The registry writes descriptions as Markdown for the
 * CLI and MCP readers; the palette row is a single truncated line, where `**` and backticks read as noise.
 */
export function plainDescription(text: string): string {
  return text
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
