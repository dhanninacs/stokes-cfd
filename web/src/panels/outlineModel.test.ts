import { describe, expect, it } from "vitest";
import { OUTLINE, OUTLINE_STAGES, revealChainFor, SECTION_PATH, availableRows, buildOutline, PAGE_ROW, clickAction, flattenOutline, outlineRows, outlineRowsFor, outlineStatus, resolveIn, sectionText, type OutlineRow } from "./outlineModel";
import { openTargetOf } from "./stageTreeModel";
import { INITIAL_TURBULENCE_SECTION } from "./settingsPresentation";

/**
 * The outline as data (G-305).
 *
 * **Two properties carry the brief**: the six sections appear in the stage order, and **every real node
 * appears at most once** - a tree that shows one node in two places makes selection ambiguous.
 */
describe("the outline's sections", () => {
  it("has all six, in the stage order", () => {
    expect([...OUTLINE_STAGES]).toEqual(["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"]);
    for (const stage of OUTLINE_STAGES) expect(OUTLINE[stage].length).toBeGreaterThan(0);
  });

  it("names each section after a REAL group path", () => {
    // **Measured against a real project**: these six exist there, so a section is never a container
    // with nothing under it.
    expect(SECTION_PATH).toEqual({
      Geometry: "/geometry",
      Mesh: "/mesh",
      Physics: "/physics",
      Solve: "/solver",
      Results: "/scenes",
      Explore: "/studies",
    });
  });

  it("gives each section's rows unique keys", () => {
    for (const stage of OUTLINE_STAGES) {
      const keys = OUTLINE[stage].map((r) => r.key);
      expect(new Set(keys).size).toBe(keys.length);
    }
  });

  it("groups results like a post-processing tree without duplicate legacy folders", () => {
    const results = OUTLINE.Results;
    expect(results.map((r) => r.label)).toEqual(["Surfaces", "Graphics", "Plots", "Reports"]);
    expect(results.flatMap((r) => r.children?.map((c) => c.label) ?? [])).toEqual([
      "Slice", "Contours", "Vectors", "Streamlines", "Probe", "Level of detail", "Residuals", "Monitors", "HTML report",
    ]);
    const pageTargets = results.flatMap((r) => r.children ?? []).filter((r) => r.open).map((r) => r.open);
    for (const target of pageTargets) expect(PAGE_ROW[target!]).toBeDefined();
  });

  it("puts the boundaries under Physics, as the brief's largest folder", () => {
    const physics = OUTLINE.Physics.map((r) => r.key);
    expect(physics).toContain("boundaries");
    expect(physics).toContain("materials");
    expect(physics).toContain("parameters");
  });
});

describe("the two rows this project cannot build", () => {
  const unavailableOf = (rows: readonly OutlineRow[]) =>
    rows.filter((r) => r.unavailable !== undefined).map((r) => r.key);

  it("declares Mesh ▸ Controls unavailable, with a reason rather than an empty folder", () => {
    // **Measured**: the project's groups are `/mesh` and `/mesh/results`; there is no `/mesh/controls` and
    // no `mesh.control*` command. A folder that is always empty would pass a brief-shaped test and tell a
    // user something false.
    expect(unavailableOf(OUTLINE.Mesh)).toEqual(["controls"]);
    const row = OUTLINE.Mesh.find((r) => r.key === "controls")!;
    expect(row.unavailable).toMatch(/mesh controls/);
  });

  it("declares Physics ▸ Interfaces unavailable, for the same reason", () => {
    // `region.interfaces` does not exist as a command.
    expect(unavailableOf(OUTLINE.Physics)).toEqual(["interfaces"]);
  });

  it("declares NOTHING else unavailable, so the rest is buildable today", () => {
    for (const stage of ["Geometry", "Solve", "Results", "Explore"] as const) {
      expect(unavailableOf(OUTLINE[stage])).toEqual([]);
    }
  });

  it("omits a page whose destination the Details pane does not know", () => {
    // **Not drawn inert** (Codex's 005002): `reference` names "properties", which is not a panel id the
    // tree resolves, so the row is omitted before anything can render it.
    const projection = buildOutline(null, () => null);
    expect(projection.omitted.some((o) => o.key === "reference")).toBe(true);
    const reference = projection.sections.find((s) => s.stage === "Physics")!.rows.find((r) => r.label === "Reference values");
    expect(reference).toBeUndefined();
  });

  it("keeps the pages that DO open a panel, with a row kind the Details pane resolves", () => {
    const projection = buildOutline(null, () => null);
    const solve = projection.sections.find((s) => s.stage === "Solve")!;
    expect(solve.rows.some((r) => r.label === "Settings")).toBe(true);
    expect(PAGE_ROW.solve).toBe("solver-settings");
  });
});

describe("availableRows", () => {
  it("separates the unavailable rows with their reasons", () => {
    const result = availableRows(OUTLINE.Mesh, () => true);
    expect(result.buildable.map((r) => r.key)).toEqual(["global", "capping", "meshers", "result"]);
    expect(result.unavailable.map((u) => u.row.key)).toEqual(["controls"]);
    expect(result.unavailable[0]!.reason).not.toBe("");
  });

  it("keeps a row whose SOURCE is absent in this project - it is empty, not missing", () => {
    // A project without studies has no `/studies`; the row is simply empty rather than unavailable.
    const result = availableRows(OUTLINE.Explore, () => false);
    expect(result.unavailable).toEqual([]);
    expect(result.buildable.map((r) => r.key)).toEqual(["studies"]);
  });

  it("keeps every Solve row but the unavailable ones", () => {
    const result = availableRows(OUTLINE.Solve, () => true);
    expect(result.unavailable).toEqual([]);
    expect(result.buildable.map((r) => r.key)).toEqual(["cases", "methods", "controls", "initialisation", "run", "runs"]);
  });
});

