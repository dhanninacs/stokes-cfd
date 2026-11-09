/**
 * The flow strip's model, against hand-built trees: an empty project, a body imported but no fluid, a
 * fluid with no material, boundaries missing, ready, meshed, running, done and results loaded.
 */
import { describe, expect, it } from "vitest";
import type { NodeSummary, SetupCheck, UncoveredAnswer } from "../api/types";
import type { RunSummary } from "../panels/runApi";
import { GUIDED_STEPS, STEP_STAGE, faceRowText, guidedView, nextStepId, reportBlocker, resultChoices, runLine, stepFromName, type GuidedInput } from "./guidedModel";
import { defaultFluidBody, readBodies, readBoundaries, readCase, readFluidRegion, unassignedFaces } from "./guidedTree";

function node(id: string, type: string, path: string, params: Record<string, unknown> = {}, children?: NodeSummary[], extra: Partial<NodeSummary> = {}): NodeSummary {
  return {
    id,
    type,
    name: path.split("/").pop() ?? id,
    status: "ok",
    params: params as NodeSummary["params"],
    path,
    children_count: children?.length ?? 0,
    children,
    ...extra,
  };
}

interface TreeOptions {
  parts?: { id: string; name: string; simulate?: boolean }[];
  domainPart?: string | null;
  settings?: boolean;
  region?: { material: string | null } | null;
  boundaries?: { id: string; kind: string; tag: string; values?: Record<string, unknown>; displays?: Record<string, string> }[];
  mesh?: { state: string; status?: NodeSummary["status"]; cells?: number } | null;
}

function tree(o: TreeOptions = {}): NodeSummary {
  const parts = (o.parts ?? []).map((p) => node(p.id, "part", `/geometry/parts/${p.name}`, { simulate: p.simulate !== false }));
  const settings = o.settings === false ? [] : [node("s1", "mesh_settings", "/mesh/settings", { domain_part: o.domainPart ?? null })];
  const results = o.mesh
    ? [node("rs", "group", "/mesh/results", {}, [node("r1", "mesh_result", "/mesh/results/blockmesh", { state: o.mesh.state, cells: o.mesh.cells ?? 0 }, undefined, { status: o.mesh.status ?? "ok" })])]
    : [];
  const boundaries = (o.boundaries ?? []).map((b) => node(b.id, "boundary", `/regions/fluid/boundaries/${b.id}`, { kind: b.kind, tag: b.tag, ...(b.values ?? {}) }, undefined, { displays: b.displays }));
  const regions =
    o.region === null || o.region === undefined
      ? []
      : [node("reg1", "region", "/regions/fluid", { kind: "fluid", material: o.region.material }, [node("bg", "group", "/regions/fluid/boundaries", {}, boundaries)])];
  return node("root", "group", "/", {}, [
    node("g", "group", "/geometry", {}, [node("gp", "group", "/geometry/parts", {}, parts), node("gt", "group", "/geometry/tags", {}, [node("tag_in", "tag", "/geometry/tags/inlet", { color: "#3b82f6" })])]),
    node("m", "group", "/mesh", {}, [...settings, ...results]),
    node("ph", "group", "/physics", {}, [node("mg", "group", "/physics/materials", {}, [node("mat1", "material", "/physics/materials/water", {})])]),
    node("rg", "group", "/regions", {}, regions),
  ]);
}

const READY: SetupCheck = { state: "ready", next: "", counts: { regions: 1, materials: 1, boundaries: 2, uncovered_faces: 1, legacy: 0 }, findings: [] };
const NEEDS_OUTLET: SetupCheck = { state: "incomplete", next: "needs an outlet", counts: { regions: 1, materials: 1, boundaries: 1, uncovered_faces: 4, legacy: 0 }, findings: [] };

function input(over: Partial<GuidedInput> = {}): GuidedInput {
  return {
    tree: tree(),
    hasProject: true,
    importing: false,
    setup: null,
    meshPhase: "none",
    meshStale: false,
    meshCells: 0,
    runs: [],
    resultsLoaded: false,
    fieldChosen: false,
    ...over,
  };
}

const states = (i: GuidedInput) => Object.fromEntries(guidedView(i).steps.map((s) => [s.id, s.state]));

const PIPE = [
  { id: "p1", name: "fluid" },
  { id: "p2", name: "wall_tube" },
];

