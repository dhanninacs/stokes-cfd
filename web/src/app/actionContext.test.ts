/**
 * The action table's `enabled(ctx)` and `label(ctx)` (T-171): one table says what each menu entry is
 * called and whether it can run now, and why not. Pure values, no DOM.
 */
import { describe, expect, it, vi } from "vitest";

// `actions.ts` imports the store, which reads `window` as it loads and this test has no DOM; the hook
// only reads it, so the global itself stands in. Hoisted, so it runs before the imports.
vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

import { NO_PICK, type Picked } from "../state/pick";
import { actionContextOf, type ActionContext, type ContextInput } from "./actionContext";
import { ACTIONS, ACTION_SPECS, enabledFor, labelFor } from "./actions";
import type { ContextTarget } from "./contextTarget";

const input = (over: Partial<ContextInput> = {}): ContextInput => ({
  project: true,
  canUndo: false,
  canRedo: false,
  pickMode: "body",
  picked: NO_PICK,
  bodies: 0,
  faces: 0,
  pickLabel: null,
  selectedPatch: null,
  hiddenPatches: [],
  isolatedPatch: null,
  previousPicks: 0,
  navMode: "cad",
  orbitMode: false,
  viewMode: "shaded",
  orthographic: false,
  grid: true,
  sectionOn: false,
  paintMode: false,
  measureMode: false,
  treeShown: true,
  ...over,
});

const EMPTY: ContextTarget = { kind: "empty" };
const ctx = (over: Partial<ContextInput> = {}, target: ContextTarget = EMPTY): ActionContext => actionContextOf(input(over), target);
const bodyPick = (...ids: string[]): Picked => ({ bodies: ids, faces: {}, edges: {}, vertices: {} });
const facePick = (map: Record<string, number[]>): Picked => ({ bodies: [], faces: map, edges: {}, vertices: {} });
/** A single body picked in the viewport, as the store leaves it. */
const oneBody = (over: Partial<ContextInput> = {}): ActionContext =>
  ctx({ picked: bodyPick("p1"), bodies: 1, pickLabel: "1 body", selectedPatch: "patch1", ...over });

describe("actionContextOf", () => {
  it("takes the part from the pick and the patch from the selected patch", () => {
    const c = oneBody();
    expect(c.activePart).toBe("p1");
    expect(c.activePatch).toBe("patch1");
  });

  it("takes the patch from the target in face mode, where no patch is selected", () => {
    const target: ContextTarget = { kind: "face", part: "p1", patch: "patchOfFace", face: 2, inPick: true, sample: false };
    const c = ctx({ pickMode: "face", picked: facePick({ p1: [2] }), faces: 1, pickLabel: "1 face on 1 part", hiddenPatches: ["patchOfFace"] }, target);
    expect(c.activePart).toBe("p1");
    expect(c.activePatch).toBe("patchOfFace");
    expect(c.hidden).toBe(true);
    expect(c.hiddenTarget).toBe(true);
  });

  it("does not derive the part from the patch name or the patch from the part", () => {
    const c = ctx({ picked: facePick({ p1: [2] }), pickMode: "face", faces: 1, pickLabel: "1 face on 1 part" });
    expect(c.activePart).toBe("p1");
    expect(c.activePatch).toBeNull();
  });

  it("says whether anything is hidden or isolated", () => {
    expect(ctx().anyHidden).toBe(false);
    expect(ctx({ hiddenPatches: ["x"] }).anyHidden).toBe(true);
    const c = ctx({ isolatedPatch: "x" });
    expect(c.anyHidden).toBe(true);
    expect(c.isolated).toBe(true);
  });
});

