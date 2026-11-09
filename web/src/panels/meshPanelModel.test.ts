/**
 * The Mesh panel's logic (T-211b): every state of a result, every quality verdict, the button
 * enablement and its reasons, the events and the live log, the settings form's helpers.
 */
import { describe, expect, it } from "vitest";
import { FINENESS, SWITCH_LAYERS, baseSizeFor, finenessOf, type Box } from "../guided/sizing";
import { finenessChoices, layersFor } from "../setup/meshBasic";
import type { NodeSummary } from "../api/types";
import {
  ASPECT_RATIO_LIMIT,
  LOG_CAP,
  MESH_PHASES,
  NO_LIVE,
  applyMeshEvent,
  boxEntry,
  boxFilled,
  boxTexts,
  boxUnit,
  boxValue,
  cancelState,
  fidelityRows,
  fidelityVerdict,
  findMeshNodes,
  formatCount,
  formatSeconds,
  generateState,
  isActive,
  isStale,
  keptMeshes,
  lengthUnitOf,
  logText,
  mappingRows,
  mergeLogTail,
  meshTreeValue,
  phaseNow,
  progressOf,
  qualityRows,
  readFidelity,
  readMeshEvent,
  readMeshers,
  readResult,
  shouldPoll,
  stateLine,
  stepsOf,
  verdictOf,
  visibleSettings,
  worstVerdict,
  type GenerateInput,
  type MeshLive,
  type MeshPhase,
  type MesherInfo,
} from "./meshPanelModel";

const QUALITY = {
  cells: 12000,
  max_non_ortho: 54.2,
  max_skewness: 1.3,
  max_aspect_ratio: 4.5,
  failed_checks: 0,
  mesh_ok: true,
  thresholds: { max_non_ortho: 70, max_skewness: 4 },
};

function node(state: string, extra: Partial<NodeSummary> & { params?: Record<string, unknown> } = {}): NodeSummary {
  const { params, ...rest } = extra;
  return {
    id: "n_00000001",
    type: "mesh_result",
    name: "blockmesh",
    status: "ok",
    path: "/mesh/results/blockmesh",
    children_count: 0,
    params: { mesher: "blockmesh", host: "local", state, cells: 12000, points: 13000, faces: 36000, seconds: 3.2, ok: true, error: "", quality: QUALITY, ...params } as NodeSummary["params"],
    meta: { warnings: ["tag fins lies inside the domain; blockMesh cannot mesh it"], patch_tags: { xmin: "t_00000001", xmax: "t_00000002" } },
    ...rest,
  };
}

const BLOCK: MesherInfo = { id: "blockmesh", label: "blockMesh", built: true, domains: ["part", "box"], why: "" };
const SNAPPY: MesherInfo = { id: "snappy", label: "snappyHexMesh", built: false, domains: ["part"], why: "snappyHexMesh arrives in T-150" };

function input(over: Partial<GenerateInput> = {}): GenerateInput {
  return { connected: true, hasProject: true, phase: "none", stale: false, domain: "part", hasPart: true, boxFilled: false, mesher: BLOCK, host: { name: "local", available: true, why: "" }, busy: false, ...over };
}

