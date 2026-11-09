/**
 * What the viewport context menu lists for each target (T-172): the groups, the checked rows, and the
 * reason on every disabled one. Pure values, no DOM.
 */
import { describe, expect, it, vi } from "vitest";

// `actions.ts` imports the store, which reads `window` as it loads and this test has no DOM; the hook
// only reads it, so the global itself stands in. Hoisted, so it runs before the imports.
vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

import type { MenuEntry, MenuItemSpec } from "../components/Menu";
import { NO_PICK, type Picked } from "../state/pick";
import { actionContextOf, type ContextInput } from "./actionContext";
import {
  ASSIGN_REASON,
  FIT_SELECTION_REASON,
  MATERIAL_REASON,
  RELATED_REASON,
  SAMPLE_REASON,
  SETTINGS_REASON,
  contextMenuItems,
  type MenuDeps,
} from "./contextMenuItems";
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
const BODY: ContextTarget = { kind: "body", part: "p1", patch: "pa", inPick: true, sample: false };
const SAMPLE: ContextTarget = { kind: "body", part: "@sample", patch: "ps", inPick: true, sample: true };
const FACE: ContextTarget = { kind: "face", part: "p1", patch: "pa", face: 4, inPick: true, sample: false };
const bodyPick = (...ids: string[]): Picked => ({ bodies: ids, faces: {}, edges: {}, vertices: {} });
const facePick = (map: Record<string, number[]>): Picked => ({ bodies: [], faces: map, edges: {}, vertices: {} });

/** Deps that record what the rows run, with every hook ready unless a test says otherwise. */
function deps(target: ContextTarget, over: Partial<ContextInput> = {}, extra: Partial<MenuDeps> = {}): MenuDeps & { calls: string[] } {
  const calls: string[] = [];
  const log = (name: string) => (...args: unknown[]) => void calls.push([name, ...args].join(" "));
  return {
    calls,
    ctx: actionContextOf(input(over), target),
    target,
    run: log("run"),
    chain: log("chain"),
    ready: { assign: true, material: true, prepare: true, related: true },
    prepareWhy: "Pick a body to prepare it",
    has: (id) => id !== "view.fitSelection" && id !== "app.settings",
    viewFrom: log("viewFrom"),
    setViewMode: log("setViewMode"),
    setNavMode: log("setNavMode"),
    toggleGrid: log("toggleGrid"),
    copy: log("copy"),
    rename: log("rename"),
    remove: log("remove"),
    isolate: log("isolate"),
    hide: log("hide"),
    hint: () => undefined,
    ...extra,
  };
}

const bodyOver: Partial<ContextInput> = { picked: bodyPick("p1"), bodies: 1, pickLabel: "1 body", selectedPatch: "pa" };
const faceOver: Partial<ContextInput> = { pickMode: "face", picked: facePick({ p1: [4] }), faces: 1, pickLabel: "1 face on 1 part", selectedPatch: null };

const entries = (items: MenuItemSpec[]): MenuEntry[] => items.filter((i): i is MenuEntry => i.type === undefined || i.type === "item");
const find = (items: MenuItemSpec[], id: string): MenuEntry => {
  const found = entries(items).find((e) => e.id === id);
  if (!found) throw new Error(`no entry ${id}; have ${entries(items).map((e) => e.id).join(", ")}`);
  return found;
};
const ids = (items: MenuItemSpec[]): string[] => items.map((i) => i.id);
/** The ids of each group, split at the separators. */
const groups = (items: MenuItemSpec[]): string[][] => {
  const out: string[][] = [[]];
  for (const item of items) {
    if (item.type === "separator") out.push([]);
    else out[out.length - 1].push(item.id);
  }
  return out;
};
/** Every entry, nested lists included. */
const everyEntry = (items: MenuItemSpec[]): MenuEntry[] => entries(items).flatMap((e) => [e, ...(e.items ? everyEntry(e.items) : [])]);