describe("enabledFor", () => {
  it("is a reason for an id that is no action", () => {
    expect(typeof enabledFor("nope.nothing", ctx())).toBe("string");
  });

  it("is true for an action with no rule", () => {
    expect(enabledFor("view.fit", ctx())).toBe(true);
  });

  it("explains Isolate when nothing is selected, and allows it with a patch or when something is isolated", () => {
    expect(enabledFor("selection.isolate", ctx())).toBe("Select a body to isolate it");
    expect(enabledFor("selection.isolate", oneBody())).toBe(true);
    expect(enabledFor("selection.isolate", ctx({ isolatedPatch: "x" }))).toBe(true);
  });

  it("explains Hide, and takes the patch of a face target (face mode selects no patch)", () => {
    expect(enabledFor("selection.hide", ctx())).toBe("Select a body to hide it");
    const target: ContextTarget = { kind: "face", part: "p1", patch: "pf", face: 0, inPick: true, sample: false };
    expect(enabledFor("selection.hide", ctx({ pickMode: "face", picked: facePick({ p1: [0] }), faces: 1 }, target))).toBe(true);
  });

  it("explains Show all when nothing is hidden and allows it when something is hidden or isolated", () => {
    expect(enabledFor("view.showAll", ctx())).toBe("Nothing is hidden");
    expect(enabledFor("view.showAll", ctx({ isolatedPatch: "x" }))).toBe(true);
    expect(enabledFor("view.showAll", ctx({ hiddenPatches: ["x"] }))).toBe(true);
  });

  it("follows the project's undo and redo state", () => {
    expect(enabledFor("history.undo", ctx())).toBe("Nothing to undo");
    expect(enabledFor("history.undo", ctx({ canUndo: true }))).toBe(true);
    expect(enabledFor("history.redo", ctx())).toBe("Nothing to redo");
    expect(enabledFor("history.redo", ctx({ canRedo: true }))).toBe(true);
  });

  it("names the sample part's reasons", () => {
    const sample = { picked: bodyPick("@sample"), pickLabel: "1 body", selectedPatch: "sample" };
    expect(enabledFor("selection.name", ctx(sample))).toBe("The sample part has no node");
    expect(enabledFor("selection.allFaces", ctx(sample))).toBe("The sample part has no face ids");
    expect(enabledFor("selection.name", ctx())).toBe("Pick faces or bodies of a part first");
    expect(enabledFor("selection.name", oneBody())).toBe(true);
  });

  it("needs a body for the selection actions", () => {
    expect(enabledFor("selection.siblings", ctx())).toBe("Select a body first");
    expect(enabledFor("selection.allFaces", ctx())).toBe("Select a body first");
    expect(enabledFor("view.fitSelection", ctx())).toBe("Select a body first");
    expect(enabledFor("selection.siblings", oneBody())).toBe(true);
    expect(enabledFor("selection.allFaces", oneBody())).toBe(true);
    expect(enabledFor("view.fitSelection", oneBody())).toBe(true);
  });

  it("grows only a face pick, and goes back only when there is an earlier pick", () => {
    expect(enabledFor("selection.grow", oneBody())).toBe("Grow works on picked faces: switch to Face and pick some");
    expect(enabledFor("selection.grow", ctx({ pickMode: "face", picked: facePick({ p1: [1] }), faces: 1 }))).toBe(true);
    expect(enabledFor("selection.previous", ctx())).toBe("No earlier pick");
    expect(enabledFor("selection.previous", ctx({ previousPicks: 2 }))).toBe(true);
  });

  it("clears the pick only when there is one, under its own id", () => {
    expect(ACTION_SPECS.get("selection.clearPick")?.group).toBe("selection");
    expect(enabledFor("selection.clearPick", ctx())).toBe("Nothing is picked");
    expect(enabledFor("selection.clearPick", oneBody())).toBe(true);
    expect(enabledFor("selection.clear", ctx())).toBe("Nothing is picked");
  });

  it("words the Prepare reasons as the Prepare menu does", () => {
    expect(enabledFor("prepare.enclosure", ctx())).toBe("Pick a body first");
    expect(enabledFor("prepare.boolean", oneBody())).toBe("Boolean needs two bodies: Shift-click a second one");
    expect(enabledFor("prepare.boolean", ctx({ bodies: 2 }))).toBe(true);
    expect(enabledFor("prepare.repair", ctx())).toBe("Repair needs a body, or the faces to remove");
    expect(enabledFor("prepare.repair", ctx({ pickMode: "face", faces: 3 }))).toBe(true);
  });

  it("needs a project for the project actions that write", () => {
    expect(enabledFor("project.save", ctx({ project: false }))).toBe("Open a project first");
    expect(enabledFor("project.importStep", ctx({ project: false }))).toBe("Open a project first");
    expect(enabledFor("project.open", ctx({ project: false }))).toBe(true);
    expect(enabledFor("project.save", ctx())).toBe(true);
  });

  describe("edit.rename and edit.delete", () => {
    const target = (t: ContextTarget) => ctx({}, t);

    it("need a target", () => {
      expect(enabledFor("edit.rename", ctx())).toBe("Select something in the tree first");
      expect(enabledFor("edit.delete", ctx())).toBe("Select something in the tree first");
    });

    it("refuse the pick as a whole, one body at a time", () => {
      const pick: ContextTarget = { kind: "pick", label: "3 bodies" };
      expect(enabledFor("edit.delete", target(pick))).toBe("Delete one body at a time");
      expect(enabledFor("edit.rename", target(pick))).toBe("Rename one body at a time");
    });

    it("refuse a skeleton node by name, and allow any other node", () => {
      const skeleton: ContextTarget = { kind: "node", nodeId: "n1", nodeType: "group", structural: true, name: "physics" };
      expect(enabledFor("edit.rename", target(skeleton))).toBe("physics is part of the project skeleton");
      const plain: ContextTarget = { kind: "node", nodeId: "n2", nodeType: "part", structural: false };
      expect(enabledFor("edit.delete", target(plain))).toBe(true);
    });

    it("allow a body, and refuse the sample part", () => {
      const body: ContextTarget = { kind: "body", part: "p1", patch: "pa", inPick: true, sample: false };
      expect(enabledFor("edit.rename", target(body))).toBe(true);
      expect(enabledFor("edit.rename", target({ ...body, part: "@sample", sample: true }))).toBe("The sample part has no node");
    });
  });

  it("copies a name or a path only with something selected", () => {
    expect(enabledFor("edit.copyName", ctx())).toBe("Select something first");
    expect(enabledFor("edit.copyPath", ctx())).toBe("Select something first");
    expect(enabledFor("edit.copyName", ctx({}, { kind: "pick", label: "2 bodies" }))).toBe(true);
  });
});