describe("buildOutline - the projection (Codex's 233503 decision)", () => {
  const node = (id: string, name: string, children?: unknown[]): any => ({ id, name, type: "group", children });
  const all = (source: string) => node(source, source.split("/").pop() ?? "x", [node(`${source}/a`, "a"), node(`${source}/b`, "b")]);

  it("OMITS the unsupported rows and reports them, rather than drawing them empty", () => {
    // **The decision**: no fake persisted folders, no empty success rows, no inert menus. The omitted list
    // is for a diagnostic, never a row.
    const projection = buildOutline(all("/"), all);
    const meshKeys = projection.sections.find((s) => s.stage === "Mesh")!.rows.map((r) => r.label);
    expect(meshKeys).not.toContain("Local controls");
    // **`reference` joins them**: its page names a Panel the Details pane does not know, so a click on it
    // would do nothing - and an inert row looks available without being it.
    expect(projection.omitted.map((o) => o.key).sort()).toEqual(["controls", "interfaces", "reference"]);
    for (const o of projection.omitted) expect(o.reason).not.toBe("");
  });

  it("keeps every section, in the stage order", () => {
    const projection = buildOutline(all("/"), all);
    expect(projection.sections.map((s) => s.stage)).toEqual([...OUTLINE_STAGES]);
  });

  it("preserves REAL node ids and their children, so selection and reveal still work", () => {
    const projection = buildOutline(all("/"), all);
    const bodies = projection.sections.find((s) => s.stage === "Geometry")!.rows.find((r) => r.label === "Bodies")!;
    expect(bodies.children?.map((c) => c.id)).toEqual(["/geometry/parts/a", "/geometry/parts/b"]);
  });

  it("gives every synthetic row a section prefix, without rewriting project-node ids", () => {
    const projection = buildOutline(null, all);
    for (const section of projection.sections) {
      expect(section.id).toBe(`virtual:outline:${section.stage}`);
      for (const row of section.rows) {
        expect(row.id.startsWith(`virtual:outline:${section.stage}:`) || row.id.startsWith("virtual:")).toBe(true);
      }
    }
  });

  it("draws NO phantom 'ready' entries for a minimal project", () => {
    // A minimal project has almost nothing: the tree must be empty rather than claiming readiness.
    const projection = buildOutline(null, () => null);
    const status = outlineStatus(projection, () => "idle");
    for (const [, value] of status) {
      expect(value.text).toBe("");
      expect(value.state).toBe("idle");
    }
  });

  it("survives an expanded project: every supported row resolves and no id repeats", () => {
    const projection = buildOutline(all("/"), all);
    const ids: string[] = [];
    const walk = (n: { id: string; children?: readonly { id: string; children?: readonly unknown[] }[] }): void => {
      ids.push(n.id);
      for (const c of n.children ?? []) walk(c as never);
    };
    for (const s of projection.sections) {
      ids.push(s.id);
      for (const r of s.rows) walk(r);
    }
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("outlineStatus - rolled up, counted, never invented", () => {
  const projection = buildOutline(null, () => null);

  it("counts what is outstanding when one row of a section is todo", () => {
    const physics = projection.sections.find((s) => s.stage === "Physics")!;
    const status = outlineStatus(projection, (id) => (id === physics.rows[0]!.id ? "todo" : "idle"));
    expect(status.get(physics.id)).toEqual({ state: "todo", text: "1 outstanding" });
  });

  it("lets the WORST child win: failed beats todo beats stale", () => {
    const physics = projection.sections.find((s) => s.stage === "Physics")!;
    const [a, b, c] = physics.rows;
    const status = outlineStatus(projection, (id) => (id === a!.id ? "stale" : id === b!.id ? "todo" : id === c!.id ? "failed" : "idle"));
    expect(status.get(physics.id)!.state).toBe("failed");
  });

  it("says 'ready' for a section with nothing outstanding", () => {
    const physics = projection.sections.find((s) => s.stage === "Physics")!;
    const status = outlineStatus(projection, (id) => (id === physics.rows[0]!.id ? "done" : "idle"));
    expect(status.get(physics.id)!.text).toBe("done");
  });

  it("has nothing to say about an idle section", () => {
    expect(sectionText("idle", 0)).toBe("");
    expect(sectionText("todo", 3)).toBe("3 outstanding");
    expect(sectionText("running", 0)).toBe("running");
  });
});

describe("flattenOutline - what a renderer draws", () => {
  const node = (id: string, name: string, children?: unknown[]): any => ({ id, name, type: "group", children });
  const all = (s: string) => node(s, s.split("/").pop() ?? "x", [node(`${s}/a`, `${s}/a`), node(`${s}/b`, `${s}/b`)]);
  const projection = buildOutline(all("/"), all);
  const everything = new Set(projection.sections.map((s) => s.id));

  it("draws sections only when nothing is open - a closed tree is one line per section", () => {
    const lines = flattenOutline(projection, { expanded: new Set() });
    expect(lines.map((l) => l.id)).toEqual(projection.sections.map((s) => s.id));
    expect(lines.every((l) => l.depth === 0 && l.foldable && !l.open)).toBe(true);
  });

  it("draws a section's rows with depth 1 when it is open", () => {
    const lines = flattenOutline(projection, { expanded: everything });
    const first = lines.findIndex((l) => l.id === projection.sections[0]!.id);
    expect(lines[first + 1]!.depth).toBe(1);
    expect(lines[first + 1]!.stage).toBe(projection.sections[0]!.stage);
  });

  it("does not duplicate results folders across categories", () => {
    // Surfaces, Graphics, Plots and Reports now own their result rows, so the former duplicate
    // Scenes entry and its `/scenes` source reference are gone.
    const lines = flattenOutline(projection, { expanded: everything });
    const referenced = lines.filter((l) => l.refScope !== null);
    expect(referenced).toEqual([]);
    expect(new Set(lines.map((line) => line.id)).size).toBe(lines.length);
  });

  it("Boundary conditions is NOT a reference any more: it narrows /regions to the boundary nodes", () => {
    // **Measured before the fix**: the boundary rows never appeared, because Regions had already
    // claimed `/regions` and Boundary conditions became a reference with no rows of its own.
    // **A realistic source**: boundaries live INSIDE a region, which is why they need narrowing.
    const bnd = (id: string, name: string) => ({ id, name, type: "boundary", path: id });
    const region = node("/regions", "regions", [
      node("/regions/fluid", "fluid", [
        node("/regions/fluid/boundaries", "boundaries", [
          bnd("/regions/fluid/boundaries/inlet", "inlet"),
          bnd("/regions/fluid/boundaries/outlet", "outlet"),
        ]),
      ]),
    ]);
    const real = buildOutline(region, (s: string) => (s === "/regions" ? region : null));
    const lines = flattenOutline(real, { expanded: new Set(["virtual:outline:Physics"]) });
    const boundary = lines.find((l) => l.label === "Boundary conditions")!;
    expect(boundary.refScope).toBeNull();
    expect(boundary.foldable).toBe(true);
  });

  it("keeps ANCESTORS of a filter match, so a match is never shown without its way to it", () => {
    const lines = flattenOutline(projection, { expanded: new Set(), filter: "boundary" });
    const labels = lines.map((l) => l.label);
    expect(labels).toContain("Physics");
    expect(labels).toContain("Boundary conditions");
    expect(labels).not.toContain("Geometry");
  });

  it("opens the filtered tree, so a match inside a closed row is visible", () => {
    const lines = flattenOutline(projection, { expanded: new Set(), filter: "boundary" });
    const physics = lines.find((l) => l.label === "Physics")!;
    expect(physics.open).toBe(true);
  });

  it("carries the state and its text from the status map", () => {
    const states = new Map([[projection.sections[0]!.id, { state: "todo" as const, text: "2 outstanding" }]]);
    const lines = flattenOutline(projection, { expanded: new Set(), states });
    expect(lines[0]!.state).toBe("todo");
    expect(lines[0]!.stateText).toBe("2 outstanding");
  });

  it("carries a bound finding's message as the row's value", () => {
    const lines = flattenOutline(projection, { expanded: everything, rowValue: (id) => (id.endsWith(":materials") ? "needs a material" : "") });
    expect(lines.find((l) => l.label === "Materials")!.value).toBe("needs a material");
  });
});

describe("clickAction - what a click on a line does", () => {
  it("a REFERENCE row goes to its owner and selects nothing", () => {
    // **Not inert** (Codex's 000514), and the selection belongs to a node, not to an alias.
    expect(clickAction({ id: "virtual:outline:Physics:boundaries", kind: "folder", foldable: false, refScope: "virtual:outline:Physics:regions" }))
      .toEqual({ kind: "ref", target: "virtual:outline:Physics:regions" });
  });

  it("a SECTION folds", () => {
    expect(clickAction({ id: "virtual:outline:Mesh", kind: "section", foldable: true, refScope: null }))
      .toEqual({ kind: "fold", target: "virtual:outline:Mesh" });
  });

  it("a foldable folder folds", () => {
    expect(clickAction({ id: "virtual:outline:Geometry:bodies", kind: "folder", foldable: true, refScope: null }))
      .toEqual({ kind: "fold", target: "virtual:outline:Geometry:bodies" });
  });

  it("a real node leaf SELECTS, so the Details pane follows the tree", () => {
    expect(clickAction({ id: "n_part_box", kind: "leaf", foldable: false, refScope: null }))
      .toEqual({ kind: "select", target: "n_part_box" });
  });

  it("a page with nothing under it and nothing to open does nothing", () => {
    // A page whose destination is not implemented must not pretend to act.
    expect(clickAction({ id: "virtual:outline:Physics:reference", kind: "page", foldable: false, refScope: null }))
      .toEqual({ kind: "none" });
  });
});

describe("outlineRows - the mount point", () => {
  const node = (id: string, name: string, children?: unknown[]): any => ({ id, name, type: "group", children });
  const all = (s: string) => node(s, s.split("/").pop() ?? "x", [node(`${s}/a`, "a")]);
  const projection = buildOutline(all("/"), all);
  const findNode = (id: string) => (id === "n_part_box" ? ({ id, name: "box", type: "part" } as never) : null);

  it("produces the tree's OWN row shape, so everything downstream keeps working", () => {
    const rows = outlineRows(flattenOutline(projection, { expanded: new Set() }), findNode);
    expect(rows.length).toBe(projection.sections.length);
    for (const row of rows) {
      expect(row).toHaveProperty("node");
      expect(row).toHaveProperty("depth");
      expect(row).toHaveProperty("parentId");
      expect(row).toHaveProperty("hasChildren");
      expect(row).toHaveProperty("open");
    }
  });

  it("keeps a REAL node untouched, so selection and the Details page see what they see today", () => {
    // **Both levels open**: the section AND the folder that holds the leaves, or the leaf is not drawn.
    const lines = flattenOutline(projection, {
      expanded: new Set(["virtual:outline:Geometry", "virtual:outline:Geometry:bodies"]),
    });
    const rows = outlineRows(lines, () => ({ id: "/geometry/parts/a", name: "a", type: "part" }) as never);
    const leaf = rows.find((r) => r.node.id === "/geometry/parts/a")!;
    expect(leaf.node.type).toBe("part");
    expect(leaf.hasChildren).toBe(false);
  });

  it("gives a virtual row the tree's own `meta.row`, so the menus treat it as virtual", () => {
    const rows = outlineRows(flattenOutline(projection, { expanded: new Set() }), findNode);
    expect((rows[0]!.node.meta as { row: string }).row).toBe("outline");
  });

  it("sets each row's PARENT from the flat list's own depths", () => {
    // The flat list carries depth, not parents: the parent of a line is the nearest one above with a
    // smaller depth, which is what the tree's expand/collapse and drag rules expect.
    const lines = flattenOutline(projection, {
      expanded: new Set(["virtual:outline:Geometry", "virtual:outline:Geometry:bodies"]),
    });
    const rows = outlineRows(lines, findNode);
    const child = rows.find((r) => r.depth === 1)!;
    expect(child.parentId).toBe("virtual:outline:Geometry");
    expect(rows.find((r) => r.depth === 0)!.parentId).toBeNull();
  });
});

describe("revealChainFor - selecting a node from OUTSIDE the tree", () => {
  const node = (id: string, name: string, path: string, children?: unknown[]) => ({ id, name, type: "group", path, children });
  const patch = node("n_patch", "inlet", "/mesh/results/blockmesh/inlet");
  const result = node("n_result", "blockmesh", "/mesh/results/blockmesh", [patch]);
  const results = node("g_results", "results", "/mesh/results", [result]);
  const mesh = node("g_mesh", "mesh", "/mesh", [results]);
  const root = node("p", "duct", "/", [mesh]);

  it("returns the OUTLINE's virtual ancestors, not the real group ids", () => {
    // **The defect this fixes**: `revealIds`/`ancestorsOf` answer real ids, which the outline never
    // reads - measured: selecting a mesh patch revealed nothing and left six closed sections.
    const projection = buildOutline(root as never, (s: string) => {
      let f: never | null = null;
      const w = (n: any) => { if (f) return; if (n.path === s) { f = n; return; } for (const c of n.children ?? []) w(c); };
      w(root);
      return f;
    });
    const findNode = (id: string) => {
      let f: any = null;
      const w = (n: any) => { if (f) return; if (n.id === id) { f = n; return; } for (const c of n.children ?? []) w(c); };
      w(root);
      return f;
    };
    const chain = revealChainFor(projection, "n_patch", findNode);
    expect(chain[0]).toBe("virtual:outline:Mesh");
    /*
      **The chain is virtual ids THEN real ones**: the section and the row are virtual (the outline draws
      those), and the real group ids after them are what makes the tree expand the row's own subtree -
      measured: without them the selected patch stayed undrawn and nothing was marked.
      **The last id before the node is its parent**, which is what a fold needs.
    */
    const virtualPart = chain.filter((id) => id.startsWith("virtual:outline:"));
    expect(virtualPart.length).toBe(2);
    expect(chain.indexOf("n_result")).toBeGreaterThan(chain.indexOf("virtual:outline:Mesh:global"));
  });

  it("is empty for a node the outline does not show, rather than opening something random", () => {
    const projection = buildOutline(root as never, () => null);
    expect(revealChainFor(projection, "n_nowhere", () => null)).toEqual([]);
  });
});

describe("PAGE_ROW - each mapping opens the page the row CLAIMS", () => {
  it("names NO kind for Conditions, because no virtual kind reaches that panel", () => {
    // **Codex's 013452 applied to the letter**: `flow-model` opens the SOLVE panel and `"boundary"` is
    // not a `meta.row` kind at all - `openTargetOf` reaches Conditions only from a real `region` or
    // `boundary` NODE. A page row claiming it would be inert, so the entry is absent.
    expect(PAGE_ROW.conditions).toBeUndefined();
    expect(PAGE_ROW["flow-model"]).toBeUndefined();
  });

  it("names only row kinds `openTargetOf` resolves", () => {
    // Every value must be a kind the stage model knows; a typo would open nothing, silently.
    const known = new Set([
      "mesh-settings", "mesh-result", "mesh-boundaries", "mesh-surface", "mesh-layers",
      "mesh-quality", "mesh-fidelity", "solver-settings", "flow-model", "run", "fields",
      "field", "slice", "residuals", "monitors", "result-vectors", "result-streamlines",
      "result-probe", "result-report", "result-lod",
    ]);
    for (const kind of Object.values(PAGE_ROW)) expect(known.has(kind)).toBe(true);
  });

  it("omits Solver type and Models, because neither page exists", () => {
    // Pointing "Solver type" at the Conditions panel would open the BOUNDARY conditions - a different
    // question with a similar name, which is worse than not showing the row.
    const projection = buildOutline(null, () => null);
    const physics = projection.sections.find((s) => s.stage === "Physics")!;
    const labels = physics.rows.map((r) => r.label);
    expect(labels).not.toContain("Solver type");
    expect(labels).not.toContain("Models");
    // And the boundary conditions still reach the Conditions panel, through their REAL folder.
    expect(labels).toContain("Boundary conditions");
  });
});

describe("two rows sharing a source never draw a real node twice", () => {
  it("Regions and Boundary conditions: no real id repeats, with two regions", () => {
    /*
      **Measured as a defect**: after `only` let these two rows share `/regions`, the boundary nodes and
      their region were drawn TWICE - once under Regions, once under Boundary conditions - which is
      exactly what the uniqueness rule exists to prevent, and the fixture here had no boundaries so the
      older test said nothing. **A duplicate real id makes selection ambiguous.**
    */
    const bnd = (id: string, name: string) => ({ id, name, type: "boundary", path: id });
    const region = (id: string, name: string) => ({
      id,
      name,
      type: "region",
      path: `/regions/${name}`,
      children: [
        {
          id: `${id}_b`,
          name: "boundaries",
          type: "group",
          path: `/regions/${name}/boundaries`,
          children: [bnd(`/regions/${name}/boundaries/inlet`, "inlet")],
        },
      ],
    });
    const groups = {
      id: "g_regions",
      name: "regions",
      type: "group",
      path: "/regions",
      children: [region("r_fluid", "fluid"), region("r_solid", "solid")],
    };
    const projection = buildOutline(groups as never, (s: string) => (s === "/regions" ? (groups as never) : null));
    const ids: string[] = [];
    const walk = (node: { id: string; children?: readonly { id: string }[] }): void => {
      ids.push(node.id);
      for (const child of node.children ?? []) walk(child as never);
    };
    for (const section of projection.sections) for (const row of section.rows) walk(row);
    const counts = new Map<string, number>();
    for (const id of ids) counts.set(id, (counts.get(id) ?? 0) + 1);
    expect([...counts].filter(([, n]) => n > 1)).toEqual([]);
  });
});

describe("the Mesh section's mesher row", () => {
  it("lists ONLY the built meshers, from the catalogue the app reads", () => {
    // **Codex's 040513**: current truthful mappings are blockMesh and snappyHexMesh; nothing else may be
    // offered. The row is built from `mesh.meshers`, so a tool whose `built` is false simply is not there.
    const rows = outlineRowsFor(null, { "virtual:outline:Mesh": true, "virtual:outline:Mesh:meshers": true }, "", [
      { id: "blockmesh", label: "blockMesh" },
      { id: "snappy", label: "snappyHexMesh" },
    ]);
    const labels = rows.map((r) => r.node.name);
    expect(labels).toContain("blockMesh");
    expect(labels).toContain("snappyHexMesh");
    // **The unbuilt ones are absent, not disabled**: cfMesh and Gmsh exist in the catalogue and are not
    // offerable, and a disabled choice is still a choice a user can try to make.
    expect(labels).not.toContain("cfMesh");
    expect(labels).not.toContain("Gmsh");
  });

  it("draws no mesher at all when the catalogue has not answered, rather than inventing names", () => {
    const rows = outlineRowsFor(null, { "virtual:outline:Mesh": true, "virtual:outline:Mesh:meshers": true }, "", []);
    expect(rows.map((r) => r.node.name)).not.toContain("blockMesh");
  });
});

describe("Results outline sources", () => {
  it("shows live virtual result rows under the Fluent-style categories", () => {
    const row = (id: string, name: string, kind: string, children: unknown[] = []) => ({
      id: `virtual:${id}`,
      name,
      type: kind,
      path: `/scenes/${id}`,
      meta: { row: kind, placeholder: false, subtitle: "available" },
      children,
    });
    const fields = row("fields", "Fields", "fields", [row("field:U_mag", "|U|", "field")]);
    const tree = { id: "root", name: "project", type: "project", path: "/", children: [
      row("slice", "Slice", "slice"), fields, row("residuals", "Residuals", "residuals"), row("monitors", "Monitors", "monitors"),
    ] } as never;
    const expanded = Object.fromEntries([
      "virtual:outline:Results", "virtual:outline:Results:surfaces", "virtual:outline:Results:graphics",
      "virtual:outline:Results:contours", "virtual:outline:Results:plots", "virtual:outline:Results:reports",
    ].map((id) => [id, true]));
    const rows = outlineRowsFor(tree, expanded, "");
    const first = rows.findIndex((r) => r.node.name === "Results");
    const last = rows.findIndex((r) => r.node.name === "Explore");
    expect(rows.slice(first, last).map((r) => r.node.name)).toEqual([
      "Results", "Surfaces", "Slice", "Graphics", "Contours", "|U|", "Vectors", "Streamlines", "Probe", "Level of detail",
      "Plots", "Residuals", "Monitors", "Reports", "HTML report",
    ]);
    expect(rows.find((r) => r.node.name === "Slice")?.node.id).toBe("virtual:slice");
    expect(rows.find((r) => r.node.name === "Residuals")?.node.id).toBe("virtual:residuals");
    expect(rows.find((r) => r.node.name === "Vectors")?.node.meta).toMatchObject({ row: "result-vectors" });
  });
});

describe("a row's node carries the REAL path of the folder it shows", () => {
  it("Materials gets /physics/materials, so the folder verbs keyed on paths match", () => {
    /*
      **Measured as an empty menu**: every outline row offered an "Actions for …" button whose menu held
      one heading and no actions, because `folderVerb(node.path)` is keyed on `/physics/materials` while
      the synthetic node's `path` was its virtual id - so "Add from library…" could never match.
    */
    const materials = { id: "g_m", name: "materials", type: "group", path: "/physics/materials", children: [] };
    const physics = { id: "g_p", name: "physics", type: "group", path: "/physics", children: [materials] };
    const projection = buildOutline({ id: "p", name: "d", type: "project", path: "/", children: [physics] } as never, (s: string) =>
      s === "/physics/materials" ? (materials as never) : null,
    );
    const rows = flattenOutline(projection, { expanded: new Set(["virtual:outline:Physics"]) });
    const line = rows.find((l) => l.label === "Materials")!;
    expect(line.sourcePath).toBe("/physics/materials");
  });

  it("a SECTION carries no source path, because it shows no folder", () => {
    const rows = flattenOutline(buildOutline(null, () => null), { expanded: new Set() });
    expect(rows[0]!.sourcePath).toBeUndefined();
  });
});

describe("a row's node is what the folder rules read", () => {
  it("canAddParameter sees /physics/parameters on an outline row, so Add parameter appears there", async () => {
    /*
      **The dispatch question** (Codex's 050456): *"actual folder verbs must target the REAL persisted
      source id, not merely a synthetic row with its path."* For a fixed folder there is exactly one
      folder of that path, so the real path IS the target: `parameter.define` takes a name and the server
      places it in the only `/physics/parameters`. Measured: adding through the outline menu created
      `/physics/parameters/bulkcheck`, and "New region…" created `/regions/solidcheck`.
    */
    const { canAddParameter } = await import("./nodeRules");
    const params = { id: "g_p", name: "parameters", type: "group", path: "/physics/parameters", children: [] };
    const projection = buildOutline(
      { id: "p", name: "d", type: "project", path: "/", children: [{ id: "g_ph", name: "physics", type: "group", path: "/physics", children: [params] }] } as never,
      (s: string) => (s === "/physics/parameters" ? (params as never) : null),
    );
    const rows = outlineRowsFor(null, {}, "", []);
    void rows;
    const lines = flattenOutline(projection, { expanded: new Set(["virtual:outline:Physics"]) });
    const line = lines.find((l) => l.label === "Parameters")!;
    const synthetic = { id: line.id, name: line.label, type: "group", path: line.sourcePath ?? "" } as never;
    expect(canAddParameter(synthetic)).toBe(true);
  });
});

describe("a user's arrangement reaches the outline", () => {
  it("drops a section whose groups are ALL hidden", () => {
    /*
      **Measured as a defect**: `arrangeTree` hides the real group nodes, which the outline does not draw,
      so `hidden: ["geometry"]` left all six sections exactly as they were - **a user who hid Geometry
      still saw it**. A section now offers only its groups that are still shown.
    */
    const projection = buildOutline(null, () => null, ["geometry"]);
    const stages = projection.sections.map((s) => s.stage);
    expect(stages).not.toContain("Geometry");
    expect(stages).toContain("Mesh");
    expect(stages).toHaveLength(5);
  });

  it("keeps a section when only SOME of its groups are hidden", () => {
    // Physics works in /physics AND /regions; hiding one of them must not remove the section.
    const projection = buildOutline(null, () => null, ["regions"]);
    expect(projection.sections.map((s) => s.stage)).toContain("Physics");
  });

  it("shows every section when nothing is hidden", () => {
    expect(buildOutline(null, () => null).sections).toHaveLength(6);
    expect(buildOutline(null, () => null, []).sections).toHaveLength(6);
  });
});

describe("hidden-source rows stay absent, the section stays", () => {
  /** A project with a real `/regions` group holding a region and its boundaries. */
  const regions = () => {
    const bnd = (id: string, name: string) => ({ id, name, type: "boundary", path: id });
    return {
      id: "g_regions",
      name: "regions",
      type: "group",
      path: "/regions",
      children: [
        {
          id: "r_fluid",
          name: "fluid",
          type: "region",
          path: "/regions/fluid",
          children: [
            {
              id: "b_fluid",
              name: "boundaries",
              type: "group",
              path: "/regions/fluid/boundaries",
              children: [bnd("/regions/fluid/boundaries/inlet", "inlet")],
            },
          ],
        },
      ],
    };
  };
  const projectionWith = (hidden: string[]) => {
    const group = regions();
    return buildOutline(group as never, (s: string) => (s === "/regions" ? (group as never) : null), hidden);
  };
  const physics = (projection: ReturnType<typeof projectionWith>) =>
    projection.sections.find((s) => s.stage === "Physics")!;

  it("keeps Physics when only /regions is hidden, and drops the rows that read it", () => {
    /*
      **Codex's 081956**: *"Include a partially hidden multi-group section (e.g. hide regions only): the
      section must remain while hidden-source rows stay absent."* Measured before: the `Regions` row kept
      its region, because only whole sections were dropped.
    */
    const projection = projectionWith(["regions"]);
    expect(projection.sections.map((s) => s.stage)).toContain("Physics");
    const labels = physics(projection).rows.map((r) => r.label);
    expect(labels).not.toContain("Regions");
    // **Both rows that read `/regions`**: Boundary conditions narrows the same group to its boundaries.
    expect(labels).not.toContain("Boundary conditions");
    // What Physics keeps: its other groups are untouched.
    expect(labels).toContain("Materials");
    expect(labels).toContain("Parameters");
  });

  it("reports the hidden rows as omitted, with a reason", () => {
    const projection = projectionWith(["regions"]);
    const reasons = projection.omitted.filter((o) => o.stage === "Physics").map((o) => o.key);
    expect(reasons).toContain("regions");
    expect(reasons).toContain("boundaries");
    for (const o of projection.omitted) expect(o.reason).not.toBe("");
  });

  it("shows every row again when nothing is hidden", () => {
    const labels = physics(projectionWith([])).rows.map((r) => r.label);
    expect(labels).toContain("Regions");
    expect(labels).toContain("Boundary conditions");
  });

  it("is unaffected by hiding a group that NO row reads", () => {
    // `studies` is Explore's only group, so hiding it drops Explore and touches nothing in Physics.
    const projection = projectionWith(["studies"]);
    expect(projection.sections.map((s) => s.stage)).not.toContain("Explore");
    expect(physics(projection).rows.map((r) => r.label)).toContain("Regions");
  });
});

describe("the Mesh section's Capping row", () => {
  it("shows the decorated tree's Capping node after Global size (ADR-0026 decision 2)", () => {
    const meshNode = { id: "n_mesh", name: "Mesh", type: "group", path: "/mesh", children: [] };
    const capping = { id: "virtual:mesh/capping", name: "Capping", type: "mesh-capping", path: "/mesh/capping", meta: { row: "mesh-capping", placeholder: true, subtitle: "no openings closed yet" } };
    const tree = { id: "root", name: "project", type: "project", path: "/", children: [meshNode, capping] } as never;
    const rows = outlineRowsFor(tree, { "virtual:outline:Mesh": true }, "");
    const names = rows.map((r) => r.node.name);
    expect(names.indexOf("Capping")).toBe(names.indexOf("Global size") + 1);
    expect(rows.find((r) => r.node.name === "Capping")?.node.id).toBe("virtual:mesh/capping");
  });
});

describe("the OpenFOAM cases row (ADR-0029)", () => {
  const node = (id: string, name: string, children?: unknown[]): any => ({ id, name, type: "group", children });
  const solveRows = (resolve: (source: string) => any) =>
    buildOutline(node("/", "root"), resolve).sections.find((s) => s.stage === "Solve")?.rows.map((r) => r.id) ?? [];

  it("is left out of a project with no imported case, not drawn as an empty folder", () => {
    const ids = solveRows((source) => (source === "/cases" ? null : node(source, source)));
    expect(ids).not.toContain("virtual:outline:Solve:cases");
  });

  it("shows the imported cases, with their real ids, when there are some", () => {
    const cases = node("/cases", "cases", [{ id: "n_case", name: "cavity", type: "foam_case" }]);
    const projection = buildOutline(node("/", "root"), (source) => (source === "/cases" ? cases : null));
    const row = projection.sections.find((s) => s.stage === "Solve")?.rows.find((r) => r.id === "virtual:outline:Solve:cases");
    expect(row?.children?.map((child) => child.id)).toEqual(["n_case"]);
  });
});

describe("the Basic tree: only the rows a first run needs", () => {
  /** The demo pipe: 4 bodies, 3 tags, no operations or parameters, the mesh tasks, water, a region with 3 boundaries. */
  const n = (id: string, name: string, path: string, type = "group", children: unknown[] = [], meta?: unknown): any => ({ id, name, type, path, children, ...(meta === undefined ? {} : { meta }) });
  const task = (id: string, kind: string) => n(`mesh/${id}`, id, `/mesh/${id}`, kind, [], { row: kind, placeholder: false, subtitle: "" });
  const pipe = (parameters: unknown[] = [], operations: unknown[] = []): any =>
    n("root", "project", "/", "project", [
      n("g_geo", "geometry", "/geometry", "group", [
        n("g_parts", "parts", "/geometry/parts", "group", ["a", "b", "c", "d"].map((x) => n(`p_${x}`, x, `/geometry/parts/${x}`, "part"))),
        n("g_tags", "tags", "/geometry/tags", "group", ["inlet", "outlet", "wall"].map((x) => n(`t_${x}`, x, `/geometry/tags/${x}`, "tag"))),
        n("g_ops", "operations", "/geometry/operations", "group", operations),
      ]),
      n("n_mesh", "Mesh settings", "/mesh", "mesh_settings", [
        task("boundaries", "mesh-boundaries"),
        task("surface", "mesh-surface"),
        task("layers", "mesh-layers"),
        task("quality", "mesh-quality"),
        task("fidelity", "mesh-fidelity"),
      ]),
      n("virtual:mesh/capping", "Capping", "/mesh/capping", "mesh-capping", [], { row: "mesh-capping", placeholder: true, subtitle: "" }),
      n("n_result", "Mesh", "/mesh/results", "mesh_result"),
      n("g_phys", "physics", "/physics", "group", [
        n("g_mats", "materials", "/physics/materials", "group", [n("m_water", "water", "/physics/materials/water", "material")]),
        n("g_params", "parameters", "/physics/parameters", "group", parameters),
      ]),
      n("g_regions", "regions", "/regions", "group", [
        n("r_fluid", "fluid", "/regions/fluid", "region", [
          n("g_b", "boundaries", "/regions/fluid/boundaries", "group", ["inlet", "outlet", "wall"].map((x) => n(`b_${x}`, x, `/regions/fluid/boundaries/${x}`, "boundary"))),
        ]),
      ]),
      n("n_solver", "Solver settings", "/solver/settings", "solver_settings"),
      n("g_runs", "runs", "/runs", "group"),
    ]);
  const meshers = [{ id: "blockmesh", label: "blockMesh" }, { id: "snappy", label: "snappyHexMesh" }];
  const open = (root: unknown): Record<string, boolean> => {
    const all: Record<string, boolean> = {};
    const full = buildOutline(root as never, resolveIn(root as never));
    for (const section of full.sections) {
      all[section.id] = true;
      const walk = (node: { id: string; children?: readonly { id: string; children?: readonly unknown[] }[] }): void => {
        all[node.id] = true;
        for (const child of node.children ?? []) walk(child as never);
      };
      section.rows.forEach(walk);
    }
    return all;
  };
  const rowsOf = (root: unknown, showAll: boolean) => outlineRowsFor(root as never, open(root), "", meshers, [], showAll);
  /** The set-up half of the tree: the four sections before Results. */
  const setUp = (rows: ReturnType<typeof rowsOf>) => {
    const stop = rows.findIndex((r) => r.node.id === "virtual:outline:Results");
    return stop < 0 ? rows : rows.slice(0, stop);
  };

  it("flags Capping, Mesher, Operations and Parameters (when empty), Reference values and the Solve pages that share one", () => {
    const flagged = (stage: "Mesh" | "Geometry" | "Physics" | "Solve") => OUTLINE[stage].filter((r) => r.advanced !== undefined).map((r) => `${r.key}:${String(r.advanced)}`);
    expect(flagged("Mesh")).toEqual(["capping:true", "meshers:true"]);
    expect(flagged("Geometry")).toEqual(["operations:whenEmpty"]);
    expect(flagged("Physics")).toEqual(["parameters:whenEmpty", "reference:true"]);
    expect(flagged("Solve")).toEqual(["controls:true", "initialisation:true", "run:true"]);
    expect(OUTLINE.Solve.filter((r) => r.advanced === undefined).map((r) => r.label)).toEqual(["OpenFOAM cases", "Settings", "Runs"]);
  });

  it("shows the pipe in at most 24 set-up rows (the four section lines aside)", () => {
    const rows = setUp(rowsOf(pipe(), false));
    expect(rows.filter((r) => r.depth > 0).length).toBeLessThanOrEqual(24);
    const names = rows.map((r) => r.node.name);
    for (const left of ["Capping", "Mesher", "blockMesh", "Operations", "Parameters", "Initialisation", "Controls", "Run", "Fidelity to CAD"]) expect(names).not.toContain(left);
    // The mesh task Boundaries is gone; Physics keeps its Boundary conditions.
    expect(names).not.toContain("boundaries");
    expect(names).toEqual(expect.arrayContaining(["Bodies", "Global size", "Mesh", "Materials", "Regions", "Boundary conditions", "Settings", "Runs"]));
  });

  it("Show all restores every row", () => {
    const basic = rowsOf(pipe(), false).map((r) => r.node.id);
    const full = rowsOf(pipe(), true).map((r) => r.node.id);
    expect(full.length).toBeGreaterThan(basic.length);
    // Everything Basic draws, Full draws too, in the same order.
    expect(full.filter((id) => basic.includes(id))).toEqual(basic);
    const names = rowsOf(pipe(), true).map((r) => r.node.name);
    for (const back of ["Capping", "Mesher", "blockMesh", "snappyHexMesh", "boundaries", "fidelity", "Controls", "Initialisation", "Run"]) expect(names).toContain(back);
  });

  it("keeps Operations and Parameters once they hold something", () => {
    const names = rowsOf(pipe([n("par", "speed", "/physics/parameters/speed", "parameter")], [n("op", "cap", "/geometry/operations/cap", "operation")]), false).map((r) => r.node.name);
    expect(names).toContain("Parameters");
    expect(names).toContain("Operations");
  });

  it("buildOutline without the flag still draws every row, and reports what Basic leaves out", () => {
    const projection = buildOutline(pipe(), resolveIn(pipe()));
    expect(projection.folded).toEqual([]);
    const basic = buildOutline(pipe(), resolveIn(pipe()), [], false);
    expect(basic.folded.map((f) => f.key)).toEqual(expect.arrayContaining(["capping", "meshers", "operations", "parameters", "controls", "initialisation", "run", "mesh/boundaries", "mesh/fidelity"]));
  });

  it("keeps every id unique in both projections", () => {
    for (const showAll of [false, true]) {
      const ids = rowsOf(pipe(), showAll).map((r) => r.node.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it("leaves a task row out of Basic without losing the others, and the full projection still finds it", () => {
    expect(rowsOf(pipe(), true).map((r) => r.node.id)).toContain("mesh/fidelity");
    const basic = rowsOf(pipe(), false).map((r) => r.node.id);
    expect(basic).not.toContain("mesh/fidelity");
    expect(basic).not.toContain("mesh/boundaries");
    expect(basic).toContain("mesh/surface");
  });
});

describe("the Solve rows open their own section", () => {
  const rows = outlineRowsFor(null, { "virtual:outline:Solve": true }, "");
  const opens = (key: string) => {
    const row = rows.find((r) => r.node.id === `virtual:outline:Solve:${key}`);
    return row === undefined ? undefined : openTargetOf(row.node);
  };

  it("Settings opens the Model section, Controls the run limits, Initialisation the initial-turbulence fields, Run the monitor", () => {
    expect(opens("methods")).toEqual({ panel: "solve", section: "solver-model" });
    expect(opens("controls")).toEqual({ panel: "solve", section: "solver-limits" });
    expect(opens("initialisation")).toEqual({ panel: "solve", section: INITIAL_TURBULENCE_SECTION });
    expect(opens("run")).toEqual({ panel: "solve", section: "run-monitor" });
  });
});
