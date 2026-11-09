/**
 * The action context (T-171): everything an action's `label(ctx)` and `enabled(ctx)` may read, built
 * from plain values so that a test needs no store. The menus, the palette and the ribbon build one
 * `ContextInput` from `useStore.getState()` and `useWorkspace.getState()` (T-172 does) and ask the
 * action table (`enabledFor`, `labelFor` in actions.ts) what each entry says and whether it can run.
 *
 * Part and patch are different things: `activePart` is a part node id (from the pick), `activePatch` a
 * patch name (the selected patch, or the patch of the hit the menu was opened on). `pickFace` leaves
 * `selectedPatch` null, so in face mode the patch comes from the target. This module imports only types.
 */
import type { NavMode, ViewMode } from "../state/store";
import type { Picked, PickMode } from "../state/pick";
import type { ContextTarget } from "./contextTarget";

/** The store fields the context reads, as values. `bodies`, `faces` and `pickLabel` are `readablePick` and `describePick`. */
export interface ContextInput {
  /** A project is open (`project !== null`). */
  project: boolean;
  canUndo: boolean;
  canRedo: boolean;
  pickMode: PickMode;
  picked: Picked;
  /** Whether every body in play is already left out of the simulation (T-221); computed by the caller. */
  simulateOut: boolean;
  /** `readablePick(picked, pickMode)`: bodies and face ids an operation can read (the sample part is not counted). */
  bodies: number;
  faces: number;
  /** `describePick(picked, pickMode)`. */
  pickLabel: string | null;
  selectedPatch: string | null;
  hiddenPatches: readonly string[];
  isolatedPatch: string | null;
  /** The length of `pickHistory`: how many earlier picks `selection.previous` can bring back. */
  previousPicks: number;
  navMode: NavMode;
  orbitMode: boolean;
  viewMode: ViewMode;
  orthographic: boolean;
  /** The workspace's `viewport.grid`. */
  grid: boolean;
  /** `section !== null`. */
  sectionOn: boolean;
  paintMode: boolean;
  measureMode: boolean;
  /** `panelShown(layout, "tree")`. */
  treeShown: boolean;
}

export interface ActionContext {
  project: boolean;
  canUndo: boolean;
  canRedo: boolean;
  pickMode: PickMode;
  bodies: number;
  faces: number;
  pickLabel: string | null;
  /** A part node id: the last picked body, else the last part holding picked faces. */
  activePart: string | null;
  /** A patch name: the target's patch when the menu was opened on a hit, else `selectedPatch`. */
  activePatch: string | null;
  /** The active patch is hidden. */
  hidden: boolean;
  /** Something is isolated (what `toggleIsolate` undoes). */
  isolated: boolean;
  /** Something is hidden or isolated. */
  anyHidden: boolean;
  /**
   * Every body in play is ALREADY left out of the simulation, so the entry offers to put them back
   * (T-221). `false` when any of them is in, which is the reading that matches the label: "Leave out"
   * while there is something left to leave out.
   */
  simulateOut: boolean;
  /** The target's patch is hidden (false when the target has no patch). */
  hiddenTarget: boolean;
  /** Earlier picks `selection.previous` can bring back. */
  previousPicks: number;
  navMode: NavMode;
  orbitMode: boolean;
  viewMode: ViewMode;
  orthographic: boolean;
  grid: boolean;
  sectionOn: boolean;
  paintMode: boolean;
  measureMode: boolean;
  treeShown: boolean;
  target: ContextTarget;
}

/** The part a face pick acts on: the last part still holding faces (the store's `activeFacePart`). */
function lastFacePart(picked: Picked): string | null {
  const held = Object.keys(picked.faces).filter((part) => picked.faces[part].length > 0);
  return held.length > 0 ? held[held.length - 1] : null;
}

/** The part node id the pick acts on, or null. */
export function activePartOf(picked: Picked): string | null {
  return picked.bodies.length > 0 ? picked.bodies[picked.bodies.length - 1] : lastFacePart(picked);
}

export function actionContextOf(input: ContextInput, target: ContextTarget): ActionContext {
  const onHit = target.kind === "body" || target.kind === "face";
  const activePatch = onHit ? target.patch : input.selectedPatch;
  return {
    project: input.project,
    canUndo: input.canUndo,
    canRedo: input.canRedo,
    pickMode: input.pickMode,
    bodies: input.bodies,
    faces: input.faces,
    pickLabel: input.pickLabel,
    activePart: activePartOf(input.picked),
    activePatch,
    hidden: activePatch !== null && input.hiddenPatches.includes(activePatch),
    isolated: input.isolatedPatch !== null,
    anyHidden: input.hiddenPatches.length > 0 || input.isolatedPatch !== null,
    // **Read from the caller**, because this module imports only types and the flag depends on
    // the tree. `ContextMenu` computes it beside the other store reads.
    simulateOut: input.simulateOut,
    hiddenTarget: onHit && input.hiddenPatches.includes(target.patch),
    previousPicks: input.previousPicks,
    navMode: input.navMode,
    orbitMode: input.orbitMode,
    viewMode: input.viewMode,
    orthographic: input.orthographic,
    grid: input.grid,
    sectionOn: input.sectionOn,
    paintMode: input.paintMode,
    measureMode: input.measureMode,
    treeShown: input.treeShown,
    target,
  };
}