describe("the flow strip's steps", () => {
  it("lists the six steps in the order of the work", () => {
    expect(guidedView(input()).steps.map((s) => s.id)).toEqual([...GUIDED_STEPS]);
    expect(guidedView(input()).steps.map((s) => s.label)).toEqual(["Geometry", "Fluid", "Boundaries", "Mesh", "Run", "Results"]);
  });

  it("an empty project waits for an import", () => {
    const view = guidedView(input());
    expect(view.active).toBe("geometry");
    expect(view.steps[0]).toMatchObject({ state: "todo", hint: "Import a CAD file" });
    expect(states(input())).toEqual({ geometry: "todo", fluid: "todo", boundaries: "todo", mesh: "todo", run: "todo", results: "todo" });
  });

  it("no project says so, and an import in flight is running", () => {
    expect(guidedView(input({ hasProject: false })).steps[0].hint).toBe("Open or create a project");
    expect(guidedView(input({ importing: true })).steps[0].state).toBe("running");
  });

  it("bodies imported but none chosen as the fluid needs attention", () => {
    const i = input({ tree: tree({ parts: PIPE }) });
    const view = guidedView(i);
    expect(view.steps[0]).toMatchObject({ state: "attention", hint: "Choose which body is the fluid" });
    expect(view.active).toBe("geometry");
    expect(states(i).fluid).toBe("todo");
  });

  it("a fluid body finishes Geometry and opens Fluid with no material", () => {
    const i = input({ tree: tree({ parts: PIPE, domainPart: "p1" }) });
    const view = guidedView(i);
    expect(view.steps[0]).toMatchObject({ state: "done", hint: "2 bodies · fluid is the fluid" });
    expect(view.steps[1]).toMatchObject({ state: "attention", hint: "Pick the fluid in fluid" });
    expect(view.active).toBe("fluid");
  });

  it("a fluid region with a material finishes Fluid and moves on to Boundaries", () => {
    const i = input({ tree: tree({ parts: PIPE, domainPart: "p1", region: { material: "mat1" } }) });
    const view = guidedView(i);
    expect(view.steps[1]).toMatchObject({ state: "done", hint: "Water" });
    expect(view.steps[2].state).toBe("todo");
    expect(view.active).toBe("boundaries");
  });

  it("boundaries missing: the set-up check's own sentence is the hint", () => {
    const i = input({
      tree: tree({ parts: PIPE, domainPart: "p1", region: { material: "mat1" }, boundaries: [{ id: "b1", kind: "velocity_inlet", tag: "tag_in" }] }),
      setup: NEEDS_OUTLET,
    });
    const step = guidedView(i).steps[2];
    expect(step).toMatchObject({ state: "attention", hint: "Needs an outlet" });
    expect(guidedView(i).active).toBe("boundaries");
  });

  it("ready: boundaries done, with the faces left as wall counted", () => {
    const i = input({
      tree: tree({ parts: PIPE, domainPart: "p1", region: { material: "mat1" }, boundaries: [{ id: "b1", kind: "velocity_inlet", tag: "t" }, { id: "b2", kind: "pressure_outlet", tag: "t" }] }),
      setup: READY,
    });
    const view = guidedView(i);
    expect(view.steps[2]).toMatchObject({ state: "done", hint: "2 boundaries · 1 face left as wall" });
    expect(view.steps[3]).toMatchObject({ state: "todo", hint: "Choose a size and generate" });
    expect(view.active).toBe("mesh");
  });

  const SETUP_DONE = { tree: tree({ parts: PIPE, domainPart: "p1", region: { material: "mat1" } }), setup: READY };

  it("meshing is running, a made mesh is done with its cell count, a stale one needs attention", () => {
    expect(guidedView(input({ ...SETUP_DONE, meshPhase: "running" })).steps[3]).toMatchObject({ state: "running" });
    const done = guidedView(input({ ...SETUP_DONE, meshPhase: "done", meshCells: 123456 }));
    expect(done.steps[3]).toMatchObject({ state: "done", hint: "123,456 cells" });
    expect(done.active).toBe("run");
    expect(guidedView(input({ ...SETUP_DONE, meshPhase: "done", meshStale: true })).steps[3]).toMatchObject({ state: "attention", hint: "Out of date: build it again" });
    expect(guidedView(input({ ...SETUP_DONE, meshPhase: "failed" })).steps[3].state).toBe("attention");
  });

  it("a run in flight is running; a failed one needs attention", () => {
    const meshed = { ...SETUP_DONE, meshPhase: "done" as const, meshCells: 10 };
    expect(guidedView(input({ ...meshed, runs: [{ state: "running", stale: false, iterations: 40, converged: false }] })).steps[4]).toMatchObject({ state: "running", hint: "Iteration 40" });
    expect(guidedView(input({ ...meshed, runs: [{ state: "failed", stale: false, iterations: 3, converged: false }] })).steps[4].state).toBe("attention");
  });

  it("a finished run is done; out of date it needs attention; not converged says so", () => {
    const meshed = { ...SETUP_DONE, meshPhase: "done" as const, meshCells: 10 };
    const ok = guidedView(input({ ...meshed, runs: [{ state: "done", stale: false, iterations: 212, converged: true }] }));
    expect(ok.steps[4]).toMatchObject({ state: "done", hint: "Converged in 212 iterations" });
    expect(ok.active).toBe("results");
    expect(guidedView(input({ ...meshed, runs: [{ state: "done", stale: true, iterations: 212, converged: true }] })).steps[4].state).toBe("attention");
    expect(guidedView(input({ ...meshed, runs: [{ state: "done_unconverged", stale: false, iterations: 1, converged: false }] })).steps[4].hint).toBe("Stopped at 1 iteration");
  });

  it("results: read but no field chosen needs attention, a field makes every step done", () => {
    const finished = { ...SETUP_DONE, meshPhase: "done" as const, meshCells: 10, runs: [{ state: "done" as const, stale: false, iterations: 5, converged: true }] };
    expect(guidedView(input(finished)).steps[5]).toMatchObject({ state: "todo", hint: "Show the fields of the run" });
    expect(guidedView(input({ ...finished, resultsLoaded: true })).steps[5].state).toBe("attention");
    const all = guidedView(input({ ...finished, resultsLoaded: true, fieldChosen: true }));
    expect(all.steps.every((s) => s.state === "done")).toBe(true);
    expect(all.active).toBe("results");
  });
});