describe("empty space", () => {
  const d = deps(EMPTY);
  const items = contextMenuItems(d);

  it("lists the four groups of the table, in order", () => {
    expect(groups(items)).toEqual([
      ["view.fit", "view", "view.projection", "display", "mouse", "grid"],
      ["view.showAll", "selection.clearPick", "selection.previous"],
      ["history.undo", "history.redo"],
      ["app.settings"],
    ]);
  });

  it("offers Orthographic as a check, ticked once the camera is orthographic", () => {
    expect(find(items, "view.projection")).toMatchObject({ label: "Orthographic", checked: false });
    expect(find(contextMenuItems(deps(EMPTY, { orthographic: true })), "view.projection").checked).toBe(true);
  });

  it("offers Display as a radio over the three modes, the current one checked", () => {
    const modes = find(contextMenuItems(deps(EMPTY, { viewMode: "wireframe" })), "display").items ?? [];
    expect(entries(modes).map((m) => [m.label, m.radio, m.checked])).toEqual([
      ["Shaded", true, false],
      ["Shaded + edges", true, false],
      ["Wireframe", true, true],
    ]);
    // the row sets the mode it names
    const d2 = deps(EMPTY);
    entries(find(contextMenuItems(d2), "display").items ?? [])[1].onSelect?.();
    expect(d2.calls).toEqual(["setViewMode edges"]);
  });

  it("offers Mouse as a radio over the schemes, each saying which button does what", () => {
    const d2 = deps(EMPTY, { navMode: "blender" });
    const schemes = entries(find(contextMenuItems(d2), "mouse").items ?? []);
    expect(schemes.map((s) => s.id)).toEqual(["mouse.cad", "mouse.paraview", "mouse.blender", "mouse.toggle"]);
    expect(schemes.filter((s) => s.checked).map((s) => s.label)).toEqual(["Blender"]);
    for (const s of schemes) {
      expect(s.radio).toBe(true);
      expect(s.description).toMatch(/\w/);
    }
    schemes[1].onSelect?.();
    expect(d2.calls).toEqual(["setNavMode paraview"]);
  });

  it("offers Grid as a check that follows the workspace switch and toggles it", () => {
    expect(find(items, "grid")).toMatchObject({ label: "Grid", checked: true });
    expect(find(contextMenuItems(deps(EMPTY, { grid: false })), "grid").checked).toBe(false);
    const d2 = deps(EMPTY);
    find(contextMenuItems(d2), "grid").onSelect?.();
    expect(d2.calls).toEqual(["toggleGrid"]);
  });

  it("lists the seven standard views under View, each running its direction", () => {
    const d2 = deps(EMPTY);
    const views = entries(find(contextMenuItems(d2), "view").items ?? []);
    expect(views.map((v) => v.label)).toEqual(["Front", "Back", "Left", "Right", "Top", "Bottom", "Isometric"]);
    expect(views.map((v) => v.id)).toEqual(["front", "back", "left", "right", "top", "bottom", "iso"].map((d) => `view.from.${d}`));
    views[4].onSelect?.();
    expect(d2.calls).toEqual(["viewFrom top"]);
  });

  it("disables Show all with its reason while nothing is hidden, and enables it once something is", () => {
    expect(find(items, "view.showAll")).toMatchObject({ disabled: true, description: "Nothing is hidden" });
    expect(find(contextMenuItems(deps(EMPTY, { hiddenPatches: ["x"] })), "view.showAll").disabled).toBeUndefined();
  });

  it("disables Undo and Redo with their reasons, and enables them when there is history", () => {
    expect(find(items, "history.undo")).toMatchObject({ disabled: true, description: "Nothing to undo" });
    expect(find(items, "history.redo")).toMatchObject({ disabled: true, description: "Nothing to redo" });
    const live = contextMenuItems(deps(EMPTY, { canUndo: true, canRedo: true }));
    expect(find(live, "history.undo").disabled).toBeUndefined();
    expect(find(live, "history.redo").disabled).toBeUndefined();
  });

  it("lists Settings… disabled while nothing handles app.settings, and live once something does", () => {
    expect(find(items, "app.settings")).toMatchObject({ label: "Settings…", disabled: true, description: SETTINGS_REASON });
    const d2 = deps(EMPTY, {}, { has: () => true });
    const live = find(contextMenuItems(d2), "app.settings");
    expect(live.disabled).toBeUndefined();
    live.onSelect?.();
    expect(d2.calls).toEqual(["run app.settings"]);
  });

  it("disables Clear and Previous with their reasons when nothing is picked", () => {
    expect(find(items, "selection.clearPick")).toMatchObject({ disabled: true, description: "Nothing is picked" });
    expect(find(items, "selection.previous")).toMatchObject({ disabled: true, description: "No earlier pick" });
  });
});

