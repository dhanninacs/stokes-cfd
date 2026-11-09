/**
 * Window-level custom events that connect the chrome to the panels without shared component
 * state. The chrome (context toolbar, shortcuts) dispatches; the panels listen:
 *
 *   window.addEventListener("stokes:rename", (e) => { e.detail.patch; e.detail.nodeId; });
 *
 * The WindowEventMap augmentation below types `e.detail` for every file in the program.
 *
 * Assigning a boundary no longer goes through an event (T-061): the toolbar, the patch card and
 * the ribbon share the same assign menu, which calls `boundary.assign` itself.
 */

export interface RenameDetail {
  /** Name of the selected patch. */
  patch: string;
  /** Id of the tree node selected for it (a named selection or a part), when one exists. */
  nodeId: string | null;
}

declare global {
  interface WindowEventMap {
    "stokes:rename": CustomEvent<RenameDetail>;
  }
}

export const RENAME_EVENT = "stokes:rename";

export function emitRename(detail: RenameDetail): void {
  window.dispatchEvent(new CustomEvent<RenameDetail>(RENAME_EVENT, { detail }));
}
