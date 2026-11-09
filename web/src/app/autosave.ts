/**
 * Autosave: unsaved work kept in the project's recovery copy once a minute (`project.autosave`).
 *
 * The server writes the copy BESIDE the project file, never over it, so the user's choice to save or
 * discard is untouched; it only means a server restart or a crash before Ctrl+S loses a minute rather
 * than the whole set-up (the mixing-elbow walkthrough, 2026-11-08). The next open offers the copy back
 * (`RecoveryCard`).
 */
import { useEffect } from "react";
import { useStore } from "../state/store";
import { AUTOSAVE_MS, autosaveWanted } from "./autosaveModel";

/** Copy the unsaved work now, quietly: a copy that fails is retried on the next tick. */
export async function autosaveNow(): Promise<void> {
  const s = useStore.getState();
  if (!autosaveWanted(s.project, s.connected)) return;
  try {
    await s.execute("project.autosave", {}, { silent: true });
  } catch {
    // An older server has no project.autosave, or the disk refused the write: nothing to tell the user
    // every minute, and the next tick tries again.
  }
}

/** Run the autosave for as long as the app is mounted. */
export function useAutosave(): void {
  useEffect(() => {
    const timer = window.setInterval(() => void autosaveNow(), AUTOSAVE_MS);
    return () => window.clearInterval(timer);
  }, []);
}
