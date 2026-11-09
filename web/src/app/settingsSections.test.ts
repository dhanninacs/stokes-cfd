/**
 * The Settings table (T-178): which option needs a project, where each value is read from, and that
 * every option names something that exists. The dialog renders this table, so a wrong row here is a
 * wrong control there.
 */
import { describe, expect, it, vi } from "vitest";

// `app/actions` pulls in `state/store`, which reads `window` as it loads; this test has no DOM.
vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

import { defaultDocument, IMPORT_UNITS } from "../workspace/defaults";
import { getIn } from "../workspace/paths";
import type { WorkspaceDocument } from "../workspace/types";
import { PROJECT_COMMANDS, SECTIONS, choiceDisabled, optionDisabled, optionShown, optionValue, sectionById, type Option, type OptionState } from "./settingsSections";
import { REDUCED_MOTION_KEY, STAGE_FOCUS_KEY } from "./prefs";

/** Every device preference the Settings dialog may name. */
const DEVICE_KEYS = [REDUCED_MOTION_KEY, STAGE_FOCUS_KEY] as const;


function option(id: string): Option {
  const found = SECTIONS.flatMap((s) => s.options).find((o) => o.id === id);
  if (!found) throw new Error(`no option ${id}`);
  return found;
}

function state(patch: Partial<OptionState> = {}, edit: (d: WorkspaceDocument) => void = () => undefined): OptionState {
  const workspace = defaultDocument();
  edit(workspace);
  return {
    workspace,
    project: null,
    device: () => null,
    theme: () => ({ family: "stokes", appearance: "auto" }),
    projectFolder: false,
    ...patch,
  };
}