describe("labelFor", () => {
  it("reads Hide, then Show once the active patch is hidden", () => {
    expect(labelFor("selection.hide", oneBody())).toBe("Hide");
    expect(labelFor("selection.hide", oneBody({ hiddenPatches: ["patch1"] }))).toBe("Show");
  });

  it("reads Isolate, then Show all once something is isolated", () => {
    expect(labelFor("selection.isolate", oneBody())).toBe("Isolate");
    expect(labelFor("selection.isolate", oneBody({ isolatedPatch: "patch1" }))).toBe("Show all");
  });

  it("names the projection that choosing the entry gives", () => {
    expect(labelFor("view.projection", ctx())).toBe("Orthographic");
    expect(labelFor("view.projection", ctx({ orthographic: true }))).toBe("Perspective");
  });

  it("falls back to the title, and to the id for an unknown action", () => {
    expect(labelFor("view.fit", ctx())).toBe("Fit the model to the view");
    expect(labelFor("nope.nothing", ctx())).toBe("nope.nothing");
  });
});

describe("the new action ids", () => {
  it("are all global, grouped and listed once", () => {
    const ids = [
      "context.menu",
      "app.settings",
      "view.fitSelection",
      "view.showAll",
      "view.from.front",
      "view.from.back",
      "view.from.left",
      "view.from.right",
      "view.from.top",
      "view.from.bottom",
      "view.from.iso",
      "selection.siblings",
      "selection.allFaces",
      "selection.clearPick",
      "edit.rename",
      "edit.delete",
      "edit.copyName",
      "edit.copyPath",
    ];
    expect(new Set(ACTIONS.map((a) => a.id)).size).toBe(ACTIONS.length);
    for (const id of ids) {
      const found = ACTION_SPECS.get(id);
      expect(found, id).toBeDefined();
      expect(found?.context).toBe("global");
      expect(found?.group, id).toBeDefined();
    }
  });
});