describe("a body", () => {
  const d = deps(BODY, bodyOver);
  const items = contextMenuItems(d);

  it("lists the groups of the table with a separator between them and Delete last of the edit group", () => {
    // **`view.fitSelection` is gone** (claude-win2's 2000 item 2): the group now opens with Isolate,
    // which is the first entry a user can actually use. Three separators, not four, because one
    // fewer group.
    expect(groups(items)).toEqual([
      // **`selection.simulate` joined them** (T-221): Hide is the view and this is the simulation,
      // and a user looking for "make this not be there" finds both together.
      ["selection.isolate", "selection.hide", "selection.simulate"],
      ["selection.name", "chain.assign", "chain.material", "chain.prepare", "selection.siblings", "selection.allFaces"],
      ["edit.rename", "edit.copyName", "edit.copyPath", "edit.delete"],
      ["view"],
    ]);
    expect(ids(items).filter((i) => i.startsWith("sep-")).length).toBe(3);
  });

  it("marks Delete as dangerous with an icon, and no other row has one", () => {
    expect(find(items, "edit.delete").icon).toBeDefined();
    expect(entries(items).filter((e) => e.icon !== undefined).map((e) => e.id)).toEqual(["edit.delete"]);
  });

  it("does NOT list Fit selection, because it has no handler", () => {
    // **The dead entry was removed, not left greyed** (claude-win2's 2000 item 2: "implement or
    // hide"). It was asserted as present-but-disabled with a reason that named a task - which is a
    // row that can never be used, in a menu a user reads to find out what they CAN do.
    //
    // The constant is kept for the day it returns; `FIT_SELECTION_REASON` is asserted here so the
    // import is not merely vestigial.
    // **The menu is FLAT**: `contextMenuItems` joins its groups with separators, so `entries` reads
    // it directly. My first version called it as `items`, which is not in scope in this file.
    const ids = entries(contextMenuItems(deps(BODY, bodyOver))).map((e) => e.id);
    expect(ids).not.toContain("view.fitSelection");
    // And the wording kept for its return is real, so the import is not merely vestigial.
    expect(FIT_SELECTION_REASON).toMatch(/fit/i);
  });

  it("isolates and hides the patch the menu was opened on, not the selected patch", () => {
    const d2 = deps(BODY, { ...bodyOver, selectedPatch: "other" });
    const list = contextMenuItems(d2);
    find(list, "selection.isolate").onSelect?.();
    find(list, "selection.hide").onSelect?.();
    expect(d2.calls).toEqual(["isolate pa", "hide pa"]);
  });

  it("words Isolate and Hide from the state of the patch", () => {
    expect(find(items, "selection.isolate").label).toBe("Isolate");
    expect(find(items, "selection.hide").label).toBe("Hide");
    const shown = contextMenuItems(deps(BODY, { ...bodyOver, hiddenPatches: ["pa"], isolatedPatch: "pa" }));
    expect(find(shown, "selection.hide").label).toBe("Show");
    expect(find(shown, "selection.isolate").label).toBe("Show all");
  });

  it("chains Assign, Material and Prepare, each a popup row that calls its hook", () => {
    const d2 = deps(BODY, bodyOver);
    const list = contextMenuItems(d2);
    for (const which of ["assign", "material", "prepare"] as const) {
      const row = find(list, `chain.${which}`);
      expect(row.popup).toBe(true);
      expect(row.items).toBeUndefined();
      row.onSelect?.();
    }
    expect(d2.calls).toEqual(["chain assign", "chain material", "chain prepare"]);
  });

  it("runs the table's actions by id", () => {
    const d2 = deps(BODY, bodyOver);
    const list = contextMenuItems(d2);
    find(list, "selection.siblings").onSelect?.();
    find(list, "selection.allFaces").onSelect?.();
    find(list, "selection.name").onSelect?.();
    expect(d2.calls).toEqual(["run selection.siblings", "run selection.allFaces", "run selection.name"]);
  });

  it("copies the name and the path, renames and deletes through the deps", () => {
    const d2 = deps(BODY, bodyOver);
    const list = contextMenuItems(d2);
    find(list, "edit.copyName").onSelect?.();
    find(list, "edit.copyPath").onSelect?.();
    find(list, "edit.rename").onSelect?.();
    find(list, "edit.delete").onSelect?.();
    expect(d2.calls).toEqual(["copy name", "copy path", "rename", "remove"]);
  });

  it("disables a hook's row with the pick-based reason when the hook cannot run", () => {
    const none = deps(BODY, bodyOver, { ready: { assign: false, material: false, prepare: false, related: false }, prepareWhy: "Pick a body to prepare it" });
    const list = contextMenuItems(none);
    expect(find(list, "chain.assign")).toMatchObject({ disabled: true, description: ASSIGN_REASON });
    expect(find(list, "chain.material")).toMatchObject({ disabled: true, description: MATERIAL_REASON });
    expect(find(list, "chain.prepare")).toMatchObject({ disabled: true, description: "Pick a body to prepare it" });
  });

  it("shows Rename, Copy and Delete as enabled for a real body", () => {
    for (const id of ["edit.rename", "edit.copyName", "edit.copyPath", "edit.delete"]) expect(find(items, id).disabled).toBeUndefined();
  });
});

