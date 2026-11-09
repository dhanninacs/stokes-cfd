/**
 * Actions: what a button, a key and the ribbon all trigger, by the action ids of the workspace's
 * `shortcuts` section (docs/engineering/workspace-schema.md). The viewport helpers below read the
 * store at call time, so the islands, the shortcuts and the tooltips all say and do the same thing.
 *
 * The action bus: `runAction(id)` runs the handler a component registered for the id
 * (`registerAction`), else the built-in one. The keymap (workspace/keymap.ts) resolves a chord to ids
 * and calls this; so do the ribbon, the command palette and the Customise overlay. An action id
 * without a handler (a plug-in's) does nothing.
 *
 * The table also says what each action is called and whether it can run now (T-171): `group`, `icon`,
 * `label(ctx)` and `enabled(ctx)` on the spec, read through `labelFor` and `enabledFor`. The context
 * menu, the File menu, the palette and the ribbon read that one table, so an action is defined once:
 * its id, its label, its group, whether it is enabled and why not. `enabled` answers `true` or the
 * reason, one sentence with no full stop. `ActionContext` is pure (app/actionContext.ts).
 *
 * The project actions (`project.save`, `.saveAs`, `.open`, `.importStep`) belong to ProjectMenu, which
 * registers them while it is mounted: with the project menu hidden from the top bar they do nothing.
 */
import type { LucideIcon } from "lucide-react";
import type { NodeSummary } from "../api/types";
import { NO_LINE, isLinePicking } from "../panels/lineModel";
import { STAGES, activeFacePart, findNode, useStore, type NavMode, type StokesState, type ViewMode } from "../state/store";
import { useWorkspace } from "../workspace/store";
import type { ActionContext } from "./actionContext";
import { SAMPLE_PART } from "./contextTarget";
import { PICK_BODY, PICK_TWO_BODIES } from "./prepareEntries";

export const VIEW_MODES: { id: ViewMode; label: string }[] = [
  { id: "shaded", label: "Shaded" },
  { id: "edges", label: "Shaded + edges" },
  { id: "wireframe", label: "Wireframe" },
];

// `comingSoon` used to live here, and its docstring was a running list of the tools that were not
// built: Section view last, Measure before it. **Both are now real** (Measure in T-113, Section view
// in T-065), so the helper had no callers left and its comment had become a false statement about our
// own code — the same class of error it was written to fix. Deleted rather than left as an unused
// export that a later reader would take as current.
//
// What genuinely is not built names a milestone where one exists (the Run button names M3) and
// invents none where it does not: a stub must not promise a release that is already behind us.

/**
 * The mouse schemes, in the order the Modes island's Navigation menu offers them (ADR-0021 3.4).
 *
 * Every scheme rotates, pans and zooms from anywhere in the viewport; they differ in WHICH button
 * does what, because there is no single convention: CAD tools rotate on the middle button, CFD
 * post-processors on the left. The bindings are the names of well-known tools on purpose, so a user
 * can pick the one their hands already know.
 *
 * `paraview` ("Orbit") is the default since 2026-11-05: a left drag rotates from anywhere, which is
 * what a first-time user tries first (the hint says "Drag to orbit"), and a click still selects. `cad`
 * stays for people whose hands expect the middle button to rotate and the left to draw the box.
 */
export const NAV_MODES: { id: NavMode; label: string; summary: string }[] = [
  {
    id: "cad",
    label: "CAD",
    summary: "Middle-drag rotates. Right-drag pans. Wheel zooms. Left selects, or drags the box.",
  },
  {
    id: "paraview",
    label: "Orbit (ParaView)",
    summary: "Left-drag rotates anywhere. Middle- or right-drag pans. Wheel zooms. Shift-drag draws the box.",
  },
  {
    id: "blender",
    label: "Blender",
    summary: "Middle-drag rotates. Shift+middle pans. Wheel zooms. Left selects or drags the box.",
  },
  {
    id: "toggle",
    label: "Select / Orbit",
    summary: "Middle-drag rotates. Right-drag pans. The left button follows the switch below.",
  },
];