describe("the result", () => {
  it("reads every state of the schema and treats none as no result", () => {
    for (const state of MESH_PHASES.filter((p) => p !== "none")) expect(readResult(node(state))?.phase).toBe(state);
    expect(readResult(null)).toBeNull();
    expect(isActive("queued") && isActive("running") && isActive("cancelling")).toBe(true);
    for (const p of ["none", "done", "failed", "cancelled", "interrupted", "skipped"] as MeshPhase[]) expect(isActive(p)).toBe(false);
  });

  it("reads the counts, the quality and the meta", () => {
    const view = readResult(node("done"));
    expect(view).toMatchObject({ cells: 12000, points: 13000, faces: 36000, seconds: 3.2, ok: true, hasQuality: true });
    expect(view?.quality.maxNonOrtho).toBe(54.2);
    expect(view?.quality.limitSkewness).toBe(4);
    expect(view?.warnings).toEqual(["tag fins lies inside the domain; blockMesh cannot mesh it"]);
    expect(view?.patchTags).toEqual({ xmin: "t_00000001", xmax: "t_00000002" });
  });

  it("survives a result with nothing in it", () => {
    const bare = readResult({ id: "n_1", type: "mesh_result", name: "x", status: "new", path: "/mesh/results/x", children_count: 0, params: {} });
    expect(bare).toMatchObject({ phase: "queued", cells: 0, hasQuality: false, warnings: [] });
    expect(qualityRows(bare)).toEqual([]);
  });

  it("finds the settings and the result in the tree", () => {
    const settings: NodeSummary = { id: "n_s", type: "mesh_settings", name: "settings", status: "ok", path: "/mesh/settings", children_count: 0, params: {} };
    const results: NodeSummary = { id: "g_r", type: "group", name: "results", status: "ok", path: "/mesh/results", children_count: 1, params: {}, children: [node("done")] };
    const tree = { id: "root", type: "group", name: "", status: "ok", path: "", children_count: 1, params: {}, children: [{ id: "g_m", type: "group", name: "mesh", status: "ok", path: "/mesh", children_count: 2, params: {}, children: [settings, results] }] } as NodeSummary;
    expect(findMeshNodes(tree).settings?.id).toBe("n_s");
    expect(findMeshNodes(tree).result?.type).toBe("mesh_result");
    expect(findMeshNodes(null)).toEqual({ settings: null, result: null });
  });

  it("lists the kept meshes for the picker, one per mesher, only when there is a choice (ADR-0034)", () => {
    const tet = node("done", { id: "n_tet", name: "cfmesh_tet", path: "/mesh/results/cfmesh_tet", params: { mesher: "cfmesh_tet", cells: 276113 } });
    const poly = node("done", { id: "n_poly", name: "cfmesh_poly", path: "/mesh/results/cfmesh_poly", params: { mesher: "cfmesh_poly", cells: 79149, active: true } });
    const failed = node("failed", { id: "n_snap", name: "snappy", path: "/mesh/results/snappy", params: { mesher: "snappy" } });
    const treeOf = (...kids: NodeSummary[]): NodeSummary =>
      ({ id: "root", type: "group", name: "", status: "ok", path: "", children_count: 1, params: {}, children: [{ id: "g_m", type: "group", name: "mesh", status: "ok", path: "/mesh", children_count: 1, params: {}, children: [{ id: "g_r", type: "group", name: "results", status: "ok", path: "/mesh/results", children_count: kids.length, params: {}, children: kids }] }] }) as NodeSummary;
    const labels = new Map([["cfmesh_tet", "Tetrahedral (cfMesh tetMesh)"]]);
    const kept = keptMeshes(treeOf(tet, poly, failed), labels);
    expect(kept.map((k) => [k.id, k.active])).toEqual([["n_tet", false], ["n_poly", true]]); // the failed one has nothing to show
    expect(kept[0].label).toBe("Tetrahedral (cfMesh tetMesh) · 276,113 cells");
    expect(kept[1].label).toMatch(/^cfmesh_poly · /); // no label known: the mesher id
    expect(findMeshNodes(treeOf(tet, poly, failed)).result?.id).toBe("n_poly");
    expect(keptMeshes(treeOf(poly), labels)).toEqual([]); // one mesh is no choice
  });

  it("is stale only when a finished mesh says so", () => {
    expect(isStale(readResult(node("done", { status: "stale" })))).toBe(true);
    expect(isStale(readResult(node("done")))).toBe(false);
    expect(isStale(readResult(node("running", { status: "stale" })))).toBe(false);
    expect(isStale(null)).toBe(false);
  });
});

describe("the state line", () => {
  const live = (step: string, stage = "running"): MeshLive => ({ ...NO_LIVE, nodeId: "n_00000001", step, stage });
  it("says every state in words", () => {
    expect(stateLine(null, NO_LIVE)).toEqual({ text: "No mesh yet", tone: "neutral" });
    expect(stateLine(readResult(node("queued")), NO_LIVE)).toEqual({ text: "Waiting to start", tone: "busy" });
    expect(stateLine(readResult(node("cancelling")), NO_LIVE)).toEqual({ text: "Stopping", tone: "busy" });
    expect(stateLine(readResult(node("cancelled")), NO_LIVE)).toEqual({ text: "Cancelled", tone: "neutral" });
    expect(stateLine(readResult(node("interrupted")), NO_LIVE).tone).toBe("warn");
    expect(stateLine(readResult(node("interrupted")), NO_LIVE).text).toMatch(/agent stopped reporting/);
  });

  it("names the step that runs, with its place when the mesher's steps are known", () => {
    expect(stateLine(readResult(node("running")), live("blockMesh")).text).toBe("Meshing: step 1 of 2 (blockMesh)");
    expect(stateLine(readResult(node("running")), live("checkMesh")).text).toBe("Meshing: step 2 of 2 (checkMesh)");
    expect(stateLine(readResult(node("running")), NO_LIVE).text).toBe("Meshing");
    expect(stateLine(readResult(node("running", { params: { mesher: "gmsh" } })), live("mesh")).text).toBe("Meshing: mesh");
  });

  it("reports a finished mesh with its cells and time, and says when the quality missed the bar or the mesh is old", () => {
    expect(stateLine(readResult(node("done")), NO_LIVE)).toEqual({ text: "Finished: 12,000 cells in 3.2 s", tone: "ok" });
    expect(stateLine(readResult(node("done", { params: { ok: false } })), NO_LIVE)).toEqual({ text: "Finished: 12,000 cells in 3.2 s. Quality not met", tone: "warn" });
    expect(stateLine(readResult(node("done", { status: "stale" })), NO_LIVE)).toEqual({ text: "Finished: 12,000 cells in 3.2 s. Out of date", tone: "warn" });
  });

  it("carries the server's words for a failure and a skip", () => {
    expect(stateLine(readResult(node("failed", { params: { error: "mesh.error: blockMesh returned 1" } })), NO_LIVE)).toEqual({ text: "Failed: mesh.error: blockMesh returned 1", tone: "bad" });
    expect(stateLine(readResult(node("failed", { params: { error: "" } })), NO_LIVE).text).toBe("Failed");
    expect(stateLine(readResult(node("skipped", { params: { error: "mesh.no_host: the host has no OpenFOAM install" } })), NO_LIVE)).toEqual({ text: "Skipped: the host has no OpenFOAM install", tone: "warn" });
    expect(stateLine(readResult(node("skipped", { params: { error: "" } })), NO_LIVE).text).toBe("Skipped: no host was available");
  });
});

