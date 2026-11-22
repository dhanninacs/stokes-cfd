/**
 * The list of what the results view can show (`ShownSurfaces.tsx`): the mesh's boundaries, then the
 * slices, each with whether it is drawn now. Pure, so the order and the rules are tested in Node.
 *
 * Import rule: no React, no store, no DOM.
 */
import type { MeshPatchEntry } from "../state/meshPatches";

export interface ShownItem {
  kind: "patch" | "slice" | "mid";
  /** The mesh_patch node id, the slice id, or "mid" for the run's own mid-plane cut. */
  id: string;
  label: string;
  shown: boolean;
}

/**
 * Boundaries first, in the mesh's order, then the slices: the user's own when there are any, else the
 * run's mid-plane cut when it has one. A boundary hidden in the tree (`hiddenNodes`) or switched off in
 * the mesh (`visible: false`) is listed as not shown.
 */
export function shownItems(
  patches: readonly MeshPatchEntry[],
  hidden: readonly string[],
  slices: readonly { id: string; label: string; visible: boolean }[],
  hasMid: boolean,
  midOn: boolean,
): ShownItem[] {
  const out: ShownItem[] = patches.map((p) => ({ kind: "patch", id: p.id, label: p.name, shown: p.visible && !hidden.includes(p.id) }));
  if (slices.length > 0) for (const s of slices) out.push({ kind: "slice", id: s.id, label: s.label, shown: s.visible });
  else if (hasMid) out.push({ kind: "mid", id: "mid", label: "Mid-plane", shown: midOn });
  return out;
}