/**
 * Pick a mouse scheme: the viewport now, and the preference for next time.
 *
 * **Both, because one without the other is a bug either way** (claude-win2's 1330 answer). The
 * viewport keeps the scheme in session state (`setNavMode`), and the workspace document is the
 * REMEMBERED preference (`preferences.mouse.scheme`) - `viewDefaults.applyMouse` syncs document ->
 * store on load and on every change, and never the other way.
 *
 * So a menu that called only `setNavMode` changed the viewport until reload and then silently
 * reverted - measured before this: the Settings row remembered and the menu did not, which is the
 * same one-control-two-behaviours shape as the tree's eye.
 *
 * **One function for every caller** (the viewport menu, the empty-space menu, the cycle chord and
 * the toolbar) so the two halves cannot drift apart again.
 */
export function setMouseScheme(mode: NavMode): void {
  useStore.getState().setNavMode(mode);
  // Fire and forget: a workspace that cannot be written still leaves the session correct, and a
  // toast about a preference the user just chose would be noise. `setValue` reports its own errors.
  void useWorkspace.getState().setValue(["preferences", "mouse", "scheme"], mode, { silent: true });
}

/** The next mouse scheme, then around again. */
export function cycleNavMode(): void {
  const s = useStore.getState();
  const at = NAV_MODES.findIndex((m) => m.id === s.navMode);
  setMouseScheme(NAV_MODES[(at + 1) % NAV_MODES.length].id);
}

/** Isolate the selected patch; when something is isolated already, show everything again. */
export function toggleIsolate(): void {
  const s = useStore.getState();
  if (s.isolatedPatch) s.isolate(null);
  else if (s.selectedPatch) s.isolate(s.selectedPatch);
  else s.pushToast("info", "Select a patch to isolate it.");
}

export function toggleHideSelected(): void {
  const s = useStore.getState();
  if (s.selectedPatch) s.toggleHidden(s.selectedPatch);
}

/**
 * Leave the selected bodies out of the simulation, or put them back (T-221).
 *
 * **Through `geometry.simulate`, the one place the flag changes**, so this menu entry, the tree's
 * toggle and the tree's row menu cannot disagree about what the switch does - and the change is one
 * undo step and one journal record.
 *
 * **Parts, and the groups that hold them.** The selection is bodies or the parts beneath a selected
 * group; a selection with no part in it does nothing rather than erroring, because the menu entry is
 * only offered when there is something it can act on.
 */
export function toggleSimulateSelected(): void {
  const s = useStore.getState();
  const ids = simulateTargets(s);
  if (ids.length === 0) return;
  const anyIn = ids.some((id) => partSimulate(s.tree, id) !== false);
  void s.execute("geometry.simulate", { nodes: ids, simulate: !anyIn }).catch(() => undefined);
}

/** The parts a simulate action would act on: the picked bodies, or a selected group's parts. */
function simulateTargets(s: StokesState): string[] {
  const ids = new Set<string>(s.picked.bodies);
  if (s.selectedId !== null) {
    const node = findNode(s.tree, s.selectedId);
    // A group stands for its parts, which is the sentence a user says about an assembly.
    if (node?.type === "group") {
      for (const inside of node.children ?? []) if (inside.type === "part") ids.add(inside.id);
    } else if (node?.type === "part") {
      ids.add(node.id);
    }
  }
  return [...ids];
}

/** Whether a part is in the simulation; `undefined` for a node that is not a part. */
function partSimulate(tree: NodeSummary | null, id: string): boolean | undefined {
  const node = findNode(tree, id);
  if (node?.type !== "part") return undefined;
  return node.params?.simulate !== false;
}

export function toggleProjection(): void {
  const s = useStore.getState();
  s.setOrthographic(!s.orthographic);
}

/** Shaded, then shaded with edges, then wireframe, then around again. */
export function cycleViewMode(): void {
  const s = useStore.getState();
  const at = VIEW_MODES.findIndex((m) => m.id === s.viewMode);
  s.setViewMode(VIEW_MODES[(at + 1) % VIEW_MODES.length].id);
}