describe("a cancel that was asked for", () => {
  it("reads as stopping while the node still says running, and only for the run it was asked of", () => {
    const view = readResult(node("running"));
    const stopping: MeshLive = { ...NO_LIVE, nodeId: "n_00000001", step: "blockMesh", stage: "cancelling" };
    expect(phaseNow(view, stopping)).toBe("cancelling");
    expect(stateLine(view, stopping)).toEqual({ text: "Stopping", tone: "busy" });
    expect(cancelState(phaseNow(view, stopping)).enabled).toBe(false);
    expect(phaseNow(view, { ...stopping, nodeId: "n_other" })).toBe("running");
    expect(phaseNow(readResult(node("done")), stopping)).toBe("done");
    expect(phaseNow(null, stopping)).toBe("none");
  });
});

describe("progress", () => {
  const live = (step: string, stage: string): MeshLive => ({ ...NO_LIVE, nodeId: "n_00000001", step, stage });
  it("comes from the step's name, so a step that ended does not read as the job", () => {
    const running = readResult(node("running"));
    expect(progressOf(running, live("blockMesh", "running")).fraction).toBe(0);
    expect(progressOf(running, live("blockMesh", "done")).fraction).toBe(0.5);
    expect(progressOf(running, live("checkMesh", "running")).fraction).toBe(0.5);
    expect(progressOf(running, live("checkMesh", "done")).fraction).toBe(1);
    expect(progressOf(running, NO_LIVE).fraction).toBeNull();
  });

  it("is full when done, empty at the start, and nothing otherwise", () => {
    expect(progressOf(readResult(node("done")), NO_LIVE).fraction).toBe(1);
    expect(progressOf(readResult(node("queued")), NO_LIVE).fraction).toBe(0);
    for (const s of ["failed", "cancelled", "skipped", "interrupted"]) expect(progressOf(readResult(node(s)), NO_LIVE).fraction).toBeNull();
    expect(progressOf(null, NO_LIVE).fraction).toBeNull();
    expect(stepsOf("blockmesh")).toEqual(["blockMesh", "checkMesh"]);
    expect(stepsOf("nothing")).toEqual([]);
  });
});

