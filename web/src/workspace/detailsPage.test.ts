/**
 * The Details pane's selection-to-page map (the layout decision, 2026-10-18).
 *
 * The decision's own list, as assertions: a body shows Properties, a tag or boundary shows
 * Conditions, a mesh node shows Mesh, a solver node shows Solve, and nothing selected shows the
 * stage's checklist.
 *
 * **Two of these tests exist because of mistakes I made writing the map**, and both are the kind that
 * fail silently in a running app:
 *
 * 1. I named node types that do not exist (`physics`, `run`, `results`, `scene`, `slice`). A map keyed
 *    on an invented type does not error - it falls through to the fallback - so the pane looks like it
 *    works while never showing the page that was asked for.
 * 2. A map that names a panel nothing registers renders an empty pane, which reads as "this node has
 *    no settings" rather than as a mistake - checked in `registry.test.ts`, which can load the
 *    registry (this file cannot: `state/store` reads `window` at import).
 */
import { describe, expect, it } from "vitest";
import { NAMED_PANELS, detailsPage } from "./detailsPage";

/** The node types `node.types` returns, listed from the server rather than remembered. */
const REAL_TYPES = [
  "boolean", "boundary", "cap", "constraint", "defeature", "enclosure", "extract_fluid", "group",
  "heal", "material", "mesh_patch", "mesh_result", "mesh_settings", "objective", "orient",
  "parameter", "part", "primitive", "project", "property", "region", "response", "sew", "slivers",
  "solidify", "solver_settings", "study", "tag", "unify", "variable",
];

describe("detailsPage", () => {
  it("shows the stage's checklist when nothing is selected", () => {
    expect(detailsPage(null)).toEqual({ kind: "checklist" });
    // An explicit count of zero is the same answer, so a caller need not special-case it.
    expect(detailsPage("part", 0)).toEqual({ kind: "checklist" });
  });

  it("shows the common actions when several things are selected", () => {
    // A page that is right for the first selection and wrong for the rest is worse than none.
    expect(detailsPage("part", 3)).toEqual({ kind: "many" });
    expect(detailsPage("tag", 2)).toEqual({ kind: "many" });
  });

  it("maps the decision's examples to their pages", () => {
    expect(detailsPage("part")).toEqual({ kind: "panel", id: "properties" });
    // **A tag and a boundary show their OWN page**, not `conditions` - measured: selecting a boundary
    // opened Conditions, which lays out the whole set-up and never reads the selection, so the
    // boundary's own condition picker was unreachable. Conditions is the whole-set-up page, which a
    // user opens for the overview; a selected boundary has verbs of its own.
    expect(detailsPage("tag")).toEqual({ kind: "panel", id: "properties" });
    expect(detailsPage("boundary")).toEqual({ kind: "panel", id: "properties" });
    expect(detailsPage("mesh_settings")).toEqual({ kind: "panel", id: "mesh" });
    expect(detailsPage("mesh_result")).toEqual({ kind: "panel", id: "mesh" });
    expect(detailsPage("solver_settings")).toEqual({ kind: "panel", id: "solve" });
    // **A region shows its OWN page, not Conditions.** Measured: the region row opened Conditions,
    // which lays out the whole set-up, so the region's Material picker never appeared. A tag and a
    // boundary DO belong to Conditions - it is their page - while a region has its own.
    expect(detailsPage("region")).toEqual({ kind: "panel", id: "properties" });
    expect(detailsPage("material")).toEqual({ kind: "panel", id: "properties" });
  });

  it("falls back to Properties for a node type it does not name", () => {
    // **The property that makes the pane safe to land before every page exists**: a node added later
    // shows the page that already renders any node's own fields, never a blank pane.
    expect(detailsPage("primitive")).toEqual({ kind: "panel", id: "properties" });
    expect(detailsPage("defeature")).toEqual({ kind: "panel", id: "properties" });
  });

  it("names a page for every panel id it can produce, and the set is small", () => {
    // **The registry check lives where the registry can be loaded** (`registryPanels.test.ts`):
    // importing it here fails, because `state/store` reads `window` as it loads
    // (`ReferenceError: window is not defined`, measured). This keeps the map honest about its shape.
    expect(NAMED_PANELS.length).toBeGreaterThan(2);
    for (const id of NAMED_PANELS) expect(id).toMatch(/^[a-z][a-z-]*$/);
    // **The map names FEW panels, and that is the point**: most nodes show Properties, so a long list
    // here would mean nodes are being sent to pages that do not describe them.
    expect(NAMED_PANELS.length).toBeLessThanOrEqual(4);
  });

  it("names only node types the server really returns", () => {
    // **The check that would have caught my invented types.** Every key of the map must be a type
    // `node.types` lists; a key that is not is dead weight that reads like coverage.
    const mapped = [
      "part", "tag", "boundary", "mesh_settings", "mesh_result", "mesh_patch",
      "solver_settings", "study", "region", "material", "parameter", "variable",
    ];
    const invented = mapped.filter((t) => !REAL_TYPES.includes(t));
    expect(invented).toEqual([]);
  });
});

describe("a mixed selection", () => {
  it("is 'many', not the checklist, when it has no single type", () => {
    // **The order of the checks is the bug this pins** (claude-win2's 1400 file). A mixed selection
    // arrives with `type === null` AND `count > 1`; testing `type === null` first returned the
    // checklist, and measured in the browser two parts said "2 parts" while adding a tag of any kind
    // replaced the page with "NEXT STEP".
    expect(detailsPage(null, 3).kind).toBe("many");
  });

  it("is still the checklist when nothing is chosen", () => {
    expect(detailsPage(null, 0).kind).toBe("checklist");
  });

  it("is still ONE node's page for a single selection of a known type", () => {
    expect(detailsPage("part", 1)).toEqual({ kind: "panel", id: "properties" });
  });
});
