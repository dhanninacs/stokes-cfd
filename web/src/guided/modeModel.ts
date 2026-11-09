/**
 * Guided or Full: which of the two arrangements the window is in, and how the choice is kept.
 *
 * **Guided is the default.** The flow strip and the step cards sit on the viewport and the tree is
 * put away; Full is the arrangement the app had before, tree and Details beside it. The strip is
 * visible in both.
 *
 * The choice lives in the workspace document as `mode` (read by `workspace/defaults.ts`), and is also
 * kept in the browser's storage: the server validates every key it writes back to the workspace file
 * and does not know `mode` yet, so a value written there would be refused. Storage is a per-viewer
 * convenience, wrapped so a refusal (a private window, a full quota) leaves the default in place.
 *
 * Import rule: no React, no store, no DOM.
 */

export type GuidedMode = "guided" | "full";

/** The `localStorage` key the choice lives under. */
export const MODE_KEY = "stokes.guided.mode";

/** The key that records that guided mode has put the tree away once, so a user who opens it keeps it open. */
export const TREE_INIT_KEY = "stokes.guided.treeInit";

/** `mode` as a document or a stored string gives it; anything else is null (so the default stands). */
export function readMode(value: unknown): GuidedMode | null {
  return value === "guided" || value === "full" ? value : null;
}

/** The stored choice, or null when there is none or the storage refuses. */
export function readStoredMode(storage: Pick<Storage, "getItem"> | null): GuidedMode | null {
  if (storage === null) return null;
  try {
    return readMode(storage.getItem(MODE_KEY));
  } catch {
    return null;
  }
}

/** Keep the choice; a refusal is swallowed, as it is a convenience. */
export function writeStoredMode(storage: Pick<Storage, "setItem"> | null, mode: GuidedMode): void {
  if (storage === null) return;
  try {
    storage.setItem(MODE_KEY, mode);
  } catch {
    // The choice then lasts for the session only.
  }
}

/** The mode in force: the user's choice, else the document's, else Guided. */
export function effectiveMode(chosen: GuidedMode | null, document: unknown): GuidedMode {
  return chosen ?? readMode(document) ?? "guided";
}
