/**
 * Where the view cube goes (`islands`, the `gizmo` group). The viewport draws the cube into the WebGL
 * canvas, where CSS cannot place it, so the island layer reserves a 128 px square for it in the row of
 * the corner the document puts it in, measures that square after every layout and tells the viewport
 * here; the viewport draws the cube into it. `null` is the document hiding the cube. Until the island
 * layer has said anything (the workspace has not loaded) the cube is where it always was, at the
 * bottom right, so the default window never sees it move.
 *
 * Both distances are measured from the corner's own edges, so a window resize needs no new message.
 */
import type { Corner } from "./types";

export interface GizmoPlacement {
  corner: Corner;
  /** From the viewport's left edge (corners `bl`, `tl`) or right edge (`br`, `tr`) to the square, in px. */
  x: number;
  /** From the viewport's top edge (`tl`, `tr`) or bottom edge (`bl`, `br`) to the square, in px. */
  y: number;
}

/** The corner the cube was always drawn in, flush with it. */
export const DEFAULT_GIZMO: GizmoPlacement = { corner: "br", x: 0, y: 0 };

let placement: GizmoPlacement | null = DEFAULT_GIZMO;
const listeners = new Set<() => void>();

function same(a: GizmoPlacement | null, b: GizmoPlacement | null): boolean {
  return a === b || (a !== null && b !== null && a.corner === b.corner && a.x === b.x && a.y === b.y);
}

/** Set by the island layer; listeners hear only a change. */
export function setGizmoPlacement(next: GizmoPlacement | null): void {
  if (same(placement, next)) return;
  placement = next;
  listeners.forEach((listener) => listener());
}

export function gizmoPlacement(): GizmoPlacement | null {
  return placement;
}

export function subscribeGizmo(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