describe("what the tree says", () => {
  it("reads each body's role the way the Mesh stepper does", () => {
    const t = tree({ parts: [...PIPE, { id: "p3", name: "spare", simulate: false }], domainPart: "p1" });
    expect(readBodies(t).map((b) => [b.name, b.role])).toEqual([
      ["fluid", "fluid"],
      ["wall_tube", "solid"],
      ["spare", "left_out"],
    ]);
  });

  it("guesses the fluid from its name, or from there being only one body, and never over a choice", () => {
    expect(defaultFluidBody(readBodies(tree({ parts: PIPE })))?.id).toBe("p1");
    expect(defaultFluidBody(readBodies(tree({ parts: [{ id: "a", name: "block" }] })))?.id).toBe("a");
    expect(defaultFluidBody(readBodies(tree({ parts: [{ id: "a", name: "plate" }, { id: "b", name: "shell" }] })))).toBeNull();
    expect(defaultFluidBody(readBodies(tree({ parts: PIPE, domainPart: "p2" })))).toBeNull();
  });

  it("finds the fluid region and the material's name", () => {
    expect(readFluidRegion(tree({ region: { material: "mat1" } }))).toMatchObject({ materialId: "mat1", materialName: "Water" });
    expect(readFluidRegion(tree({ region: { material: null } }))).toMatchObject({ materialId: null, materialName: null });
    expect(readFluidRegion(tree())).toBeNull();
  });

  it("lists boundaries with the swatch of their tag, the kind in words and an editable value", () => {
    const rows = readBoundaries(
      tree({
        region: { material: null },
        boundaries: [
          { id: "b1", kind: "velocity_inlet", tag: "tag_in", values: { velocity: { value: 0.5, unit: "m/s" } }, displays: { velocity: "0.5 m/s" } },
          { id: "b2", kind: "pressure_outlet", tag: "x", values: { pressure: 0 } },
          { id: "b3", kind: "wall", tag: "x" },
        ],
      }),
    );
    expect(rows[0]).toMatchObject({ word: "Inlet", color: "#3b82f6", valueText: "0.5 m/s", value: 0.5, editable: { param: "velocity", unit: "m/s" } });
    expect(rows[1]).toMatchObject({ word: "Outlet", value: 0, valueText: "0 Pa" });
    expect(rows[2]).toMatchObject({ word: "Wall", editable: null, valueText: "" });
  });

  it("reads an inlet's temperature, and none for a kind that takes no inflow", () => {
    const rows = readBoundaries(
      tree({
        region: { material: null },
        boundaries: [
          { id: "b1", kind: "velocity_inlet", tag: "x", values: { velocity: 0.4, temperature: { value: 293.15, unit: "K" } } },
          { id: "b2", kind: "pressure_outlet", tag: "x", values: { pressure: 0, temperature: { value: 300, unit: "K" } } },
          { id: "b3", kind: "wall", tag: "x" },
        ],
      }),
    );
    expect(rows[0]!.temperature).toBe(293.15);
    expect(rows[1]!.temperature).toBeNull();
    expect(rows[2]!.temperature).toBeNull();
  });

  it("names two boundaries of one kind, so two inlets can be told apart", () => {
    const rows = readBoundaries(
      tree({
        region: { material: null },
        boundaries: [
          { id: "b1", kind: "velocity_inlet", tag: "x", values: { velocity: 0.4 } },
          { id: "b2", kind: "velocity_inlet", tag: "x", values: { velocity: 1.2 } },
          { id: "b3", kind: "wall", tag: "x" },
        ],
      }),
    );
    expect(rows.map((r) => r.word)).toEqual([rows[0]!.name, rows[1]!.name, "Wall"]);
  });
});