// ---------------------------------------------------------------------------------------------- the action table
export interface ActionSpec {
  id: string;
  /** What the action does, for menus, the ribbon and a future shortcut editor. */
  title: string;
  /** `tree` actions are the tree's keys and fire only while it has the focus (treeKeyboard.ts). */
  context: "global" | "tree";
  /** Fires while a text field has the focus (chords with Ctrl, as before); plain keys never do. */
  whileTyping?: boolean;
  /** Fires again while the key is held (undo and redo step on). */
  repeat?: boolean;
  /** Where a menu lists the action (T-171). */
  group?: ActionGroup;
  /** The icon a menu or the ribbon draws beside the label. */
  icon?: LucideIcon;
  /** The dynamic word: "Hide" or "Show", "Isolate" or "Show all". Absent: the title is the label. */
  label?: (ctx: ActionContext) => string;
  /** `true` when the action can run now, else the reason, one sentence with no full stop. Absent: always enabled. */
  enabled?: (ctx: ActionContext) => true | string;
}

export type ActionGroup = "view" | "selection" | "setup" | "prepare" | "edit" | "project" | "app";

const spec = (id: string, title: string, extra: Partial<ActionSpec> = {}): ActionSpec => ({ id, title, context: "global", ...extra });

// The reasons (T-171). Each is one sentence with no full stop; the Prepare ones are the words of prepareEntries.ts.
const hasSelection = (c: ActionContext): boolean => c.activePart !== null || c.bodies + c.faces > 0 || c.target.kind === "body" || c.target.kind === "face";
const needProject = (c: ActionContext): true | string => (c.project ? true : "Open a project first");
const needBody = (why: string) => (c: ActionContext): true | string => (hasSelection(c) ? true : why);
const needBodies = (count: number, why: string) => (c: ActionContext): true | string => (c.bodies >= count ? true : why);

/** Rename and Delete act on one node: a body or a tree row, never a skeleton node, never the pick as a whole. */
function oneNode(verb: "Rename" | "Delete"): (c: ActionContext) => true | string {
  return (c) => {
    const t = c.target;
    if (t.kind === "node") return t.structural ? `${t.name ?? "This node"} is part of the project skeleton` : true;
    if (t.kind === "pick") return `${verb} one body at a time`;
    if (t.kind === "body" || t.kind === "face") return t.sample ? "The sample part has no node" : true;
    return "Select something in the tree first";
  };
}

/** Copy name and Copy path read a node: something must be selected, and the sample part has none. */
function hasNode(c: ActionContext): true | string {
  const t = c.target;
  if (t.kind === "empty") return "Select something first";
  if ((t.kind === "body" || t.kind === "face") && t.sample) return "The sample part has no node";
  return true;
}

