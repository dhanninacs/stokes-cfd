/**
 * What a context menu was opened on (T-171, PLAN ux-menus a and c), as pure functions.
 *
 * The viewport menu, the tree row menu and the keyboard's menu key all ask the same question: what is
 * under the pointer, or what does the keyboard mean? `resolveViewportTarget` and `resolveKeyboardTarget`
 * answer it; `selectionFor` is the CAD rule that says what to pick before the menu opens. This module
 * imports no store and no three.js (only types), so the tests run in Node.
 *
 * The owner's rule (decision 2, 2026-10-14): a right-click selects what it lands on unless the target is
 * already in the pick. In Edge, Point, Paint and Measure mode it does not change the selection, and
 * empty space keeps the current pick. A pick holds one kind at a time (ADR-0022 15.4), so selecting a
 * body under `pickMode: "edge"` would leave a body pick under an edge mode: `selectionFor` therefore
 * answers null in those modes.
 */
import type { Picked, PickMode } from "../state/pick";
import type { PickHit } from "../viewport/scene";

/** The sample part's source id (`SAMPLE_SOURCE` in viewport/scene.ts, which this module cannot import). */
export const SAMPLE_PART = "@sample";

export type ContextTarget =
  | { kind: "empty" }
  | { kind: "body"; part: string; patch: string; inPick: boolean; sample: boolean }
  | { kind: "face"; part: string; patch: string; face: number; inPick: boolean; sample: boolean }
  /** The pick as a whole: the keyboard's target, and a hit inside a pick that holds several bodies or parts. */
  | { kind: "pick"; label: string }
  /** A tree row (T-174). `name` lets a reason say which node it is. */
  | { kind: "node"; nodeId: string; nodeType: string; structural: boolean; name?: string };

/** The store fields the resolvers read. */
export interface TargetState {
  pickMode: PickMode;
  picked: Picked;
  selectedPatch: string | null;
}

/** The modes in which a right-click leaves the selection alone. */
export interface SelectionModes {
  pickMode: PickMode;
  paintMode: boolean;
  measureMode: boolean;
}

/** `describePick` of the store, passed in so that this module needs no store. */
export type DescribePick = (picked: Picked, mode: PickMode) => string | null;

/** How many parts hold faces in the pick. */
function partsWithFaces(picked: Picked): number {
  return Object.values(picked.faces).filter((ids) => ids.length > 0).length;
}

/** True when the pick holds more than one body, or faces of more than one part. */
function isMultiPick(picked: Picked): boolean {
  return picked.bodies.length > 1 || partsWithFaces(picked) > 1;
}

/** What a right-click at a hit lands on, under the current mode. */
export function resolveViewportTarget(hit: PickHit | null, state: TargetState, describe: DescribePick): ContextTarget {
  if (hit === null) return { kind: "empty" };
  const { part, patch } = hit;
  const sample = part === SAMPLE_PART;
  const faceHeld = state.picked.faces[part]?.includes(hit.face) ?? false;
  const bodyHeld = state.picked.bodies.includes(part) || state.selectedPatch === patch;
  const faceMode = state.pickMode === "face" && hit.face >= 0;
  const inPick = faceMode ? faceHeld : bodyHeld;
  // A hit inside a pick of several bodies (or parts) is the pick: a body menu would offer Rename, Delete
  // and Hide, which act on one part, where the plan says the menu acts on the whole pick.
  if (inPick && isMultiPick(state.picked)) {
    return { kind: "pick", label: describe(state.picked, state.pickMode) ?? "" };
  }
  if (faceMode) return { kind: "face", part, patch, face: hit.face, inPick, sample };
  // Body mode, and a mesh without face ids in face mode; edge and point targets are T-175.
  return { kind: "body", part, patch, inPick, sample };
}

/**
 * The keyboard's target: the pick when it holds something, else empty. One body whose patch is known
 * is the body itself (so Rename and Delete apply to it); anything larger is `pick`.
 */
export function resolveKeyboardTarget(state: TargetState, describe: DescribePick): ContextTarget {
  const label = describe(state.picked, state.pickMode);
  if (label === null) return { kind: "empty" };
  const { picked, selectedPatch } = state;
  if (state.pickMode === "body" && picked.bodies.length === 1 && selectedPatch !== null) {
    const part = picked.bodies[0];
    return { kind: "body", part, patch: selectedPatch, inPick: true, sample: part === SAMPLE_PART };
  }
  return { kind: "pick", label };
}

/**
 * The CAD rule: what to pick before the menu opens, or null to keep the pick. Null for empty space, the
 * pick as a whole, a tree row, a target already in the pick, and in Edge, Point, Paint and Measure mode.
 */
export function selectionFor(
  target: ContextTarget,
  state: SelectionModes,
): { what: "body" | "face"; part: string; patch: string; face?: number } | null {
  if (state.pickMode === "edge" || state.pickMode === "point" || state.paintMode || state.measureMode) return null;
  if (target.kind === "body" && !target.inPick) return { what: "body", part: target.part, patch: target.patch };
  if (target.kind === "face" && !target.inPick) return { what: "face", part: target.part, patch: target.patch, face: target.face };
  return null;
}
