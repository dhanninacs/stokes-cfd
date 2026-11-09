/** The rows the tree shows for every stage (T-219 B): placeholders, virtual rows, subtitles, and what stays untouched. */
import { describe, expect, it } from "vitest";
import type { ConditionKind, NodeSummary, SetupCheck } from "../api/types";
import { NO_RESULTS } from "./fieldsModel";
import type { RunSummary } from "./runApi";
import { VIRTUAL_PREFIX, boundaryRow, cappingRow, decorateTree, enterTargetOf, isPlaceholder, isVirtualId, openTargetOf, rowMetaOf, runSubtitle, solverSubtitle, subtitleOf, type TreeContext } from "./stageTreeModel";

function node(over: Partial<NodeSummary> & { id: string; path: string }): NodeSummary {
  return { type: "group", name: over.path.split("/").pop() ?? "", status: "ok", params: {}, children_count: over.children?.length ?? 0, ...over };
}

const VELOCITY: ConditionKind = {
  id: "velocity_inlet",
  label: "Velocity inlet",
  family: "inlet",
  fields: [],
  needs: {},
  color: "#fff",
  glyph: "",
  arrives: null,
  available: true,
  note: "",
  params: [
    { name: "direction", rule: "", required: false, when: {}, schema: { enum: ["normal", "components"], default: "normal" } },
    { name: "velocity", rule: "positive", required: true, when: { direction: ["normal"] }, schema: { title: "Velocity", "x-kind": "quantity" } },
    { name: "velocity_x", rule: "", required: true, when: { direction: ["components"] }, schema: { title: "Velocity x", "x-kind": "quantity" } },
  ],
};
const WALL: ConditionKind = {
  id: "wall",
  label: "Wall",
  family: "wall",
  fields: [],
  needs: {},
  color: "#888",
  glyph: "",
  arrives: null,
  available: true,
  note: "",
  params: [{ name: "shear", rule: "", required: false, when: {}, schema: { enum: ["no_slip", "slip"], default: "no_slip" } }],
};
const SYMMETRY: ConditionKind = { ...WALL, id: "symmetry", label: "Symmetry", family: "constraint", params: [] };
const KINDS = [VELOCITY, WALL, SYMMETRY];

function runOf(over: Partial<RunSummary> = {}): RunSummary {
  return { id: "r_1", name: "duct", state: "done", stale: false, iterations: 52, converged: true, seconds: 11, step: "", fraction: null, error: "", host: "local", last: [], monitors: [], ...over };
}

function ctx(over: Partial<TreeContext> = {}): TreeContext {
  return { kinds: KINDS, runs: [], results: NO_RESULTS, setup: null, ...over };
}

/** A project as a fresh `project.new` leaves it: the skeleton groups and nothing else. */
function emptyProject(): NodeSummary {
  const group = (name: string, children: NodeSummary[] = []) => node({ id: `g_${name}`, path: `/${name}`, children });
  return node({
    id: "root",
    path: "",
    children: [
      group("geometry"),
      group("mesh"),
      group("physics", [node({ id: "g_params", path: "/physics/parameters" }), node({ id: "g_mats", path: "/physics/materials", children: [node({ id: "m_air", path: "/physics/materials/air", type: "material", name: "air" })] })]),
      group("regions"),
      group("solver"),
      group("monitors"),
      group("scenes"),
      group("runs"),
      group("studies"),
    ],
  });
}

function child(root: NodeSummary | null, path: string): NodeSummary {
  const hit = root?.children?.find((c) => c.path === path);
  if (!hit) throw new Error(`no ${path}`);
  return hit;
}