describe("the sample body", () => {
  const items = contextMenuItems(deps(SAMPLE, { picked: bodyPick("@sample"), bodies: 0, pickLabel: "1 body", selectedPatch: "ps" }));

  it("disables Name…, Rename, Copy name, Copy path and Delete with the sample reason", () => {
    for (const id of ["selection.name", "edit.rename", "edit.copyName", "edit.copyPath", "edit.delete"]) {
      expect(find(items, id)).toMatchObject({ disabled: true, description: SAMPLE_REASON });
    }
  });

  it("gives the sample reason to Assign and Material when their hooks cannot run on it", () => {
    const list = contextMenuItems(deps(SAMPLE, { picked: bodyPick("@sample"), bodies: 0, selectedPatch: "ps" }, { ready: { assign: false, material: false, prepare: false, related: false } }));
    expect(find(list, "chain.assign").description).toBe(SAMPLE_REASON);
    expect(find(list, "chain.material").description).toBe(SAMPLE_REASON);
  });

  it("disables All faces, which the sample part has no ids for", () => {
    expect(find(items, "selection.allFaces")).toMatchObject({ disabled: true, description: "The sample part has no face ids" });
  });
});

describe("a face", () => {
  const d = deps(FACE, faceOver);
  const items = contextMenuItems(d);

  it("lists the groups of the table, with no Rename and no Delete", () => {
    expect(groups(items)).toEqual([
      ["selection.isolate", "selection.hide"],
      ["selection.name", "chain.assign", "chain.related", "selection.grow", "chain.prepare", "selection.allFaces", "selection.clearPick"],
      ["edit.copyName", "edit.copyPath"],
      ["view"],
    ]);
    expect(ids(items)).not.toContain("edit.rename");
    expect(ids(items)).not.toContain("edit.delete");
  });

  it("words Isolate and Hide for the part, and acts on the face's own patch", () => {
    expect(find(items, "selection.isolate").label).toBe("Isolate part");
    expect(find(items, "selection.hide").label).toBe("Hide part");
    const d2 = deps(FACE, { ...faceOver, hiddenPatches: ["pa"], isolatedPatch: "pa" });
    const shown = contextMenuItems(d2);
    expect(find(shown, "selection.isolate").label).toBe("Show all");
    expect(find(shown, "selection.hide").label).toBe("Show part");
    find(shown, "selection.hide").onSelect?.();
    find(items, "selection.isolate").onSelect?.();
    expect(d2.calls).toEqual(["hide pa"]);
    expect(d.calls).toEqual(["isolate pa"]);
  });

  it("calls the all-faces row 'All faces of the body'", () => {
    expect(find(items, "selection.allFaces").label).toBe("All faces of the body");
  });

  it("leaves Grow disabled with its reason when no face is picked", () => {
    const none = contextMenuItems(deps(FACE, { pickMode: "face" }));
    expect(find(none, "selection.grow")).toMatchObject({ disabled: true });
    expect(find(none, "selection.grow").description).toMatch(/\w/);
  });

  it("clears with selection.clearPick, not Esc's cascade", () => {
    const d2 = deps(FACE, faceOver);
    find(contextMenuItems(d2), "selection.clearPick").onSelect?.();
    expect(d2.calls).toEqual(["run selection.clearPick"]);
  });
});