describe("step names", () => {
  it("knows its own ids and the older names", () => {
    expect(stepFromName("boundaries")).toBe("boundaries");
    expect(stepFromName("define")).toBe("boundaries");
    expect(stepFromName("import")).toBe("geometry");
    expect(stepFromName("material")).toBe("fluid");
    expect(stepFromName("nonsense")).toBeNull();
  });

  it("chains the steps and names the stage each works in", () => {
    expect(nextStepId("geometry")).toBe("fluid");
    expect(nextStepId("results")).toBeNull();
    expect(STEP_STAGE.boundaries).toBe("Physics");
    expect(STEP_STAGE.results).toBe("Results");
  });
});

const record = (o: Partial<RunSummary>): RunSummary => ({ id: "r1", name: "", state: "running", stale: false, iterations: 0, converged: false, seconds: 0, step: "", fraction: null, error: "", host: "", last: [], monitors: [], ...o });

describe("the Run card's state line", () => {
  it("says running, with the iteration, for a record still marked queued that reports iterations", () => {
    expect(runLine(record({ state: "queued", iterations: 108 }), 500)).toEqual({ text: "Running · iteration 108", tone: "busy" });
    expect(runLine(record({ state: "running", iterations: 12 }), null).text).toBe("Running · iteration 12");
  });

  it("keeps Waiting to start for a queued run that has reported nothing, and says Stopping after Stop", () => {
    expect(runLine(record({ state: "queued" }), 500).text).toBe("Waiting to start");
    expect(runLine(record({ state: "cancelling", iterations: 30 }), 500)).toEqual({ text: "Stopping…", tone: "busy" });
  });

  it("leaves the finished wording to the Solve panel's", () => {
    expect(runLine(record({ state: "done", iterations: 52 }), 500).tone).toBe("ok");
    expect(runLine(null, null).text).toBe("No run yet");
  });
});

describe("the faces nobody assigned", () => {
  const answer: UncoveredAnswer = {
    parts: [
      { part: "p1", name: "fluid", path: "/geometry/parts/fluid", total: 12, faces: 4, ranges: [[3, 4], [9, 10]] },
      { part: "p2", name: "solid", path: "/geometry/parts/solid", total: 6, faces: 6, ranges: [[0, 5]] },
    ],
    faces: 10,
  };

  it("lists the fluid body's faces from the server's ranges, and no other body's", () => {
    expect(unassignedFaces(answer, "p1")).toEqual({ ids: [3, 4, 9, 10], more: 0 });
    expect(unassignedFaces(answer, "nope")).toEqual({ ids: [], more: 0 });
    expect(unassignedFaces(null, "p1")).toEqual({ ids: [], more: 0 });
  });

  it("caps the list and counts the rest without expanding a large range", () => {
    const big: UncoveredAnswer = { parts: [{ part: "p1", name: "f", path: "/f", total: 100000, faces: 100000, ranges: [[0, 99999]] }], faces: 100000 };
    expect(unassignedFaces(big, "p1", 3)).toEqual({ ids: [0, 1, 2], more: 99997 });
  });

  it("words a row", () => {
    expect(faceRowText(7)).toBe("Face 7 · not assigned");
    expect(faceRowText(2, { area: 0.0039270, flat: true })).toBe("Face 2 · flat · 39.3 cm²");
    expect(faceRowText(5, { area: 0.0412, flat: false })).toBe("Face 5 · curved · 412 cm²");
    expect(faceRowText(1, { area: 2.45e-5, flat: true })).toBe("Face 1 · flat · 24.5 mm²");
  });
});