describe("placeholders", () => {
  it("shows Mesh settings and Mesh result before either exists, dimmed and flagged", () => {
    const out = decorateTree(emptyProject(), ctx());
    const mesh = child(out, "/mesh");
    const names = (mesh.children ?? []).map((c) => c.name);
    expect(names).toEqual(["Mesh settings", "Capping", "Mesh result"]);
    const [settings, , result] = mesh.children ?? [];
    expect(isPlaceholder(settings)).toBe(true);
    expect(isVirtualId(settings.id)).toBe(true);
    expect(subtitleOf(settings)).toBe("not set yet");
    expect(subtitleOf(result)).toBe("not meshed");
    expect(openTargetOf(settings)).toEqual({ panel: "mesh" });
    expect(settings.status).toBe("new");
  });

  it("shows Solver settings in Solve and Flow model in Physics, both from the same absent node", () => {
    const out = decorateTree(emptyProject(), ctx());
    const solver = child(out, "/solver").children ?? [];
    expect(solver[0].name).toBe("Solver settings");
    expect(isPlaceholder(solver[0])).toBe(true);
    const physics = child(out, "/physics").children ?? [];
    expect(physics[0].name).toBe("Flow model");
    expect(isPlaceholder(physics[0])).toBe(true);
    expect(subtitleOf(physics[0])).toContain("laminar");
    // Parameters and Materials stay where they were.
    expect(physics.slice(1).map((c) => c.name)).toEqual(["parameters", "materials"]);
  });

  it("puts a real node in the placeholder's place, with no placeholder left", () => {
    const base = emptyProject();
    const settings = node({ id: "s1", path: "/solver/settings", type: "solver_settings", name: "settings", params: { iterations: 500, residual_target: 1e-4, cores: 1, host: "local", turbulence: "laminar" } });
    const solver = child(base, "/solver");
    const tree = { ...base, children: base.children?.map((c) => (c === solver ? { ...solver, children: [settings], children_count: 1 } : c)) };
    const out = decorateTree(tree, ctx());
    const kids = child(out, "/solver").children ?? [];
    expect(kids).toHaveLength(1);
    expect(kids[0].id).toBe("s1");
    expect(subtitleOf(kids[0])).toBe("500 it · 1e-4 · 1 core · local");
    const flow = (child(out, "/physics").children ?? [])[0];
    expect(isPlaceholder(flow)).toBe(false);
    expect(subtitleOf(flow)).toBe("laminar");
    expect(rowMetaOf(flow)?.row).toBe("flow-model");
  });

  it("leaves the groups it does not touch as the same objects", () => {
    const base = emptyProject();
    const out = decorateTree(base, ctx());
    expect(child(out, "/geometry")).toBe(child(base, "/geometry"));
    expect(child(out, "/monitors")).toBe(child(base, "/monitors"));
    expect(child(out, "/studies")).toBe(child(base, "/studies"));
  });
});

describe("the mesh result", () => {
  /** The project with one mesh result of the given status and parameters under /mesh/results. */
  function withResult(status: NodeSummary["status"], params: NodeSummary["params"]): NodeSummary {
    const base = emptyProject();
    const result = node({ id: "mr1", path: "/mesh/results/blockmesh", type: "mesh_result", name: "blockmesh", status, params });
    const results = node({ id: "mres", path: "/mesh/results", children: [result] });
    const mesh = child(base, "/mesh");
    return { ...base, children: base.children?.map((c) => (c === mesh ? { ...mesh, children: [results], children_count: 1 } : c)) };
  }
  const resultRow = (tree: NodeSummary): NodeSummary => {
    const out = decorateTree(tree, ctx());
    const kids = child(out, "/mesh").children ?? [];
    expect(kids.map((k) => k.name)).toEqual(["Mesh settings", "Capping", "results"]);
    return kids[2].children?.[0] as NodeSummary;
  };

  it("says the cell count and that the mesh is stale", () => {
    const shown = resultRow(withResult("stale", { state: "done", cells: 25000 }));
    expect(shown.id).toBe("mr1"); // the real id: selection and the Mesh panel are unchanged
    expect(subtitleOf(shown)).toBe("25,000 cells · stale");
  });

  it("says ok when the mesh is current and meets the limits, and quality not met when it does not", () => {
    expect(subtitleOf(resultRow(withResult("ok", { state: "done", cells: 25000, ok: true, quality: { mesh_ok: true } })))).toBe("25,000 cells · ok");
    expect(subtitleOf(resultRow(withResult("ok", { state: "done", cells: 25000, ok: false, quality: { mesh_ok: false, max_non_ortho: 80 } })))).toBe("25,000 cells · quality not met");
  });

  it("leaves a result that is not done to the mesh model's own line", () => {
    expect(subtitleOf(resultRow(withResult("ok", { state: "running" })))).toBe("");
  });
});