/** Every action id of the default `shortcuts` section, in the order the schema lists them. */
export const ACTIONS: readonly ActionSpec[] = [
  spec("stage.1", "Go to the Geometry stage"),
  spec("stage.2", "Go to the Mesh stage"),
  spec("stage.3", "Go to the Physics stage"),
  spec("stage.4", "Go to the Solve stage"),
  spec("stage.5", "Go to the Results stage"),
  spec("stage.6", "Go to the Explore stage"),
  spec("view.fit", "Fit the model to the view", { group: "view" }),
  spec("view.fitAll", "Fit every body, the fluid included", { group: "view" }),
  // The label names what choosing the entry does, as "Hide" does for a visible body.
  spec("view.projection", "Switch between perspective and orthographic", { group: "view", label: (c) => (c.orthographic ? "Perspective" : "Orthographic") }),
  spec("view.mode.cycle", "Cycle shaded, shaded with edges, wireframe", { group: "view" }),
  spec("view.nav.cycle", "Cycle the mouse scheme: CAD, Orbit, Blender, Select/Orbit", { group: "view" }),
  spec("view.section", "Section view", { group: "view" }),
  spec("view.measure", "Measure", { group: "view" }),
  spec("view.probe", "Probe: read a field at a point", { group: "view" }),
  spec("selection.isolate", "Isolate the selected patch, or show all again", {
    group: "selection",
    label: (c) => (c.isolated ? "Show all" : "Isolate"),
    enabled: (c) => (c.activePatch !== null || c.isolated ? true : "Select a body to isolate it"),
  }),
  spec("selection.hide", "Hide or show the selected patch", {
    group: "selection",
    label: (c) => (c.hidden ? "Show" : "Hide"),
    enabled: (c) => (c.activePatch !== null ? true : "Select a body to hide it"),
  }),
  // **The viewport menu's entry** (the workflow-v2 plan §2, T-221's third entry point). It sits in
  // the same group as Isolate and Hide, which is where a user looks for "make this not be there".
  spec("selection.simulate", "Leave the selected bodies out of the simulation", {
    group: "selection",
    label: (c) => (c.simulateOut ? "Use in simulation" : "Leave out of simulation"),
    enabled: (c) => (c.bodies > 0 || c.activePart !== null ? true : "Select a body first"),
  }),
  spec("selection.clear", "Clear the selection and close the palette", {
    group: "selection",
    enabled: (c) => (c.pickLabel !== null || c.activePart !== null ? true : "Nothing is picked"),
  }),
  spec("selection.mode", "Toggle body and face picking", { group: "selection" }),
  // Naming faces (ADR-0021): no default chord; ContextToolbar.tsx installs the handlers while it is mounted.
  // **`Tag selection…`, one name everywhere** (claude-win2's 2000 item 3): the palette, the
  // toolbar tooltip and the row menu all say it, and the ellipsis says a dialog follows. The
  // old label was "Name…", which named neither the thing made nor the thing named.
  spec("selection.name", "Tag selection…", {
    group: "selection",
    enabled: (c) => (c.bodies + c.faces > 0 ? true : c.activePart === SAMPLE_PART ? "The sample part has no node" : "Pick faces or bodies of a part first"),
  }),
  spec("selection.grow", "Grow the selection along smooth surfaces", {
    group: "selection",
    enabled: (c) => (c.faces > 0 ? true : "Grow works on picked faces: switch to Face and pick some"),
  }),
  // The last ten picks (ADR-0022 6.7), brought back one at a time; no default chord, the palette lists it.
  spec("selection.previous", "Previous selection", { group: "selection", enabled: (c) => (c.previousPicks > 0 ? true : "No earlier pick") }),
  spec("selection.paint", "Paint select: drag over faces to add them, Ctrl to remove them", { group: "selection" }),
  // The geometry operations (ADR-0021 8, T-029): the Prepare menu's entries as actions, with no
  // default chords. app/PrepareMenu.tsx installs the handlers while it is mounted.
  spec("prepare.enclosure", "Enclose the picked bodies", { group: "prepare", enabled: needBodies(1, PICK_BODY) }),
  spec("prepare.extractFluid", "Extract the fluid around a seed point", { group: "prepare", enabled: needBodies(1, PICK_BODY) }),
  spec("prepare.boolean", "Fuse, cut or intersect the picked bodies", { group: "prepare", enabled: needBodies(2, PICK_TWO_BODIES) }),
  spec("prepare.repair", "Heal, sew or solidify the picked bodies", {
    group: "prepare",
    enabled: (c) => (c.bodies >= 1 || c.faces > 0 ? true : "Repair needs a body, or the faces to remove"),
  }),
  spec("palette.open", "Open the command palette", { whileTyping: true, repeat: true, group: "app" }),
  spec("history.undo", "Undo", { repeat: true, group: "edit", enabled: (c) => (c.canUndo ? true : "Nothing to undo") }),
  spec("history.redo", "Redo", { repeat: true, group: "edit", enabled: (c) => (c.canRedo ? true : "Nothing to redo") }),
  spec("project.save", "Save the project", { whileTyping: true, group: "project", enabled: needProject }),
  spec("project.saveAs", "Save the project to a new folder", { whileTyping: true, group: "project", enabled: needProject }),
  // **`Save a copy`, not `Save as`** (the menu spec): the same dialog, but the session stays in the
  // original. It is ALLOWED while a run is in flight, which is why it is the entry the busy refusal
  // points at - so it must exist here for that sentence to name something reachable.
  spec("project.saveCopy", "Copy the project without switching to it", { whileTyping: true, group: "project", enabled: needProject }),
  spec("project.open", "Open a project", { whileTyping: true, group: "project" }),
  // **Close asks about unsaved work first** (menu spec rule 2), so its enabled state is only
  // "is there a project" - whether it is dirty is decided in the handler, where the prompt lives.
  spec("project.close", "Close the project", { whileTyping: true, group: "project", enabled: needProject }),
  spec("project.startScreen", "Go to the Start screen", { whileTyping: true, group: "project" }),
  spec("project.importStep", "Import a geometry file into the project", { whileTyping: true, group: "project", enabled: needProject }),
  // **The popover is `Appearance`** (claude-win2's 2000 item 4): "Display" meant the theme and
  // layout popover, the Shaded/Wireframe view mode, and the display units. Only this one is
  // renamed - the view mode and Units keep their own words.
  spec("display.settings", "Open Appearance", { whileTyping: true, group: "app" }),
  spec("workspace.customise", "Enter or leave Customise mode", { whileTyping: true, group: "app" }),
  /*
    **The two panel toggles** (the owner, via claude-win2 1930). They are `global`, so the palette and
    the keymap both reach them; the label is what the palette shows, and "Show or hide" says what the
    chord does rather than naming a state a user has to work out they are in.

    **Declared here as well as in the server's document**, which is the arrangement this file already
    has: the workspace document carries the CHORDS (so a user can rebind them and the server can
    validate them) and this list carries the descriptions the palette renders. A test compares the ids,
    which is what keeps the two from drifting.
  */
  spec("layout.toggleTree", "Show or hide the tree", { whileTyping: true, group: "app" }),
  spec("layout.toggleDetails", "Show or hide the Details pane", { whileTyping: true, group: "app" }),
  spec("layout.reset", "Reset the panel layout", { whileTyping: true, group: "app" }),
  spec("tree.rename", "Rename the selected node", { context: "tree" }),
  spec("tree.delete", "Delete the selected node", { context: "tree" }),
  spec("tree.menu", "Open the row menu", { context: "tree" }),
  // Explore (ADR-0016): no default chord; panels/explore/actions.ts installs the handlers when the app loads.
  spec("study.new", "New study…"),
  spec("study.generate", "Generate the design table of the study"),
  spec("study.update", "Update the design table: evaluate the points that are not done"),
  spec("study.addPoint", "Add a design point to the study"),
  spec("study.export", "Export the design table as CSV"),
  // Meshing (ADR-0024, T-211b): no default chord; panels/meshRun.ts installs the handler when the app loads. It says why when it cannot run, as the Prepare entries do.
  spec("mesh.generate", "Generate mesh"),
  // The guided-meshing stepper's chords (brief M-502): between the nine tasks. panels/MeshStepper
  // registers the handlers, reading the same tasks reply the stepper draws.
  spec("mesh.task.next", "Move to the next meshing task"),
  spec("mesh.task.previous", "Move to the previous meshing task"),
  // The menus (T-171, ux-menus). No default chords: new `area.verb` ids work before the defaults list them.
  // A comment names the task whose component registers the handler; the others run from the built-ins below.
  spec("context.menu", "Open the context menu", { group: "app" }), // T-172
  spec("app.settings", "Open Settings", { whileTyping: true, group: "app" }), // T-178
  spec("view.fitSelection", "Fit the selection to the view", { group: "view", enabled: needBody("Select a body first") }), // T-172
  spec("view.showAll", "Show every hidden body again", { group: "view", enabled: (c) => (c.anyHidden ? true : "Nothing is hidden") }),
  spec("view.from.front", "Look from the front", { group: "view" }), // the seven standard views: T-172
  spec("view.from.back", "Look from the back", { group: "view" }),
  spec("view.from.left", "Look from the left", { group: "view" }),
  spec("view.from.right", "Look from the right", { group: "view" }),
  spec("view.from.top", "Look from the top", { group: "view" }),
  spec("view.from.bottom", "Look from the bottom", { group: "view" }),
  spec("view.from.iso", "Look from the isometric corner", { group: "view" }),
  spec("selection.siblings", "Select the parts of the same file", { group: "selection", enabled: needBody("Select a body first") }),
  spec("selection.allFaces", "Select every face of the body", {
    group: "selection",
    enabled: (c) => (c.activePart === null ? "Select a body first" : c.activePart === SAMPLE_PART ? "The sample part has no face ids" : true),
  }),
  // "Clear" in a menu is not Esc's cascade (`selection.clear` above): it only empties the pick.
  spec("selection.clearPick", "Clear the pick", { group: "selection", enabled: (c) => (c.pickLabel !== null || c.activePart !== null ? true : "Nothing is picked") }),
  spec("edit.rename", "Rename…", { group: "edit", enabled: oneNode("Rename") }), // T-172
  spec("edit.delete", "Delete", { group: "edit", enabled: oneNode("Delete") }), // T-172
  spec("edit.copyName", "Copy name", { group: "edit", enabled: hasNode }), // T-172
  spec("edit.copyPath", "Copy path", { group: "edit", enabled: hasNode }), // T-172
];

