/**
 * The shipped layout (`defaults.ts`) places the Mesh panel (ADR-0024, T-211b) where
 * `stokes/workspace/defaults.py` does: a tab in the right slot between Properties and Conditions.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { defaultDocument, defaultLayout, readDocument } from "./defaults";

describe("the default layout", () => {
  it("puts NOTHING in the right slot, so there is no rail to draw", () => {
    // **This test used to pin the tab order of the right slot** - `properties, mesh, conditions,
    // solve, results, plots` - which is exactly the arrangement whose collapsed form was the icon rail
    // the owner asked to remove. The order it checked is gone with the panels; what is worth asserting
    // is that they are not there.
    expect(defaultLayout().right.panels).toEqual([]);
  });

  it("places every panel once", () => {
    const layout = defaultDocument().layout;
    const ids = [layout.left, layout.right, layout.bottom, layout.float].flatMap((slot) => slot.panels.map((p) => p.id));
    // **No panel is placed twice**, which is what this test is for - two copies in one document is
    // how a panel appears twice on screen.
    expect(new Set(ids).size).toBe(ids.length);
    // **The stage panels are NOT placed in the default arrangement** (the owner's "remove the right
    // strip"): they are pages in the Details pane and entries in the Commands palette, and placing
    // them is what drew the icon rail. The old assertions named `mesh`, `solve` and `results` here,
    // which was the arrangement that rail came from.
    for (const panel of ["mesh", "solve", "results", "properties", "conditions"]) {
      expect(ids).not.toContain(panel);
    }
  });

  it("draws nothing on the right in the default arrangement", () => {
    // **The owner's request, as a test**: no rail and no column. An empty slot gives `occupied` 0,
    // which `slotGeometry` is what turns into a zero width - so the assertion is on the PANELS, the
    // fact this test can check without a browser.
    const right = defaultDocument().layout.right;
    expect(right.panels).toEqual([]);
    expect(right.collapsed).toBe(true);
  });
});

/**
 * The block docs/engineering/workspace-schema.md prints is what a backend test compares with
 * `default_document()`; reading the same block here is what keeps `defaultDocument()` identical to
 * the server's, so a section or an action added on one side and not the other fails.
 */
