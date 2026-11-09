/**
 * Whether a node type's Advanced disclosure is open, kept per browser.
 *
 * Like the guided mode flag, this is a view choice and not a project fact: it lives in `localStorage` under
 * `stokes.setup.advanced.<type>` (the workspace document refuses new keys). Every read and write goes
 * through a `Storage` the caller hands in and is wrapped in try/catch, so a browser that blocks storage just
 * shows Advanced closed.
 */

/** The key a node type's choice lives under. */
export function advancedKey(type: string): string {
  return `stokes.setup.advanced.${type}`;
}

/** The kept choice for a node type: true only when it was left open. */
export function readAdvancedOpen(storage: Pick<Storage, "getItem"> | null, type: string): boolean {
  try {
    return storage?.getItem(advancedKey(type)) === "open";
  } catch {
    return false;
  }
}

/** Keep the choice; a refusal from the browser is ignored. */
export function writeAdvancedOpen(storage: Pick<Storage, "setItem" | "removeItem"> | null, type: string, open: boolean): void {
  try {
    if (open) storage?.setItem(advancedKey(type), "open");
    else storage?.removeItem(advancedKey(type));
  } catch {
    /* storage refused: the choice lasts until the page is closed */
  }
}