export const ACTION_SPECS: ReadonlyMap<string, ActionSpec> = new Map(ACTIONS.map((a) => [a.id, a]));

// ---------------------------------------------------------------------------------------------- the bus
const registered = new Map<string, () => void>();

/** Install the handler for an action id; returns the call that removes it again. */
export function registerAction(id: string, run: () => void): () => void {
  registered.set(id, run);
  return () => {
    if (registered.get(id) === run) registered.delete(id);
  };
}

/** stage.N: the N-th stage of the project store's list; nothing when the store has no such stage yet. */
function stage(n: number): (() => void) | undefined {
  const s = STAGES[n - 1];
  return s === undefined ? undefined : () => useStore.getState().setStage(s);
}

/** The part node id the pick acts on now: the last picked body, else the part last picked from in face mode. */
function activePartNow(): string | null {
  const { picked } = useStore.getState();
  return picked.bodies.length > 0 ? picked.bodies[picked.bodies.length - 1] : activeFacePart(picked);
}

const BUILTIN: Record<string, (() => void) | undefined> = {
  "stage.1": stage(1),
  "stage.2": stage(2),
  "stage.3": stage(3),
  "stage.4": stage(4),
  "stage.5": stage(5),
  "stage.6": stage(6),
  "view.fit": () => useStore.getState().requestFit(),
  "view.fitAll": () => useStore.getState().requestFit(true),
  "view.projection": toggleProjection,
  "view.mode.cycle": cycleViewMode,
  "view.nav.cycle": cycleNavMode,
  // The section control is a popover the Modes island owns, so the action only asks it to open:
  // `section.request` is the bus between the chord (`S`) and the island, whose handler is installed
  // while that island is mounted — the same pattern `display.settings` uses for its popover.
  "view.section": () => useStore.getState().requestSection(),
  // **The probe is a point pick like Measure**, so it takes the click for the same reasons and the
  // two cannot both be waiting for one press.
  "view.probe": () => {
    const s = useStore.getState();
    const on = !s.probePick;
    if (on) {
      s.setMeasureMode(false);
      s.setPaintMode(false);
      // The line's two clicks ride the same pick: only one of the two can own the next click.
      if (isLinePicking(s.line.phase)) s.setLine(NO_LINE);
    }
    s.setProbePick(on);
  },
  "view.measure": () => {
    const s = useStore.getState();
    const on = !s.measureMode;
    // The probe is the other point pick: only one can own the next click.
    if (on) s.setProbePick(false);
    // One measuring mode at a time: paint also owns the left button, and two crosshair modes at once
    // is a state with no meaning the user can see. Turning measure on takes the click off paint.
    if (on) s.setPaintMode(false);
    s.setMeasureMode(on);
  },
  "selection.isolate": toggleIsolate,
  "selection.hide": toggleHideSelected,
  "selection.simulate": toggleSimulateSelected,
  "selection.clear": () => {
    const s = useStore.getState();
    // Esc closes what is open, one thing at a time: measure mode first (T-098), then paint mode
    // (T-043), then the operation card's seed pick, then the card itself (T-029). The pick stays
    // until a further Esc.
    if (s.measureMode) {
      s.setMeasureMode(false);
      return;
    }
    if (s.paintMode) {
      s.setPaintMode(false);
      return;
    }
    if (s.seedPick) {
      s.setSeedPick(false);
      return;
    }
    if (s.operationCard !== null) {
      s.closeOperationCard();
      return;
    }
    s.select(null);
    s.setPalette(false);
  },
  // Clear only the pick (the menus' "Clear"); `selection.clear` above is Esc's cascade and keeps its meaning.
  "selection.clearPick": () => useStore.getState().clearPick(),
  "view.showAll": () => useStore.setState({ hiddenPatches: [], isolatedPatch: null }),
  "selection.siblings": () => {
    const part = activePartNow();
    if (part !== null) useStore.getState().pickSiblings(part, "replace");
  },
  "selection.allFaces": () => {
    const part = activePartNow();
    if (part === null) return;
    const s = useStore.getState();
    // Never `pickAllFaces` under body mode: it would leave faces under body mode. In body mode the pick
    // is made a body and the mode switch expands it to every face and sets the mode.
    if (s.pickMode === "face") s.pickAllFaces(part, "replace");
    else {
      s.pickBody(part, "replace");
      useStore.getState().setPickMode("face");
    }
  },
  "selection.mode": () => {
    const s = useStore.getState();
    s.setPickMode(s.pickMode === "body" ? "face" : "body");
  },
  "selection.previous": () => useStore.getState().pickPrevious(),
  "selection.paint": () => {
    const s = useStore.getState();
    s.setPaintMode(!s.paintMode);
  },
  "palette.open": () => useStore.getState().setPalette(true),
  "history.undo": () => void useStore.getState().execute("history.undo").catch(() => undefined),
  "history.redo": () => void useStore.getState().execute("history.redo").catch(() => undefined),
  "display.settings": () => {
    const w = useWorkspace.getState();
    w.setDisplayOpen(!w.displayOpen);
  },
  // The Settings dialog (T-178). It toggles as Appearance does, though behind the open dialog the
  // keymap does not run, so in practice the chord opens it and Escape or Done closes it.
  "app.settings": () => {
    const w = useWorkspace.getState();
    w.setSettingsOpen(!w.settingsOpen);
  },
  "workspace.customise": () => {
    const w = useWorkspace.getState();
    w.setCustomising(!w.customising);
  },
};

/** True when something handles the id now: a registered handler, or a built-in one. */
export function hasAction(id: string): boolean {
  return registered.has(id) || BUILTIN[id] !== undefined;
}

/** Run an action; false when nothing handles the id. */
export function runAction(id: string): boolean {
  const run = registered.get(id) ?? BUILTIN[id];
  if (!run) return false;
  run();
  return true;
}

// ---------------------------------------------------------------------------------------------- what an action says now
/** `true` when the action can run in `ctx`, else the reason it cannot. An unknown id is not an action. */
export function enabledFor(id: string, ctx: ActionContext): true | string {
  const found = ACTION_SPECS.get(id);
  if (found === undefined) return "Unknown action";
  return found.enabled?.(ctx) ?? true;
}

/** The label a menu shows for the action in `ctx`: the dynamic word when it has one, else the title. */
export function labelFor(id: string, ctx: ActionContext): string {
  const found = ACTION_SPECS.get(id);
  if (found === undefined) return id;
  return found.label?.(ctx) ?? found.title;
}