describe("boundaries and regions", () => {
  function boundary(params: NodeSummary["params"], displays: NodeSummary["displays"] = {}, id = "b1"): NodeSummary {
    return node({ id, path: `/regions/fluid/boundaries/${id}`, type: "boundary", name: id, params, displays });
  }

  it("reads the kind and the primary value", () => {
    expect(boundaryRow(boundary({ kind: "velocity_inlet", direction: "normal", velocity: { value: 0.1, unit: "m/s" } }, { velocity: "0.1 m/s" }), KINDS)).toEqual({ subtitle: "inlet · 0.1 m/s", missing: false });
    expect(boundaryRow(boundary({ kind: "wall" }), KINDS)).toEqual({ subtitle: "wall · no_slip", missing: false });
    expect(boundaryRow(boundary({ kind: "symmetry" }), KINDS)).toEqual({ subtitle: "symmetry", missing: false });
  });

  it("flags a value the kind needs and does not have, and a zero that must be positive", () => {
    expect(boundaryRow(boundary({ kind: "velocity_inlet" }), KINDS)).toEqual({ subtitle: "inlet · set the velocity", missing: true });
    expect(boundaryRow(boundary({ kind: "velocity_inlet", velocity: { value: 0, unit: "m/s" } }, { velocity: "0 m/s" }), KINDS).missing).toBe(true);
  });

  it("follows the switch: components ask for the component, not the normal velocity", () => {
    const row = boundaryRow(boundary({ kind: "velocity_inlet", direction: "components", velocity_x: { value: 2, unit: "m/s" } }, { velocity_x: "2 m/s" }), KINDS);
    expect(row.subtitle).toBe("inlet · 2 m/s");
  });

  it("says the kind id when the catalogue is not loaded", () => {
    expect(boundaryRow(boundary({ kind: "mass_flow_inlet" }), []).subtitle).toBe("mass flow inlet");
  });

  it("gives a region its material and its boundary count, and flags a region with no material", () => {
    const base = emptyProject();
    const inlet = boundary({ kind: "velocity_inlet", velocity: { value: 0.1, unit: "m/s" } }, { velocity: "0.1 m/s" }, "inlet");
    const wall = boundary({ kind: "wall" }, {}, "walls");
    const region = node({
      id: "reg1",
      path: "/regions/fluid",
      type: "region",
      name: "fluid",
      params: { material: "m_air", tag: "t1" },
      children: [node({ id: "bg", path: "/regions/fluid/boundaries", children: [inlet, wall] })],
    });
    const orphan = node({ id: "reg2", path: "/regions/solid", type: "region", name: "solid", params: { material: null, tag: "t2" } });
    const regions = child(base, "/regions");
    const tree = { ...base, children: base.children?.map((c) => (c === regions ? { ...regions, children: [region, orphan], children_count: 2 } : c)) };
    const out = decorateTree(tree, ctx());
    const [fluid, solid] = child(out, "/regions").children ?? [];
    expect(subtitleOf(fluid)).toBe("air · 2 boundaries");
    expect(subtitleOf(solid)).toBe("no material · 0 boundaries");
    expect(solid.status).toBe("warning");
    const boundaries = fluid.children?.[0].children ?? [];
    expect(boundaries.map(subtitleOf)).toEqual(["inlet · 0.1 m/s", "wall · no_slip"]);
    expect(boundaries[0].id).toBe("inlet"); // the real id: selection and commands are unchanged
  });

  it("turns a boundary red when setup.check names it in an error", () => {
    const base = emptyProject();
    const inlet = boundary({ kind: "velocity_inlet" }, {}, "inlet");
    const region = node({ id: "reg1", path: "/regions/fluid", type: "region", name: "fluid", params: { material: "m_air" }, children: [node({ id: "bg", path: "/regions/fluid/boundaries", children: [inlet] })] });
    const regions = child(base, "/regions");
    const tree = { ...base, children: base.children?.map((c) => (c === regions ? { ...regions, children: [region] } : c)) };
    const setup: SetupCheck = { state: "incomplete", next: "", counts: { regions: 1, materials: 1, boundaries: 1, uncovered_faces: 0, legacy: 0 }, findings: [{ code: "setup.value_missing", severity: "error", message: "x", nodes: ["inlet"] }] };
    const out = decorateTree(tree, ctx({ setup }));
    expect(child(out, "/regions").children?.[0].children?.[0].children?.[0].status).toBe("failed");
  });
});

