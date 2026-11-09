/**
 * The text editor of an imported case's files: the pure parts.
 *
 * The server lints the text with the agent's `external` profile when it is saved (`case.file_write`),
 * so a refusal arrives as an error whose `details.findings` list what was found. The editor shows the
 * first as a sentence under the text.
 */
export interface LintFinding {
  line?: number;
  message?: string;
}

/** One sentence for a refused save: the finding with its line, else the error's own message. */
export function refusalText(message: string, details: unknown): string {
  const findings = (details as { findings?: LintFinding[] } | null | undefined)?.findings;
  const first = Array.isArray(findings) ? findings[0] : undefined;
  if (first?.message) return first.line ? `Line ${first.line}: ${first.message}` : first.message;
  return message;
}

/** Whether the buffer differs from what was loaded, so Save can be off when nothing changed. */
export function isDirty(loaded: string, current: string): boolean {
  return loaded !== current;
}

/** Bytes of `text` as UTF-8, for the size line (the server limit is 256 KB). */
export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

export const MAX_EDIT_BYTES = 256 * 1024;