describe("quality against the thresholds", () => {
  it("passes at or below a limit, warns above, and never passes a missing number", () => {
    expect(verdictOf(70, 70)).toBe("pass");
    expect(verdictOf(70.01, 70)).toBe("warn");
    expect(verdictOf(0, 0)).toBe("pass");
    expect(verdictOf(null, 70)).toBe("unknown");
  });

  it("gives four rows with the project's limits and words that carry the verdict", () => {
    const rows = qualityRows(readResult(node("done")));
    expect(rows.map((r) => r.key)).toEqual(["maxNonOrtho", "maxSkewness", "maxAspectRatio", "failedChecks"]);
    expect(rows[0]).toMatchObject({ valueText: "54.2 deg", limitText: "limit 70.0 deg", verdict: "pass", verdictText: "within limit" });
    expect(rows[1]).toMatchObject({ valueText: "1.30", limitText: "limit 4.00", verdict: "pass" });
    expect(rows[2]).toMatchObject({ limit: ASPECT_RATIO_LIMIT, verdict: "pass" });
    expect(rows[3]).toMatchObject({ valueText: "0", limitText: "none allowed", verdict: "pass" });
    expect(worstVerdict(rows)).toBe("pass");
  });

  it("warns for each number above its limit and for any failed check", () => {
    const bad = { ...QUALITY, max_non_ortho: 82.4, max_skewness: 4.6, max_aspect_ratio: 2500, failed_checks: 2, mesh_ok: false };
    const rows = qualityRows(readResult(node("done", { params: { quality: bad, ok: false } })));
    expect(rows.map((r) => r.verdict)).toEqual(["warn", "warn", "warn", "warn"]);
    expect(rows[0].verdictText).toBe("above limit");
    expect(worstVerdict(rows)).toBe("warn");
  });

  it("reads a missing number as not measured, and uses the defaults when an old result has no thresholds", () => {
    const partial = { max_non_ortho: 65, max_skewness: null, failed_checks: 0 };
    const rows = qualityRows(readResult(node("done", { params: { quality: partial } })));
    expect(rows[0]).toMatchObject({ verdict: "pass", limit: 70 });
    expect(rows[1]).toMatchObject({ verdict: "unknown", valueText: "not reported", verdictText: "not measured", limit: 4 });
    expect(rows[2].verdict).toBe("unknown");
    expect(worstVerdict(rows)).toBe("unknown");
    expect(worstVerdict([])).toBe("unknown");
  });

  it("has no rows for a result without a report", () => {
    expect(qualityRows(readResult(node("failed", { params: { quality: {} } })))).toEqual([]);
  });
});

describe("Generate and Cancel", () => {
  it("is enabled when everything it needs is there", () => {
    expect(generateState(input())).toEqual({ enabled: true, reason: "", label: "Generate", force: false });
  });

  it("says why it is disabled, one reason at a time and in order", () => {
    expect(generateState(input({ connected: false })).reason).toMatch(/Not connected/);
    expect(generateState(input({ hasProject: false })).reason).toBe("Open a project first.");
    expect(generateState(input({ phase: "running" })).reason).toBe("A mesh is already running.");
    expect(generateState(input({ busy: true })).reason).toMatch(/last request/);
    expect(generateState(input({ mesher: SNAPPY })).reason).toBe("snappyHexMesh is not available: snappyHexMesh arrives in T-150");
    expect(generateState(input({ hasPart: false })).reason).toMatch(/no part to mesh/);
    expect(generateState(input({ domain: "box", boxFilled: false })).reason).toMatch(/six numbers/);
    // **The reason is already a sentence**, so it is not prefixed (claude-win2's GUI review item 6:
    // the card read "No host can mesh: No OpenFOAM on this machine: add a Linux host…", two
    // prefixes and a doubled colon). A host that gives NO reason gets the short form.
    expect(generateState(input({ host: { name: "local", available: false, why: "the agent is not installed" } })).reason).toBe("the agent is not installed");
    expect(generateState(input({ host: { name: "local", available: false, why: "" } })).reason).toBe("No host can mesh: local is not available.");
  });

  it("needs a part for the part domain and a box for the box domain, and not the other way round", () => {
    expect(generateState(input({ domain: "box", hasPart: false, boxFilled: true })).enabled).toBe(true);
    expect(generateState(input({ domain: "part", hasPart: true, boxFilled: false })).enabled).toBe(true);
  });

  it("does not block while the host is unknown", () => {
    expect(generateState(input({ host: null })).enabled).toBe(true);
    expect(generateState(input({ mesher: null })).enabled).toBe(true);
  });

  it("is enabled again after any end state, and forces only a finished, current mesh", () => {
    for (const phase of ["failed", "cancelled", "interrupted", "skipped"] as MeshPhase[]) expect(generateState(input({ phase }))).toMatchObject({ enabled: true, label: "Generate", force: false });
    expect(generateState(input({ phase: "done" }))).toMatchObject({ enabled: true, label: "Generate again", force: true });
    expect(generateState(input({ phase: "done", stale: true }))).toMatchObject({ enabled: true, label: "Regenerate", force: false });
  });

  it("offers Cancel only while a job runs, and not twice", () => {
    expect(cancelState("none").visible).toBe(false);
    expect(cancelState("done").visible).toBe(false);
    expect(cancelState("queued")).toMatchObject({ visible: true, enabled: true });
    expect(cancelState("running")).toMatchObject({ visible: true, enabled: true });
    expect(cancelState("cancelling")).toMatchObject({ visible: true, enabled: false, reason: "Already stopping." });
  });

  it("polls status only while a job is in flight", () => {
    expect(MESH_PHASES.filter(shouldPoll)).toEqual(["queued", "running", "cancelling"]);
  });
});