describe("runs and results", () => {
  it("lists each run with its state and iterations", () => {
    const out = decorateTree(emptyProject(), ctx({ runs: [runOf({ id: "r_2", name: "run 2", state: "done_unconverged", iterations: 500, converged: false }), runOf()] }));
    const kids = child(out, "/runs").children ?? [];
    expect(kids.map((k) => k.name)).toEqual(["run 2", "duct"]);
    expect(kids.map(subtitleOf)).toEqual(["not converged · 500 it", "converged · 52 it"]);
    expect(kids.map((k) => k.status)).toEqual(["warning", "ok"]);
    expect(kids[0].id).toBe(`${VIRTUAL_PREFIX}run:r_2`);
    expect(rowMetaOf(kids[0])?.ref).toBe("r_2");
    expect(openTargetOf(kids[0])).toEqual({ panel: "solve", section: "run-monitor" });
  });

  it("marks a stale run and leaves /runs alone with no run", () => {
    expect(runSubtitle(runOf({ stale: true }))).toBe("converged · 52 it · stale");
    const base = emptyProject();
    expect(child(decorateTree(base, ctx()), "/runs")).toBe(child(base, "/runs"));
  });

  it("shows the four Results rows before a run, saying so", () => {
    const out = decorateTree(emptyProject(), ctx());
    const rows = child(out, "/scenes").children ?? [];
    expect(rows.map((r) => r.name)).toEqual(["Fields", "Surfaces", "Residuals", "Monitors"]);
    expect(rows.map(subtitleOf)).toEqual(["no run yet", "no run yet", "no run yet", "no run yet"]);
    expect(rows.every(isPlaceholder)).toBe(true);
    expect((rows[1].children ?? []).map((r) => r.name)).toEqual(["Slice"]);
    expect(subtitleOf(rows[1].children?.[0] as NodeSummary)).toBe("no run yet");
    expect(openTargetOf(rows[3])).toEqual({ panel: "results", section: "results-monitors" });
  });

  it("gives Fields a row per scalar field with its range once the results are read", () => {
    const results: TreeContext["results"] = {
      view: { field: "none", lock: null, slice: false },
      data: { run: "r_1", time: 52, surfaces: "ok", slice: true, fields: [{ name: "U_mag", units: "m/s", min: 0, max: 0.158 }, { name: "p", units: "m2/s2", min: -0.01, max: 0.4 }, { name: "U", units: "m/s", min: 0, max: 1 }] },
    };
    const out = decorateTree(emptyProject(), ctx({ runs: [runOf({ last: [{ name: "p", value: 8e-5 }], monitors: [{ patch: "inlet", flowRate: 1, pMean: 2 }, { patch: "outlet", flowRate: -1, pMean: 1 }] })], results }));
    const rows = child(out, "/scenes").children ?? [];
    const [fields, surfaces, residuals, monitors] = rows;
    expect(surfaces.name).toBe("Surfaces");
    expect(subtitleOf(surfaces)).toBe("slice");
    const slice = surfaces.children?.[0] as NodeSummary;
    expect(fields.children?.map((f) => f.name)).toEqual(["|U|", "p/rho"]);
    expect(fields.children?.map(subtitleOf)).toEqual(["0 to 0.158 m/s", "-0.01 to 0.4 m2/s2"]);
    expect(rowMetaOf(fields.children?.[0] as NodeSummary)?.ref).toBe("U_mag");
    expect(subtitleOf(slice)).toBe("available");
    expect(subtitleOf(residuals)).toBe("p 8e-5 · 52 it");
    expect(subtitleOf(monitors)).toBe("2 patches");
    expect(isPlaceholder(fields)).toBe(false);
  });

  it("offers Show results when a finished run has not been read", () => {
    const out = decorateTree(emptyProject(), ctx({ runs: [runOf()] }));
    expect(subtitleOf((child(out, "/scenes").children ?? [])[0])).toBe("press Show results");
  });
});