describe("the pick as a whole", () => {
  const twoBodies: ContextTarget = { kind: "pick", label: "2 bodies" };
  const manyFaces: ContextTarget = { kind: "pick", label: "5 faces on 2 parts" };

  it("lists Material, and not Related, for bodies", () => {
    const items = contextMenuItems(deps(twoBodies, { picked: bodyPick("a", "b"), bodies: 2, pickLabel: "2 bodies" }));
    expect(groups(items)).toEqual([
      ["selection.name", "chain.assign", "chain.material", "chain.prepare", "selection.previous", "selection.clearPick"],
      ["view"],
    ]);
    expect(ids(items)).not.toContain("chain.related");
  });

  it("lists Related, and not Material, for faces", () => {
    const items = contextMenuItems(deps(manyFaces, { pickMode: "face", picked: facePick({ a: [1, 2], b: [3, 4, 5] }), faces: 5, pickLabel: "5 faces on 2 parts" }));
    expect(ids(items)).toContain("chain.related");
    expect(ids(items)).not.toContain("chain.material");
  });

  it("has no Rename, Delete or Copy: those act on one part", () => {
    const items = contextMenuItems(deps(twoBodies, { picked: bodyPick("a", "b"), bodies: 2, pickLabel: "2 bodies" }));
    for (const id of ["edit.rename", "edit.delete", "edit.copyName", "edit.copyPath"]) expect(ids(items)).not.toContain(id);
  });

  it("disables Related with its reason when no face is picked", () => {
    const items = contextMenuItems(deps(manyFaces, { pickMode: "face" }, { ready: { assign: true, material: true, prepare: true, related: false } }));
    expect(find(items, "chain.related")).toMatchObject({ disabled: true, description: RELATED_REASON });
  });
});

describe("every target", () => {
  const cases: [string, MenuDeps][] = [
    ["empty", deps(EMPTY)],
    ["empty with history", deps(EMPTY, { canUndo: true, hiddenPatches: ["x"] })],
    ["body", deps(BODY, bodyOver)],
    ["sample", deps(SAMPLE, { picked: bodyPick("@sample"), selectedPatch: "ps" })],
    ["body with no hooks ready", deps(BODY, bodyOver, { ready: { assign: false, material: false, prepare: false, related: false } })],
    ["face with hooks not ready", deps(FACE, faceOver, { ready: { assign: false, material: false, prepare: false, related: false } })],
    ["face with no pick", deps(FACE, { pickMode: "face" })],
    ["pick", deps({ kind: "pick", label: "2 bodies" }, { picked: bodyPick("a", "b"), bodies: 2 })],
  ];

  it.each(cases)("gives every disabled entry of %s a reason in description and no sentence in hint", (_name, d) => {
    const items = contextMenuItems({ ...d, hint: () => "Ctrl+Z" });
    const disabled = everyEntry(items).filter((e) => e.disabled);
    // **`disabled.length` is NOT asserted any more.** It used to require at least one, and for the
    // plain `body` case the only disabled entry WAS the dead `view.fitSelection` - so removing it
    // (claude-win2's 2000 item 2) left this failing on a menu that is now correct. The invariant
    // below is about the disabled entries that exist, and a menu with none satisfies it vacuously;
    // demanding one would mean keeping a dead row to feed the test.
    //
    // A case that SHOULD have disabled entries is its own test: "with no hooks ready" and the
    // "hooks not ready" cases assert their counts elsewhere.
    for (const entry of disabled) {
      expect(entry.description, entry.id).toMatch(/\S/);
      expect(entry.hint === undefined || entry.hint.length < 12, entry.id).toBe(true);
    }
    for (const entry of everyEntry(items)) expect(entry.hint === undefined || entry.hint.length < 12, entry.id).toBe(true);
  });

  it.each(cases)("never repeats an id within %s", (_name, d) => {
    const all = ids(contextMenuItems(d));
    expect(new Set(all).size).toBe(all.length);
  });

  it("shows a chord from the keymap as the hint, for the rows that have an action", () => {
    const items = contextMenuItems(deps(EMPTY, {}, { hint: (id) => (id === "view.fit" ? "F" : undefined) }));
    expect(find(items, "view.fit").hint).toBe("F");
    expect(find(items, "view.showAll").hint).toBeUndefined();
  });

  it("runs the fit action from the empty menu", () => {
    const d = deps(EMPTY);
    find(contextMenuItems(d), "view.fit").onSelect?.();
    expect(d.calls).toEqual(["run view.fit"]);
  });
});