describe("the events", () => {
  it("reads the service's six events and ignores others", () => {
    expect(readMeshEvent("mesh.progress", { node_id: "n_1", step: "blockMesh", stage: "running", fraction: 3 })).toEqual({ kind: "progress", nodeId: "n_1", step: "blockMesh", stage: "running", fraction: 1 });
    expect(readMeshEvent("mesh.log", { node_id: "n_1", step: "checkMesh", lines: ["a", 3, "b"] })).toEqual({ kind: "log", nodeId: "n_1", step: "checkMesh", lines: ["a", "b"] });
    expect(readMeshEvent("mesh.done", { node_id: "n_1", seconds: 2 })).toEqual({ kind: "done", nodeId: "n_1" });
    expect(readMeshEvent("mesh.cancelled", { node_id: "n_1" })).toEqual({ kind: "cancelled", nodeId: "n_1" });
    expect(readMeshEvent("mesh.failed", { node_id: "n_1", code: "mesh.error", message: "boom" })).toEqual({ kind: "failed", nodeId: "n_1", code: "mesh.error", message: "boom" });
    expect(readMeshEvent("mesh.skipped", { node_id: "n_1", code: "mesh.no_host", message: "no" })).toEqual({ kind: "skipped", nodeId: "n_1", code: "mesh.no_host", message: "no" });
    expect(readMeshEvent("mesh.progress", {})).toBeNull();
    expect(readMeshEvent("node.changed", { node_id: "n_1" })).toBeNull();
  });

  it("follows one run, and a new node starts a fresh one", () => {
    let live = applyMeshEvent(NO_LIVE, { kind: "progress", nodeId: "n_1", step: "blockMesh", stage: "running", fraction: 0 }, 10);
    live = applyMeshEvent(live, { kind: "log", nodeId: "n_1", step: "blockMesh", lines: ["a", "b"] }, 11);
    live = applyMeshEvent(live, { kind: "log", nodeId: "n_1", step: "blockMesh", lines: ["c"] }, 12);
    expect(live).toMatchObject({ nodeId: "n_1", step: "blockMesh", stage: "running", at: 12 });
    expect(logText(live)).toEqual(["--- blockMesh ---", "a", "b", "c"]);
    live = applyMeshEvent(live, { kind: "progress", nodeId: "n_1", step: "", stage: "cancelling", fraction: 0 }, 13);
    expect(live.step).toBe("blockMesh"); // an event without a step keeps the last one
    live = applyMeshEvent(live, { kind: "done", nodeId: "n_1" }, 14);
    expect(live.stage).toBe("done");
    live = applyMeshEvent(live, { kind: "progress", nodeId: "n_2", step: "blockMesh", stage: "running", fraction: 0 }, 20);
    expect(logText(live)).toEqual([]);
    expect(live.nodeId).toBe("n_2");
  });

  it("keeps the last 200 lines and the order the steps began in", () => {
    const many = Array.from({ length: 250 }, (_, i) => `line ${i}`);
    let live = applyMeshEvent(NO_LIVE, { kind: "log", nodeId: "n_1", step: "blockMesh", lines: many }, 1);
    expect(live.logs[0].lines).toHaveLength(LOG_CAP);
    expect(live.logs[0].lines[LOG_CAP - 1]).toBe("line 249");
    live = applyMeshEvent(live, { kind: "log", nodeId: "n_1", step: "checkMesh", lines: ["x"] }, 2);
    expect(live.logs.map((l) => l.step)).toEqual(["blockMesh", "checkMesh"]);
    const text = logText(live);
    expect(text).toHaveLength(LOG_CAP);
    expect(text[text.length - 1]).toBe("x");
  });

  it("takes the agent's tail over the events when it is at least as long, and keeps the events when it is shorter", () => {
    const live = applyMeshEvent(NO_LIVE, { kind: "log", nodeId: "n_1", step: "blockMesh", lines: ["a", "c"] }, 1);
    expect(mergeLogTail(live, "blockMesh", ["a", "b", "c"]).logs[0].lines).toEqual(["a", "b", "c"]);
    expect(mergeLogTail(live, "blockMesh", ["c"])).toBe(live);
    expect(mergeLogTail(live, "", ["z"])).toBe(live);
    expect(mergeLogTail(live, "checkMesh", [])).toBe(live);
    expect(mergeLogTail(live, "checkMesh", ["z"]).logs.map((l) => l.step)).toEqual(["blockMesh", "checkMesh"]);
  });

  it("is not fooled by a step named like an object key", () => {
    const live = applyMeshEvent(NO_LIVE, { kind: "log", nodeId: "n_1", step: "__proto__", lines: ["x"] }, 1);
    expect(logText(live)).toEqual(["--- __proto__ ---", "x"]);
    expect(({} as Record<string, unknown>).x).toBeUndefined();
  });
});