describe("capping under Mesh (ADR-0026)", () => {
  /** The project with the given cap operation nodes under /geometry/operations. */
  function withCaps(caps: NodeSummary[]): NodeSummary {
    const base = emptyProject();
    const geometry = child(base, "/geometry");
    const ops = node({ id: "g_ops", path: "/geometry/operations", children: caps });
    return { ...base, children: base.children?.map((c) => (c === geometry ? { ...geometry, children: [ops], children_count: 1 } : c)) };
  }
  const cap = (id: string, status: NodeSummary["status"]) => node({ id, path: `/geometry/operations/${id}`, type: "cap", name: id, status });

  it("shows a dimmed Capping row after the mesh settings before any cap exists", () => {
    const row = (child(decorateTree(emptyProject(), ctx()), "/mesh").children ?? [])[1];
    expect(row.name).toBe("Capping");
    expect(row.path).toBe("/mesh/capping");
    expect(isVirtualId(row.id)).toBe(true);
    expect(isPlaceholder(row)).toBe(true);
    expect(rowMetaOf(row)?.row).toBe("mesh-capping");
    expect(subtitleOf(row)).toBe("no openings closed yet");
    // Its page is the Close openings card, not the Mesh panel.
    expect(openTargetOf(row)).toEqual({ panel: null, operation: "cap" });
    expect(enterTargetOf(row)).toEqual({ panel: null, operation: "cap" });
  });

  it("reads the cap operations' own status and points at the first one, without moving them", () => {
    const tree = withCaps([cap("c1", "ok"), cap("c2", "stale")]);
    const out = decorateTree(tree, ctx());
    const row = (child(out, "/mesh").children ?? [])[1];
    expect(isPlaceholder(row)).toBe(false);
    expect(row.status).toBe("stale");
    expect(subtitleOf(row)).toBe("2 cap operations · stale");
    expect(rowMetaOf(row)?.ref).toBe("c1");
    // With a cap, the row opens that operation's own page by selecting it.
    expect(openTargetOf(row)).toEqual({ panel: null, select: "c1" });
    // The operation nodes stay in Geometry, as the same objects.
    expect(child(out, "/geometry")).toBe(child(tree, "/geometry"));
    expect(subtitleOf(cappingRow(withCaps([cap("c1", "ok")])))).toBe("1 cap operation");
    expect(cappingRow(withCaps([cap("c1", "ok"), cap("c2", "failed")])).status).toBe("failed");
  });

  it("follows a real settings node, as Fluent orders sizing then capping", () => {
    const base = emptyProject();
    const settings = node({ id: "ms", path: "/mesh/settings", type: "mesh_settings", name: "settings" });
    const mesh = child(base, "/mesh");
    const tree = { ...base, children: base.children?.map((c) => (c === mesh ? { ...mesh, children: [settings], children_count: 1 } : c)) };
    const kids = child(decorateTree(tree, ctx()), "/mesh").children ?? [];
    expect(kids.map((k) => k.name)).toEqual(["settings", "Capping", "Mesh result"]);
    expect(kids[0]).toBe(settings);
  });
});

