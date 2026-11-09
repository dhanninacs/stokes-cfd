/**
 * What an assign applies to, and the one `boundary.assign` that does it (T-061). Pure functions
 * over the live pick, the selected patch and the tree, so `AssignMenu.tsx` only renders and issues
 * the command and the tests read the decision without a renderer.
 *
 * One assign is one command, so it is one undo step and one journal record (`boundary.assign` makes
 * whatever is missing: the tags group, the tag, the region with its tag and `boundaries` group).
 * The target is, in order: the live pick as `members`; else the boundary the selected patch already
 * has, by path (a plain group from before typed boundaries is made typed in place, which is how the
 * demo's own boundaries keep their names); else the part whole, when the patch is that part's only
 * patch. Nothing is made first, and the target never grows past what is picked: a pick the server
 * cannot take is refused with a reason rather than widened to a whole patch.
 */

import type { ConditionKind, NodeSummary } from "../api/types";
import type { PatchMeta } from "../viewport/geometry";
import { describePick, findByPath, pickAsMembers, type PickMembers, type Picked, type PickMode } from "../state/store";
import { boundaryPath } from "./assignBoundary";

/** What `boundary.assign` takes as `target`: the members of a pick, or a tag or boundary by path. */
export type AssignValue = PickMembers["members"] | string;

export interface AssignTarget {
  /** What the command assigns to, or null when nothing can be assigned. */
  value: AssignValue | null;
  /** What it applies to, in one line ("3 faces on 2 parts", the patch's name), "" when nothing. */
  label: string;
  /** Why nothing can be assigned; said when the action is pressed. */
  refused: string | null;
}

/** The pick and the selection as `useAssignMenu` reads them from the store. */
export interface AssignState {
  picked: Picked;
  pickMode: PickMode;
  selectedPatch: string | null;
  geometry: readonly PatchMeta[];
  tree: NodeSummary | null;
}

/** One assign: the command and its parameters, and nothing else. */
export interface AssignCommand {
  method: string;
  params: Record<string, unknown>;
}

const NOTHING_PICKED = "Pick faces or bodies first, or select a patch.";
const NO_NODE = "The sample part has no node in the project, so it takes no condition: pick an imported part, or select a patch with a boundary.";
const MANY_PATCHES = "This part has several patches, and a condition covers a whole part: pick the faces of one patch.";
const UNDRAWN = "This patch is not drawn, so it has no faces to assign.";

/** The parts of the pick that `boundary.assign` can name, holding faces only. */
function heldParts(picked: Picked, mode: PickMode): string[] {
  if (mode === "body") return [...picked.bodies];
  return Object.entries(picked.faces)
    .filter(([, ids]) => ids.length > 0)
    .map(([part]) => part);
}

/** The part node of a patch that is that part's only patch; null otherwise. */
function partOf(geometry: readonly PatchMeta[], patch: string): string | null {
  const own = geometry.filter((g) => g.name === patch);
  if (own.length !== 1 || own[0].part === null) return null;
  return geometry.filter((g) => g.part === own[0].part).length === 1 ? own[0].part : null;
}

/** Why a patch with no boundary of its own and no single part is not assignable. */
function patchRefusal(geometry: readonly PatchMeta[], patch: string): string {
  const own = geometry.find((g) => g.name === patch);
  if (own === undefined) return UNDRAWN;
  return own.part === null ? NO_NODE : MANY_PATCHES;
}

/**
 * What an assign works on: the live pick first, else the selected patch's own boundary, else the
 * part that patch belongs to. A nonempty pick that `boundary.assign` cannot take (the sample part
 * has no node) is refused, never widened to the patch under it; `value` is then null.
 */
export function assignTarget(state: AssignState): AssignTarget {
  const label = describePick(state.picked, state.pickMode) ?? "";
  const pick = pickAsMembers(state.picked, state.pickMode);
  if (pick !== null) {
    // A pick that also holds parts no member list can name would lose them: refuse rather than
    // assign to part of what is picked.
    if (pick.members.length < heldParts(state.picked, state.pickMode).length) {
      return { value: null, label, refused: NO_NODE };
    }
    return { value: pick.members, label, refused: null };
  }
  if (label !== "") return { value: null, label, refused: NO_NODE }; // something is picked and no target names it
  const patch = state.selectedPatch;
  if (patch === null) return { value: null, label: "", refused: NOTHING_PICKED };
  const existing = findByPath(state.tree, boundaryPath(patch));
  if (existing !== null) return { value: existing.path, label: patch, refused: null };
  const part = partOf(state.geometry, patch);
  if (part === null) return { value: null, label: patch, refused: patchRefusal(state.geometry, patch) };
  return { value: [{ part, faces: "all" }], label: patch, refused: null };
}

/** The one command an assign is: `boundary.assign` with the target, the kind and its values. */
export function assignCommand(value: AssignValue, kind: ConditionKind, values: Record<string, unknown>): AssignCommand {
  const params: Record<string, unknown> = { kind: kind.id, target: value };
  if (Object.keys(values).length > 0) params.values = values;
  return { method: "boundary.assign", params };
}