describe("the catalogue and the settings form", () => {
  it("reads mesh.meshers and drops what is not an entry", () => {
    const list = readMeshers({ meshers: [{ id: "blockmesh", label: "blockMesh", built: true, domains: ["part", "box"], why: "" }, { id: "snappy", label: "snappyHexMesh", built: false, domains: ["part"], why: "T-150" }, { label: "no id" }, 3], schema: {} });
    expect(list.map((m) => [m.id, m.built])).toEqual([["blockmesh", true], ["snappy", false]]);
    expect(list[1].why).toBe("T-150");
    expect(readMeshers(null)).toEqual([]);
    expect(readMeshers({ meshers: "x" })).toEqual([]);
  });

  it("shows the part for the part domain, the box for the box, and snappy's size only for snappy", () => {
    expect(visibleSettings("blockmesh", "part")).toContain("domain_part");
    expect(visibleSettings("blockmesh", "part")).not.toContain("box");
    expect(visibleSettings("blockmesh", "box")).toContain("box");
    expect(visibleSettings("blockmesh", "box")).not.toContain("domain_part");
    expect(visibleSettings("blockmesh", "part")).not.toContain("surface_size");
    expect(visibleSettings("snappy", "part")).toContain("surface_size");
  });

  it("reads the length unit off the display strings, and sends a bare box number in it", () => {
    expect(lengthUnitOf({ base_size: "5 mm", margin: "0 mm" })).toBe("mm");
    expect(lengthUnitOf({ margin: "2 in" })).toBe("in");
    expect(lengthUnitOf({})).toBe("");
    expect(lengthUnitOf(undefined)).toBe("");
    expect(boxEntry("12.5", "mm")).toBe("12.5 [mm]");
    expect(boxEntry("12.5", "")).toBe("12.5");
    expect(boxEntry("3 [cm]", "mm")).toBe("3 [cm]");
    expect(boxEntry("  ", "mm")).toBeNull();
  });

  it("writes the stored box in the project's unit, and in metres when the unit is not a length it knows", () => {
    expect(boxUnit({ base_size: "5 mm" })).toBe("mm");
    expect(boxUnit({ base_size: "5 furlong" })).toBe("m");
    expect(boxUnit(undefined)).toBe("m");
    expect(boxTexts([0, 0, 0, 0.01, 0.02, 0.0254], "mm")).toEqual(["0", "0", "0", "10", "20", "25.4"]);
    expect(boxTexts([0, 0, 0, 1, 1, 1], "m")).toEqual(["0", "0", "0", "1", "1", "1"]);
    expect(boxTexts([], "mm")).toEqual(["", "", "", "", "", ""]);
    expect(boxTexts([0, 0, 0, 0.0254, 0.0254, 0.0254], "in")[3]).toBe("1");
  });

  it("builds the box value only from six filled entries", () => {
    expect(boxValue(["0", "0", "0", "10", "10", "10"], "mm")).toEqual(["0 [mm]", "0 [mm]", "0 [mm]", "10 [mm]", "10 [mm]", "10 [mm]"]);
    expect(boxValue(["0", "0", "0", "1", "1", "1"], "")).toEqual([0, 0, 0, 1, 1, 1]);
    expect(boxValue(["0", "0", "0", "1", "1", ""], "mm")).toBeNull();
    expect(boxValue(["0", "1"], "mm")).toBeNull();
    expect(boxFilled([0, 0, 0, 1, 1, 1])).toBe(true);
    expect(boxFilled([])).toBe(false);
    expect(boxFilled([0, 0, 0, 1, 1])).toBe(false);
    expect(boxFilled(null)).toBe(false);
  });
});

describe("the boundary mapping", () => {
  it("lists each side's tag in side order, naming a tag that is gone by its id", () => {
    const view = readResult(node("done", { meta: { patch_tags: { zmax: "t_3", xmin: "t_1", ymin: "t_2", ymax: "" } } }));
    const names: Record<string, string> = { t_1: "inlet", t_3: "outlet" };
    expect(mappingRows(view, (id) => names[id] ?? null)).toEqual([
      { side: "xmin", tag: "inlet", known: true },
      { side: "ymin", tag: "t_2", known: false },
      { side: "zmax", tag: "outlet", known: true },
    ]);
    expect(mappingRows(null, () => null)).toEqual([]);
  });
});