describe("surfaces under Results (ADR-0026)", () => {
  it("groups the slice under Surfaces, keeping its id and what it opens", () => {
    const surfaces = (child(decorateTree(emptyProject(), ctx()), "/scenes").children ?? [])[1];
    expect(surfaces.path).toBe("/scenes/surfaces");
    expect(rowMetaOf(surfaces)?.row).toBe("surfaces");
    expect(openTargetOf(surfaces)).toEqual({ panel: "results", section: "results-slice" });
    const slice = surfaces.children?.[0] as NodeSummary;
    expect(slice.id).toBe(`${VIRTUAL_PREFIX}slice`);
    expect(slice.path).toBe("/scenes/surfaces/slice");
    expect(openTargetOf(slice)).toEqual({ panel: "results", section: "results-slice" });
  });
});

describe("what Enter opens", () => {
  it("opens the panel of every decorated row and of the mesh and solver settings, and leaves the rest to Properties", () => {
    const placeholder = (decorateTree(emptyProject(), ctx())?.children?.find((c) => c.path === "/mesh")?.children ?? [])[0];
    expect(enterTargetOf(placeholder)).toEqual({ panel: "mesh" });
    expect(enterTargetOf(node({ id: "a", path: "/solver/settings", type: "solver_settings" }))).toEqual({ panel: "solve", section: "solver-model" });
    expect(enterTargetOf(node({ id: "b", path: "/mesh/settings", type: "mesh_settings" }))).toEqual({ panel: "mesh" });
    // A boundary keeps Enter for its fields (a double click opens the Conditions panel); a part has no panel.
    expect(enterTargetOf(node({ id: "c", path: "/regions/f/boundaries/i", type: "boundary" }))).toBeNull();
    expect(enterTargetOf(node({ id: "d", path: "/geometry/p", type: "part" }))).toBeNull();
  });
});

describe("what a row opens", () => {
  it("maps the real set-up nodes to their panels and a plain node to none", () => {
    expect(openTargetOf(node({ id: "a", path: "/solver/settings", type: "solver_settings" }))).toEqual({ panel: "solve", section: "solver-model" });
    expect(openTargetOf(node({ id: "b", path: "/mesh/settings", type: "mesh_settings" }))).toEqual({ panel: "mesh" });
    expect(openTargetOf(node({ id: "c", path: "/regions/f/boundaries/i", type: "boundary" }))).toEqual({ panel: "conditions" });
    expect(openTargetOf(node({ id: "d", path: "/geometry/p", type: "part" }))).toBeNull();
  });
  it("opens each graphics/report shortcut at its matching Results control", () => {
    const target = (row: string) => openTargetOf(node({ id: `v_${row}`, path: `/virtual/${row}`, meta: { row, placeholder: false, subtitle: "" } }));
    expect(target("result-vectors")).toEqual({ panel: "results", section: "results-arrows" });
    expect(target("result-streamlines")).toEqual({ panel: "results", section: "results-streamlines" });
    expect(target("result-probe")).toEqual({ panel: "results", section: "results-probe" });
    expect(target("result-report")).toEqual({ panel: "results", section: "results-report" });
    expect(target("result-lod")).toEqual({ panel: "results", section: "results-lod" });
  });
  it("sums up the solver settings in one line, whichever it has", () => {
    expect(solverSubtitle(node({ id: "s", path: "/solver/settings", params: { iterations: 12000, cores: 2 } }))).toBe("12,000 it · 2 cores");
  });
});
