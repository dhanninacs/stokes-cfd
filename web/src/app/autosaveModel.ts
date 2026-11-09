/** The pure rules behind autosave (`autosave.ts`) and its recovery offer (`RecoveryCard.tsx`). */
import type { ProjectInfo } from "../api/types";

/** How often unsaved work is copied. */
export const AUTOSAVE_MS = 60_000;

/**
 * Whether there is anything to keep: an open project, on disk, with unsaved changes, and a server to ask.
 * An older server's missing `dirty` counts as unsaved, as it does for the Close prompt.
 */
export function autosaveWanted(project: ProjectInfo | null, connected: boolean): boolean {
  return connected && project !== null && project.path !== null && project.dirty !== false;
}

/** "14:05 on 8 Nov", in the user's locale; the text itself when it is not a date. */
export function recoveryWhen(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return iso;
  const time = when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const day = when.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return `${time} on ${day}`;
}