describe("the sections", () => {
  it("is the trimmed set, in order, with unique option ids", () => {
    // **Two sections went** (the Settings spec §1: "an option appears in exactly one place"):
    // `Appearance` (the top-bar toggle and the Appearance popover hold Style, Appearance and the
    // custom-theme editor) and `Mouse` (the viewport's own Mouse menu). What remains is `Display`,
    // which is reduced motion and the ground-grid switch - neither offered anywhere else.
    // **Compute first** (the Settings spec §2): it decides whether anything else can work, and the
    // old first section was a theme picker that now lives in the Appearance popover.
    expect(SECTIONS.map((s) => s.title)).toEqual([
      "Compute",
      "Folders",
      "Display",
      "Units",
      "Workspace",
      "About",
    ]);
    const ids = SECTIONS.flatMap((s) => s.options.map((o) => o.id));
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("find a section by id and fall back to the first", () => {
    expect(sectionById("units").title).toBe("Units");
    expect(sectionById("gone").title).toBe("Compute");
    expect(sectionById(null).title).toBe("Compute");
  });

  it("no longer offers Style, Appearance or the mouse scheme anywhere", () => {
    // **The removals, pinned.** A rule about "one place per option" is only real if something
    // notices when the option comes back - which is how it would return, one well-meant row at a
    // time.
    const ids = SECTIONS.flatMap((s) => s.options.map((o) => o.id));
    for (const gone of ["appearance.style", "appearance.appearance", "appearance.custom", "mouse.scheme", "mouse.orbitLeft"]) {
      expect(ids, gone).not.toContain(gone);
    }
    // And what has no other home is still here.
    expect(ids).toContain("appearance.reducedMotion");
    expect(ids).toContain("appearance.grid");
  });
});

describe("where each option's value lives", () => {
  it("names a workspace path that exists in the default document, a known command, or a device key", () => {
    const doc = defaultDocument();
    for (const o of SECTIONS.flatMap((s) => s.options)) {
      const { store } = o;
      if (store.kind === "workspace") expect(getIn(doc, store.path), `${o.id}: ${store.path.join(".")}`).not.toBeUndefined();
      else if (store.kind === "project") expect(PROJECT_COMMANDS as readonly string[], o.id).toContain(store.command);
      // **Every device key must be one the prefs module defines**, not a literal here: a row
      // naming a key nothing reads would silently do nothing, which is the failure this whole
      // test exists to catch.
      else if (store.kind === "device") expect(DEVICE_KEYS as readonly string[], o.id).toContain(store.key);
    }
  });

  it("writes the preferences under the section the server keeps them in", () => {
    // The mouse rows are gone; `units.importUnit` is the preference still offered here.
    const store = option("units.importUnit").store;
    expect(store.kind === "workspace" && store.path[0]).toBe("preferences");
  });

  it("offers the import units the server accepts", () => {
    expect((option("units.importUnit").choices ?? []).map((c) => c.value)).toEqual([...IMPORT_UNITS]);
  });
});

describe("optionValue", () => {
  it("follows viewport.grid at once", () => {
    expect(optionValue(option("appearance.grid"), state())).toBe(true);
    expect(optionValue(option("appearance.grid"), state({}, (d) => (d.viewport.grid = false)))).toBe(false);
  });

  it("reads the import unit", () => {
    expect(optionValue(option("units.importUnit"), state({}, (d) => (d.preferences.import.units = "ft")))).toBe("ft");
  });

  it("reads the display units from the open project, and nothing without one", () => {
    expect(optionValue(option("units.system"), state({ project: { display_units: "imperial" } }))).toBe("imperial");
    expect(optionValue(option("units.system"), state())).toBe("");
  });

  it("reads a device switch as a boolean", () => {
    expect(optionValue(option("appearance.reducedMotion"), state({ device: () => "1" }))).toBe(true);
    expect(optionValue(option("appearance.reducedMotion"), state({ device: () => "0" }))).toBe(false);
    expect(optionValue(option("appearance.reducedMotion"), state())).toBe(false);
  });

  // **`takes Style and Appearance from the theme` is gone with the rows it tested.** The `derived`
  // path still exists in the model (`optionValue` reads a theme for a `derived: "style"` option), but
  // no section offers one now, so a test of it would be asserting a code path with no caller. It
  // returns when a section needs it again - and the Appearance popover, which owns those controls,
  // has its own tests.
});

describe("optionDisabled and choiceDisabled", () => {
  it("disables the display units without a project, and says why", () => {
    expect(optionDisabled(option("units.system"), { project: null })).toBe("Open a project first");
    expect(optionDisabled(option("units.system"), { project: { display_units: "si" } })).toBeNull();
  });

  it("leaves the default import unit available without a project: it is a preference", () => {
    expect(optionDisabled(option("units.importUnit"), { project: null })).toBeNull();
  });

  it("disables 'This project' until the project has a folder", () => {
    const projectChoice = (option("workspace.scope").choices ?? []).find((c) => c.value === "project");
    expect(projectChoice).toBeDefined();
    if (!projectChoice) return;
    expect(choiceDisabled(projectChoice, { projectFolder: false })).toBe("Save the project to a folder first");
    expect(choiceDisabled(projectChoice, { projectFolder: true })).toBeNull();
  });
});

describe("optionShown", () => {
  it("still honours shownWhen, which the removed row was the only user of", () => {
    // **The rule outlives its only caller.** `mouse.orbitLeft` was the one `shownWhen` option, and
    // it is gone with the mouse rows; the mechanism stays because a conditional row is a reasonable
    // thing for a future section to want. Tested with a SYNTHETIC option rather than a real one, so
    // this asserts the rule and not the presence of a row that no longer exists.
    const conditional: Option = {
      id: "probe",
      label: "Probe",
      store: { kind: "readonly" },
      control: "custom",
      shownWhen: { path: ["preferences", "mouse", "scheme"], equals: "toggle" },
    };
    expect(optionShown(conditional, state())).toBe(false);
    expect(optionShown(conditional, state({}, (d) => (d.preferences.mouse.scheme = "paraview")))).toBe(false);
    expect(optionShown(conditional, state({}, (d) => (d.preferences.mouse.scheme = "toggle")))).toBe(true);
  });

  it("shows every option, because the one conditional row is gone", () => {
    // **`mouse.orbitLeft` was the only `shownWhen` option** - it appeared only under the
    // Select / Orbit scheme - so with the mouse rows removed nothing is conditional. Asserted rather
    // than deleted: the day a row gains a `shownWhen`, this notices that the section now depends on
    // state, which is worth a thought.
    const hidden = SECTIONS.flatMap((s) => s.options).filter((o) => !optionShown(o, state()));
    expect(hidden).toEqual([]);
  });
});

describe("the Tree layout control", () => {
  const option = (() => {
    const found = SECTIONS.flatMap((s) => s.options).find((o) => o.id === "appearance.treeLayout");
    if (found === undefined) throw new Error("the Tree layout option is missing from the sections");
    return found;
  })();

  it("lives on Display and writes the workspace key the renderer reads", () => {
    // **The exact path** `TreePanel` reads; a typo here is a control that changes nothing.
    expect(sectionById("display").options.some((o) => o.id === "appearance.treeLayout")).toBe(true);
    expect(option.store).toEqual({ kind: "workspace", path: ["tree", "layout"] });
  });

  it("shows the user's WORDS, never the stored codes", () => {
    // Codex's 063500: *"no internal codes"* - a user reads "Workflow outline", not `outline`.
    expect(option.choices).toEqual([
      { value: "outline", label: "Workflow outline" },
      { value: "groups", label: "Groups" },
    ]);
    for (const choice of option.choices ?? []) expect(choice.label).not.toBe(choice.value);
  });

  it("offers exactly the two values the schema accepts", () => {
    // **The schema's pair**: `TREE_LAYOUTS = ("outline", "groups")`. A third choice would be refused.
    expect((option.choices ?? []).map((c) => c.value)).toEqual(["outline", "groups"]);
  });
});
