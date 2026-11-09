import { movePanel } from "./arrange";
import type { DockSlot, LayoutSpec } from "./types";

/**
 * Put a whole dock away, or bring it back.
 *
 * **The owner's second layout request** (claude-win2, 1930): *"each has a collapse button in their
 * header that removes the card completely... and a way back that does not need a visible rail."*
 *
 * **Removing the card means removing its PANELS**, not collapsing the slot: a collapsed slot still
 * draws a 40 px strip of icons, which is exactly what the owner had me delete this morning. A slot
 * with no panels renders nothing at all (`Layout` returns null), so this moves them to `hidden` and
 * remembers where they came from - on the slot itself, in `document.layout[side].stashed`, so opening
 * it again puts each panel back where it was rather than guessing.
 *
 * **Pure, and a pair**: hiding a slot that is already hidden is a no-op, and showing one that has
 * nothing stashed is too - so a chord pressed twice cannot lose the layout, which is the failure that
 * makes people stop using collapse buttons.
 */
export interface Stashed {
  /** The panel ids the slot held, in order, the first time it was put away. */
  ids?: string[];
}

export function hideSlot(layout: LayoutSpec, side: DockSlot): LayoutSpec {
  const ids = layout[side].panels.map((p) => p.id);
  if (ids.length === 0) return layout; // nothing to put away, and nothing to remember
  let next = layout;
  for (const id of ids) next = movePanel(next, id, { kind: "hidden" });
  // The stash is written on the slot, in the document, so it survives a reload with the layout.
  return { ...next, [side]: { ...next[side], stashed: ids } };
}

export function showSlot(layout: LayoutSpec, side: DockSlot): LayoutSpec {
  const stash = (layout[side] as { stashed?: string[] }).stashed ?? [];
  if (stash.length === 0) {
    // **Showing an empty slot shows what belongs there**: the first open after a document with no
    // stash would otherwise do nothing, which reads as a broken button.
    return layout;
  }
  let next = layout;
  for (const [index, id] of stash.entries()) next = movePanel(next, id, { kind: "slot", slot: side, index });
  return { ...next, [side]: { ...next[side], collapsed: false, stashed: [] } };
}

/** True when the dock is not on screen: no panels, or collapsed to a strip. */
export function slotHidden(layout: LayoutSpec, side: DockSlot): boolean {
  return layout[side].panels.length === 0 || layout[side].collapsed;
}