describe("tree rows and formatting", () => {
  it("says the mesher for the settings and the cells or the state for a result", () => {
    expect(meshTreeValue({ id: "n_s", type: "mesh_settings", name: "settings", status: "ok", path: "/mesh/settings", children_count: 0, params: { mesher: "blockmesh" }, displays: { base_size: "5 mm" } })).toBe("blockmesh · 5 mm");
    expect(meshTreeValue({ id: "n_s", type: "mesh_settings", name: "settings", status: "ok", path: "/mesh/settings", children_count: 0, params: { mesher: "blockmesh" } })).toBe("blockmesh");
    expect(meshTreeValue(node("done"))).toBe("12,000 cells");
    expect(meshTreeValue(node("running"))).toBe("Meshing");
    expect(meshTreeValue(node("failed"))).toBe("Failed");
    expect(meshTreeValue(node("skipped"))).toBe("Skipped");
    expect(meshTreeValue({ id: "n_g", type: "group", name: "x", status: "ok", path: "/mesh/x", children_count: 0, params: {} })).toBe("");
  });

  it("formats counts and times", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(999)).toBe("999");
    expect(formatCount(1234567)).toBe("1,234,567");
    expect(formatSeconds(0)).toBe("");
    expect(formatSeconds(3.24)).toBe("3.2 s");
    expect(formatSeconds(42.4)).toBe("42 s");
    expect(formatSeconds(65)).toBe("1 min 5 s");
  });
});

// -------------------------------------------------------------------------------------------- the fidelity block

/** A measured mesh, as the server writes it: four axes and the thresholds they are judged against. */
const FIDELITY = {
  max_deviation_mm: 0.32,
  max_deviation_pct: 0.16,
  mean_deviation_mm: 0.041,
  mean_deviation_pct: 0.021,
  volume_error_pct: 0.14,
  feature_capture_pct: 100,
  characteristic_length_mm: 200,
  features: 12,
  sampled: 56170,
  points: 56170,
  verdict: "Good",
  thresholds: { max_deviation_pct: 1.5, mean_deviation_pct: 0.3, volume_error_pct: 1, feature_capture_pct: 90 },
};

describe("the fidelity block", () => {
  it("gives four rows, in mm and %, with limits derived from the characteristic length", () => {
    const view = readResult(node("done", { params: { quality: { ...QUALITY, fidelity: FIDELITY }, ok: true } }));
    const rows = fidelityRows(view);
    expect(rows.map((r) => r.key)).toEqual(["maxDeviation", "meanDeviation", "volumeError", "featureCapture"]);
    expect(rows.every((r) => r.group === "fidelity")).toBe(true);
    // **The deviation limit is a FRACTION of L, so the row shows it in mm** - 1.5 % of 200 mm.
    expect(rows[0]).toMatchObject({ verdict: "pass", valueText: "0.320 mm", limitText: "limit 3.000 mm" });
    expect(rows[1]).toMatchObject({ verdict: "pass" });
    expect(rows[2]).toMatchObject({ verdict: "pass", limitText: "limit 1.00 %" });
    // Capture reads the other way round, and its limit says so.
    expect(rows[3]).toMatchObject({ verdict: "pass", valueText: "100.00 %", limitText: "at least 90.00 %" });
  });

  it("warns on each axis that misses, independently", () => {
    const bad = { ...FIDELITY, max_deviation_mm: 5, mean_deviation_mm: 0.9, volume_error_pct: 1.4, feature_capture_pct: 40 };
    const rows = fidelityRows(readResult(node("done", { params: { quality: { ...QUALITY, fidelity: bad }, ok: false } })));
    expect(rows.map((r) => r.verdict)).toEqual(["warn", "warn", "warn", "warn"]);
    expect(rows[0].verdictText).toBe("above limit");
  });

  it("reads a missing number as not measured, never as a pass", () => {
    const partial = { ...FIDELITY, max_deviation_mm: null, volume_error_pct: null };
    const rows = fidelityRows(readResult(node("done", { params: { quality: { ...QUALITY, fidelity: partial }, ok: false } })));
    expect(rows[0]).toMatchObject({ verdict: "unknown", valueText: "not measured", verdictText: "not measured" });
    expect(rows[2]).toMatchObject({ verdict: "unknown" });
  });

  it("shows NOTHING when the mesh was never measured against its CAD", () => {
    // An older result has quality but no fidelity block, and an empty block is not four blank rows.
    expect(fidelityRows(readResult(node("done")))).toEqual([]);
    expect(fidelityRows(null)).toEqual([]);
    expect(fidelityVerdict(readResult(node("done")))).toBe("");
  });

  it("carries the verdict word, which is what a reader looks for", () => {
    const view = readResult(node("done", { params: { quality: { ...QUALITY, fidelity: { ...FIDELITY, verdict: "Acceptable" } } } }));
    expect(fidelityVerdict(view)).toBe("Acceptable");
  });

  it("treats a part with NO sharp edges as unmeasured rather than fully captured", () => {
    // `features: 0` means the CAD had nothing to capture; a capture of 100 % would be a false pass.
    const noFeatures = { ...FIDELITY, features: 0, feature_capture_pct: null };
    const rows = fidelityRows(readResult(node("done", { params: { quality: { ...QUALITY, fidelity: noFeatures } } })));
    expect(rows[3]).toMatchObject({ verdict: "unknown", valueText: "not measured" });
  });

  it("reads the block out of the record field by field", () => {
    const parsed = readFidelity({ fidelity: FIDELITY });
    expect(parsed).not.toBeNull();
    expect(parsed?.maxDeviationMm).toBe(0.32);
    expect(parsed?.features).toBe(12);
    expect(parsed?.limitCapturePct).toBe(90);
    expect(parsed?.verdict).toBe("Good");
    expect(readFidelity({})).toBeNull();
    expect(readFidelity(null)).toBeNull();
  });
});

