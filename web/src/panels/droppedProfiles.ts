/**
 * A kind change that dropped a boundary's time profiles (ADR-0022 addendum 2026-11-24): `boundary.set` and
 * `boundary.assign` answer `profiles_dropped`, the values whose profile the new kind does not use and which
 * the change removed. The toast names them and offers Undo, which steps back only while the change is still
 * the newest entry of the undo stack. An answer without the field (an older server) drops nothing visibly.
 *
 * Import rule: the store and the profile words only; no React.
 */
import { useStore } from "../state/store";
import { VALUE_WORDS } from "./timeProfileModel";

/** The values whose profile an answer says were dropped; none when the answer does not say. */
export function droppedOf(answer: unknown): string[] {
  if (typeof answer !== "object" || answer === null) return [];
  const raw = (answer as { profiles_dropped?: unknown }).profiles_dropped;
  return Array.isArray(raw) ? raw.filter((p): p is string => typeof p === "string") : [];
}

/** "the speed profile was dropped", "the speed and temperature profiles were dropped". */
export function droppedText(dropped: readonly string[]): string {
  const words = dropped.map((p) => (VALUE_WORDS[p] ?? p).toLowerCase());
  const list = words.length <= 1 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
  return `the ${list} profile${words.length === 1 ? " was" : "s were"} dropped`;
}

interface HistoryState {
  undo: string | null;
  undo_kind: string | null;
}

/**
 * The toast after an assign or a kind change: `done` ("Condition assigned") alone, or with which profiles
 * were dropped and an Undo that brings them back.
 */
export async function toastChange(answer: unknown, done: string): Promise<void> {
  const s = useStore.getState();
  const dropped = droppedOf(answer);
  if (dropped.length === 0) {
    s.pushToast("ok", done);
    return;
  }
  let after: HistoryState | null = null;
  try {
    after = (await s.execute("history.state", {}, { silent: true })) as HistoryState;
  } catch {
    after = null;
  }
  const text = `${done}; ${droppedText(dropped)}`;
  s.pushToast("info", text, { label: "Undo", run: () => void undoChange(after) });
}

/** Undo the change, only while it is still the newest step. */
async function undoChange(after: HistoryState | null): Promise<void> {
  const s = useStore.getState();
  try {
    const now = (await s.execute("history.state", {}, { silent: true })) as HistoryState;
    if (after !== null && (now.undo !== after.undo || now.undo_kind !== after.undo_kind)) {
      s.pushToast("info", "Other changes were made since: step back through them with Undo.");
      return;
    }
    await s.execute("history.undo");
    await s.refresh();
  } catch {
    /* reported by execute */
  }
}