describe("the Results card", () => {
  it("offers Velocity and Pressure before the fields are read, and only the ones the run has after", () => {
    expect(resultChoices(null).map((c) => c.label)).toEqual(["Velocity", "Pressure"]);
    expect(resultChoices(["p", "k"]).map((c) => c.value)).toEqual(["p"]);
  });

  it("keeps Report off until a run is done", () => {
    expect(reportBlocker(false, false)).toBe("Finish a run first");
    expect(reportBlocker(true, false)).not.toBeNull();
    expect(reportBlocker(true, true)).toBeNull();
  });
});

describe("an imported OpenFOAM case", () => {
  const caseTree = (status = "ready"): NodeSummary =>
    node("root", "group", "/", {}, [
      node("g", "group", "/geometry", {}, [node("gp", "group", "/geometry/parts", {}, [])]),
      node("cs", "group", "/cases", {}, [node("c1", "foam_case", "/cases/cavity", { solver: "incompressibleFluid", status })]),
    ]);

  it("is read from the tree, with its solver and status", () => {
    expect(readCase(caseTree())).toEqual({ id: "c1", name: "cavity", solver: "incompressibleFluid", status: "ready" });
    expect(readCase(tree())).toBeNull();
    expect(readCase(null)).toBeNull();
  });

  it("stands in for geometry, fluid, boundaries and mesh: nothing to import, pick or name", () => {
    // Measured 2026-11-01: a tutorial opened in Guided mode said "Import a CAD file", "Choose the
    // fluid body first" and "0 cells", with the CAD drop hint over the viewport.
    const view = guidedView(input({ tree: caseTree() }));
    expect(view.steps.map((s) => [s.id, s.state])).toEqual([
      ["geometry", "done"],
      ["fluid", "done"],
      ["boundaries", "done"],
      ["mesh", "todo"],
      ["run", "todo"],
      ["results", "todo"],
    ]);
    expect(view.steps[0].hint).toBe("cavity · incompressibleFluid");
    expect(view.steps[1].hint).toBe("From the case");
    // A case has no CAD, so it lands on Mesh with Generate (the case's own mesh steps).
    expect(view.steps[3].hint).toBe("Press Generate to build the case's mesh");
    expect(view.steps[4].hint).toBe("Build the mesh first");
    expect(view.active).toBe("mesh");
  });

  it("marks the mesh made once a run finished, and a case that is not ready needs attention", () => {
    const ran = guidedView(input({ tree: caseTree(), runs: [{ state: "done_unconverged", stale: false, iterations: 2000, converged: false }] }));
    expect(ran.steps[3]).toMatchObject({ state: "done", hint: "Built from the case's own mesh steps" });
    expect(ran.steps[4].state).toBe("done");
    const broken = guidedView(input({ tree: caseTree("needs_recipe") }));
    expect(broken.steps[0]).toMatchObject({ state: "attention", hint: "cavity · incompressibleFluid · needs recipe" });
  });

  it("follows a mesh-only run in the Mesh step and never counts it as a run", () => {
    const mesh = (state: "running" | "done" | "failed") => ({ state, stale: false, iterations: 0, converged: false, kind: "mesh" });
    const building = guidedView(input({ tree: caseTree(), runs: [mesh("running")] }));
    expect(building.steps[3]).toMatchObject({ state: "running", hint: "Building the mesh" });
    const built = guidedView(input({ tree: caseTree(), runs: [mesh("done")] }));
    expect(built.steps[3]).toMatchObject({ state: "done" });
    expect(built.steps[4]).toMatchObject({ state: "todo", hint: "Press Run" });
    expect(built.active).toBe("run");
    const failed = guidedView(input({ tree: caseTree(), runs: [mesh("failed")] }));
    expect(failed.steps[3].state).toBe("attention");
    expect(failed.steps[4].state).toBe("todo");
  });
});