describe("readMeshers and the additive family field", () => {
  it("reads output_structures when it is there", () => {
    const list = readMeshers({
      meshers: [
        { id: "blockmesh", label: "blockMesh", built: true, domains: ["part"], why: "", output_structures: ["structured-hexahedral"] },
        { id: "snappy", label: "snappyHexMesh", built: true, domains: ["part"], why: "", output_structures: ["hex-dominant"] },
      ],
    });
    expect(list[0]!.outputStructures).toEqual(["structured-hexahedral"]);
    expect(list[1]!.outputStructures).toEqual(["hex-dominant"]);
  });

  it("reads ABSENT as not-stated, not as a refusal", () => {
    /*
      **The backward-compatibility case** (Codex's 053537): the field lives on the mesh branch and is not
      merged, so today's trunk payload has no `output_structures` at all. An empty list means "this build
      does not say", and the outline must fall back to the flat list rather than showing no family.
    */
    const list = readMeshers({ meshers: [{ id: "blockmesh", label: "blockMesh", built: true, domains: [], why: "" }] });
    expect(list[0]!.outputStructures).toEqual([]);
  });

  it("drops a malformed value rather than guessing a family from it", () => {
    const list = readMeshers({ meshers: [{ id: "x", label: "x", built: true, domains: [], why: "", output_structures: "hex-dominant" }] });
    expect(list[0]!.outputStructures).toEqual([]);
  });

  it("never derives a family from the mesher's NAME", () => {
    // **The rule the whole seam exists for**: a tool that says nothing claims nothing.
    const list = readMeshers({ meshers: [{ id: "hexmesh", label: "Hex Pure", built: true, domains: [], why: "" }] });
    expect(list[0]!.outputStructures).toEqual([]);
  });
});

describe("the Mesh page's fineness chips and the base size", () => {
  const pipe: Box = { min: [0, 0, 0], max: [0.06, 0.2, 1] };

  it("each chip's size reads back as that chip (fineness to base size to fineness)", () => {
    for (const level of FINENESS) {
      const size = baseSizeFor(pipe, level);
      expect(size).not.toBeNull();
      expect(finenessOf(pipe, size)).toBe(level);
    }
  });

  it("the choices carry the length each one means, and the matching one is active", () => {
    const choices = finenessChoices(pipe, baseSizeFor(pipe, "medium"));
    expect(choices.map((c) => c.value)).toEqual(["coarse", "medium", "fine"]);
    expect(choices.map((c) => c.active)).toEqual([false, true, false]);
    expect(choices.map((c) => c.size)).toEqual([0.003, 0.0015, 0.00075]);
    expect(choices.map((c) => c.length)).toEqual(["3 mm", "1.5 mm", "0.75 mm"]);
  });

  it("a size typed in Advanced that matches no chip leaves all three unselected", () => {
    expect(finenessChoices(pipe, 0.0017).some((c) => c.active)).toBe(false);
  });

  it("without a box no choice has a size, so none can be set", () => {
    const choices = finenessChoices(null, 0.01);
    expect(choices.every((c) => c.size === null && c.length === "" && !c.active)).toBe(true);
  });

  it("the layers switch writes the card's layer count when on and none when off", () => {
    expect(layersFor(true)).toBe(SWITCH_LAYERS);
    expect(layersFor(false)).toBe(0);
  });
});