function documentedDefault(): unknown {
  const text = readFileSync(new URL("../../../docs/engineering/workspace-schema.md", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  const after = text.slice(text.indexOf("A test compares this block with"));
  const block = /```json\n([\s\S]*?)\n```/.exec(after);
  if (!block) throw new Error("the default document block is missing from workspace-schema.md");
  return JSON.parse(block[1]);
}

describe("the default document", () => {
  it("is the document the schema reference prints (the server's default_document())", () => {
    expect(JSON.parse(JSON.stringify(defaultDocument()))).toEqual(documentedDefault());
  });

  it("carries the preferences section and the Settings chord that leaves Ctrl+, on Display settings", () => {
    const d = defaultDocument();
    expect(d.preferences).toEqual({
      mouse: { scheme: "paraview", orbitLeft: false },
      import: { units: "mm" },
      // **Empty means Auto** (the Settings spec §2): a user who never opens Compute
      // still meshes, and the section shows them what was chosen.
      compute: { host: "", install: "", auto: true },
      // The guided-meshing stepper (ASK Q2, brief M-502): shown by default.
      mesh: { stepper: "shown" },
    });
    expect(d.shortcuts["app.settings"]).toBe("Ctrl+Shift+,");
    expect(d.shortcuts["display.settings"]).toBe("Ctrl+,");
    expect(d.shortcuts["context.menu"]).toEqual(["Shift+F10", "ContextMenu"]);
    // The stepper's chords (brief M-502), free in both defaults files.
    expect(d.shortcuts["mesh.task.next"]).toBe("Alt+ArrowDown");
    expect(d.shortcuts["mesh.task.previous"]).toBe("Alt+ArrowUp");
  });

  it("keeps the selection editor beside the tree and leaves the right dock empty", () => {
    const layout = defaultDocument().layout;
    expect(layout.left.panels.map((panel) => panel.id)).toEqual(["tree", "details"]);
    expect(layout.right.panels).toEqual([]);
    expect(layout.bottom.panels.map((panel) => panel.id)).toEqual(["output", "design-table"]);
  });
});

describe("readDocument and preferences", () => {
  it("keeps valid preferences and unknown keys", () => {
    const doc = readDocument({ preferences: { mouse: { scheme: "blender", orbitLeft: true, extra: 1 }, import: { units: "ft" } } });
    expect(doc.preferences.mouse).toEqual({ scheme: "blender", orbitLeft: true, extra: 1 });
    expect(doc.preferences.import.units).toBe("ft");
  });

  it("falls back to the default for a wrong type or an unknown id, and when the section is missing", () => {
    // **The wrong values fall back and the missing section is filled**, so `compute` arrives from
    // the default even though the stored document never mentioned it.
    expect(
      readDocument({ preferences: { mouse: { scheme: "maya", orbitLeft: "yes" }, import: { units: "furlong" } } })
        .preferences,
    ).toEqual(defaultDocument().preferences);
    expect(readDocument({}).preferences).toEqual(defaultDocument().preferences);
    expect(readDocument({ preferences: [] }).preferences).toEqual(defaultDocument().preferences);
  });
});

describe("saved layouts with a Details pane", () => {
  it("moves right-side stage editors to hidden once and keeps the other layout data", () => {
    const legacy = {
      layout: {
        left: { mode: "split", size: 386, collapsed: false, panels: [{ id: "tree" }, { id: "details" }] },
        right: {
          mode: "tabs",
          size: 472,
          collapsed: false,
          panels: [
            { id: "properties", order: 0 },
            { id: "mesh", order: 1 },
            { id: "conditions", order: 2 },
            { id: "solve", order: 3 },
            { id: "results", order: 4 },
            { id: "plots", order: 5 },
          ],
        },
        bottom: { mode: "tabs", size: 286, collapsed: false, panels: [{ id: "output" }] },
        float: { mode: "tabs", size: 320, collapsed: false, panels: [] },
        hidden: [],
      },
      tree: { groupOrder: ["mesh", "geometry"] },
    };

    const migrated = readDocument(legacy);
    expect(migrated.layout.right.panels.map((panel) => panel.id)).toEqual(["plots"]);
    expect(migrated.layout.hidden).toEqual(["properties", "mesh", "conditions", "solve", "results"]);
    expect(migrated.layout.left.size).toBe(386);
    expect(migrated.layout.right).toMatchObject({ size: 472, collapsed: false, mode: "tabs" });
    expect(migrated.layout.bottom.size).toBe(286);
    expect(migrated.tree.groupOrder.slice(0, 2)).toEqual(["mesh", "geometry"]);
    expect(readDocument(migrated)).toEqual(migrated);
  });

  it("keeps a classic-style editor location when Details is not beside the tree", () => {
    const classic = readDocument({
      layout: {
        left: { mode: "split", size: 340, collapsed: false, panels: [{ id: "tree" }] },
        right: { mode: "tabs", size: 440, collapsed: false, panels: [{ id: "mesh" }, { id: "solve" }] },
        bottom: { mode: "tabs", size: 240, collapsed: true, panels: [] },
        float: { mode: "tabs", size: 320, collapsed: false, panels: [] },
        hidden: [],
      },
    });
    expect(classic.layout.right.panels.map((panel) => panel.id)).toEqual(["mesh", "solve"]);
    expect(classic.layout.hidden).toEqual([]);
  });
});

describe("one Results control surface (item 3, and the duplicate Codex caught)", () => {
  it("places `results` on the RIGHT slot in NO default arrangement", () => {
    // **The code was right all along and the screenshot was not**: `defaultLayout()` has an empty right
    // slot, and the duplicate a review saw came from a SAVED workspace written when the panel still
    // belonged there. This pins the code half; the saved half is what made the picture wrong.
    expect(defaultLayout().right.panels).toEqual([]);
    expect(defaultLayout().hidden).toContain("results");
  });

});
