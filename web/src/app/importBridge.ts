/**
 * One door for "import this file object": the project menu (which owns the format list and the units
 * question of an import) registers what to do with a file while it is mounted, and the viewport's drop
 * calls it. A module with a single slot, like the action bus, so the drop needs neither the menu's state
 * nor an import of the menu.
 */
type Handler = (file: File) => void;

let handler: Handler | null = null;

/** The menu mounts with its handler and unmounts with `null`; the call that removes it is returned. */
export function setImportHandler(next: Handler): () => void {
  handler = next;
  return () => {
    if (handler === next) handler = null;
  };
}

/** Hands a file to the import flow; false when nothing is registered (the project menu is hidden from the bar). */
export function importFileObject(file: File): boolean {
  if (handler === null) return false;
  handler(file);
  return true;
}
