import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { readRunEvent, readRunList, readRunRecord, readSolveCheck, type RunState, type RunSummary, type SolveCheck } from "./runApi";
import { JOB_LOG_CAP, NO_RUN, appendJobLog, applyRunEvent, canResume, findSolverSettings, formatResidual, iterationLimit, markCancelling, markStopped, mergeRecord, noteStart, noticeToast, progressFraction, reduceRunEvent, residualTarget, runButton, runReadout, seedJobLog, setRuns, stateLine, type RunLive, turbulenceOptionsFor } from "./runPanelModel";

function run(over: Partial<RunSummary> = {}): RunSummary {
  return { id: "r_1", name: "duct", state: "running", stale: false, iterations: 0, converged: false, seconds: 0, step: "", fraction: null, error: "", host: "local", last: [], monitors: [], ...over };
}

const READY: SolveCheck = { state: "ready", next: "run.start", findings: [] };
const NO_MESH: SolveCheck = {
  state: "not_ready",
  next: "Generate the mesh in the Mesh stage",
  findings: [{ code: "solve.no_mesh", severity: "error", message: "There is no mesh.", suggestion: "Generate the mesh in the Mesh stage", nodes: [] }],
};

function feed(live: RunLive, event: string, data: Record<string, unknown>): RunLive {
  const e = readRunEvent(event, data);
  if (e === null) throw new Error(`not a run event: ${event}`);
  return reduceRunEvent(live, e);
}

describe("readRunEvent", () => {
  it("reads a two-block run.residual event", () => {
    const e = readRunEvent("run.residual", {
      run_id: "r_1",
      blocks: [
        { iteration: 1, time: 1, residuals: { Ux: 1, p: 0.5 }, continuity: {} },
        { iteration: 2, time: 2, residuals: { Ux: 0.5, p: null }, continuity: {} },
      ],
    });
    expect(e?.kind).toBe("residual");
    if (e?.kind !== "residual") return;
    expect(e.blocks.map((b) => b.iteration)).toEqual([1, 2]);
    expect(e.blocks[1].values).toEqual([
      { name: "Ux", value: 0.5 },
      { name: "p", value: null },
    ]);
  });

  it("reads the one-block shape of the contract file too", () => {
    const e = readRunEvent("run.residual", { run_id: "r_1", iteration: 7, time: 7, residuals: { Ux: 0.1 }, continuity: {} });
    expect(e?.kind === "residual" && e.blocks.length).toBe(1);
  });

  it("ignores other events, an event with no run, a finish with an unknown state, and a block with no iteration", () => {
    expect(readRunEvent("node.changed", { run_id: "r_1" })).toBeNull();
    expect(readRunEvent("run.started", {})).toBeNull();
    expect(readRunEvent("run.finished", { run_id: "r_1", state: "exploded" })).toBeNull();
    const e = readRunEvent("run.residual", { run_id: "r_1", blocks: [{ residuals: { Ux: 1 } }] });
    expect(e?.kind === "residual" && e.blocks.length).toBe(0);
  });

  it("sends a mesh log line to the job log and a project change to a reset", () => {
    expect(readRunEvent("mesh.log", { node_id: "n_1", step: "blockMesh", lines: ["a"] })).toEqual({ kind: "log", source: "mesh", jobId: "n_1", step: "blockMesh", lines: ["a"] });
    expect(readRunEvent("project.opened", {})).toEqual({ kind: "reset" });
  });
});

describe("applyRunEvent through a whole run", () => {
  it("goes started, residual, residual, finished", () => {
    let live = NO_RUN;
    live = feed(live, "run.started", { run_id: "r_1" });
    expect(live.runs[0].state).toBe("running");
    expect(live.activeRun).toBe("r_1");
    live = feed(live, "run.progress", { run_id: "r_1", step: "foamRun", stage: "running", fraction: 0.25, message: "" });
    expect(live.runs[0].step).toBe("foamRun");
    expect(live.runs[0].fraction).toBe(0.25);
    live = feed(live, "run.residual", {
      run_id: "r_1",
      blocks: [
        { iteration: 1, time: 1, residuals: { Ux: 1, p: 0.5 }, continuity: {} },
        { iteration: 2, time: 2, residuals: { Ux: 0.5, p: 0.25 }, continuity: {} },
      ],
    });
    expect(live.runs[0].iterations).toBe(2);
    expect(live.runs[0].last).toEqual([
      { name: "Ux", value: 0.5 },
      { name: "p", value: 0.25 },
    ]);
    expect(live.residuals?.iterations).toEqual([1, 2]);
    live = feed(live, "run.finished", { run_id: "r_1", state: "done", seconds: 2.5, converged: true });
    expect(live.runs[0]).toMatchObject({ state: "done", seconds: 2.5, converged: true, iterations: 2 });
    expect(live.notice).toMatchObject({ seq: 1, runId: "r_1", state: "done", iterations: 2 });
  });

  it("is immutable and returns the input for an event that changes nothing", () => {
    const runs = [run({ iterations: 5 })];
    const before = JSON.stringify(runs);
    const next = applyRunEvent(runs, { kind: "started", runId: "r_1" });
    expect(next).toBe(runs);
    applyRunEvent(runs, { kind: "finished", runId: "r_1", state: "failed", seconds: 1, converged: false });
    expect(JSON.stringify(runs)).toBe(before);
    expect(applyRunEvent(runs, { kind: "reset" })).toBe(runs);
    expect(applyRunEvent(runs, { kind: "log", source: "run", jobId: "r_1", step: "", lines: ["x"] })).toBe(runs);
  });

  it("puts a new run first and starts it with an empty chart", () => {
    let live = feed(NO_RUN, "run.started", { run_id: "r_1" });
    live = feed(live, "run.residual", { run_id: "r_1", blocks: [{ iteration: 1, time: 1, residuals: { p: 1 }, continuity: {} }] });
    live = feed(live, "run.finished", { run_id: "r_1", state: "cancelled", seconds: 1, converged: false });
    live = feed(live, "run.started", { run_id: "r_2" });
    expect(live.runs.map((r) => r.id)).toEqual(["r_2", "r_1"]);
    expect(live.activeRun).toBe("r_2");
    expect(live.residuals).toBeNull();
    expect(live.residualRun).toBe("r_2");
  });

  it("a late residual of an old run does not enter the new run's chart", () => {
    let live = feed(NO_RUN, "run.started", { run_id: "r_1" });
    live = feed(live, "run.started", { run_id: "r_2" });
    live = feed(live, "run.residual", { run_id: "r_1", blocks: [{ iteration: 9, time: 9, residuals: { p: 1 }, continuity: {} }] });
    expect(live.residuals).toBeNull();
  });

  it("run.changed marks a known run stale and does not invent one", () => {
    let live = feed(NO_RUN, "run.started", { run_id: "r_1" });
    live = feed(live, "run.finished", { run_id: "r_1", state: "done", seconds: 1, converged: true });
    live = feed(live, "run.changed", { run_id: "r_1", stale: true });
    expect(live.runs[0].stale).toBe(true);
    expect(feed(NO_RUN, "run.changed", { run_id: "r_9", stale: true }).runs).toEqual([]);
  });

  it("run.skipped records the reason", () => {
    const live = feed(NO_RUN, "run.skipped", { run_id: "r_1", code: "run.no_host", message: "no OpenFOAM on this host" });
    expect(live.runs[0]).toMatchObject({ state: "skipped", error: "no OpenFOAM on this host" });
    expect(live.notice?.state).toBe("skipped");
  });

  it("a project change resets the run state but keeps the Log tab's view", () => {
    const live = feed({ ...NO_RUN, logView: "job" }, "run.started", { run_id: "r_1" });
    const reset = feed(live, "project.opened", {});
    expect(reset.runs).toEqual([]);
    expect(reset.logView).toBe("job");
  });
});

describe("the job log", () => {
  it("run.log and mesh.log lines land in the job log with a heading when the step changes", () => {
    let live = feed(NO_RUN, "mesh.log", { node_id: "n_1", step: "blockMesh", lines: ["Creating cells"] });
    live = feed(live, "mesh.log", { node_id: "n_1", step: "blockMesh", lines: ["End"] });
    live = feed(live, "run.log", { run_id: "r_1", step: "foamRun", lines: ["Time = 1", "Time = 2"] });
    expect(live.jobLog).toEqual(["--- mesh · blockMesh ---", "Creating cells", "End", "--- run · foamRun ---", "Time = 1", "Time = 2"]);
  });

  it("keeps the last 5,000 lines", () => {
    let live = NO_RUN;
    for (let i = 0; i < 12; i++) live = feed(live, "run.log", { run_id: "r_1", step: "foamRun", lines: Array.from({ length: 500 }, (_, j) => `l${i * 500 + j}`) });
    expect(live.jobLog.length).toBe(JOB_LOG_CAP);
    expect(live.jobLog[live.jobLog.length - 1]).toBe("l5999");
  });

  it("appendJobLog with no lines changes nothing", () => {
    const log = ["a"];
    expect(appendJobLog(log, "k", "run", "r_1", "s", []).log).toBe(log);
  });

  it("a tail fills an empty log and leaves one the events are feeding", () => {
    const seeded = seedJobLog(NO_RUN, "r_1", "foamRun", ["Time = 1"]);
    expect(seeded.jobLog).toEqual(["--- run · foamRun ---", "Time = 1"]);
    expect(seedJobLog(seeded, "r_1", "foamRun", ["x"])).toBe(seeded);
  });
});

describe("runButton", () => {
  it("is Stop while a run is in flight", () => {
    for (const state of ["running", "queued"] as RunState[]) {
      expect(runButton(run({ state }), READY)).toMatchObject({ label: "Stop", enabled: true });
      expect(runButton(run({ state }), NO_MESH)).toMatchObject({ label: "Stop", enabled: true });
    }
  });

  it("is a disabled Stop with its reason once the stop was asked", () => {
    expect(runButton(run({ state: "cancelling" }), READY)).toMatchObject({ label: "Stop", enabled: false, reason: "Already stopping." });
  });

  it("is a disabled Run with the server's suggestion when the case is not ready", () => {
    expect(runButton(null, NO_MESH)).toEqual({ label: "Run", enabled: false, reason: "Generate the mesh in the Mesh stage", force: false });
  });

  it("falls back to the first error's words when `next` says nothing", () => {
    const check: SolveCheck = { ...NO_MESH, next: "" };
    expect(runButton(null, check).reason).toBe("Generate the mesh in the Mesh stage");
    expect(runButton(null, { state: "not_ready", next: "", findings: [] }).reason).toBe("The case is not ready to run.");
  });

  it("is an enabled Run when the case is ready, and says it before the check has answered", () => {
    expect(runButton(null, READY)).toEqual({ label: "Run", enabled: true, reason: "", force: false });
    expect(runButton(null, null)).toMatchObject({ enabled: false, reason: "Checking whether the case is ready to run." });
    expect(runButton(null, READY, true)).toMatchObject({ enabled: false });
  });

  it("forces a run again only when the last run is done and current", () => {
    expect(runButton(run({ state: "done" }), READY).force).toBe(true);
    expect(runButton(run({ state: "done", stale: true }), READY).force).toBe(false);
    expect(runButton(run({ state: "failed" }), READY).force).toBe(false);
  });
});

describe("runReadout", () => {
  it("is null until something is known", () => {
    expect(runReadout(null, null)).toBeNull();
  });

  it("says ready or what is missing when there is no run", () => {
    expect(runReadout(null, READY)).toMatchObject({ text: "Run · ready", state: "ready" });
    expect(runReadout(null, NO_MESH)).toMatchObject({ text: "Run · Generate the mesh in the Mesh stage", state: "idle" });
  });

  it("names the iteration and the pressure residual while running", () => {
    const r = run({ iterations: 120, last: [{ name: "Ux", value: 1e-3 }, { name: "p", value: 8e-5 }] });
    expect(runReadout(r, READY)).toMatchObject({ text: "Run · it 120 · p 8e-5", state: "running" });
    expect(runReadout(r, READY, [{ name: "p", value: 1.2e-5 }])?.text).toBe("Run · it 120 · p 1.2e-5");
    expect(runReadout(run({ iterations: 3 }), READY)?.text).toBe("Run · it 3");
  });

  it("reads each end", () => {
    expect(runReadout(run({ state: "done", iterations: 240, converged: true }), READY)).toMatchObject({ text: "Run · it 240 · converged", state: "done" });
    expect(runReadout(run({ state: "done_unconverged", iterations: 1000 }), READY)).toMatchObject({ text: "Run · it 1000 · not converged", state: "unconverged" });
    expect(runReadout(run({ state: "failed", error: "boom" }), READY)).toMatchObject({ text: "Run · failed", state: "failed", detail: "boom" });
    expect(runReadout(run({ state: "cancelled", iterations: 40 }), READY)).toMatchObject({ text: "Run · cancelled", state: "cancelled" });
    expect(runReadout(run({ state: "done", stale: true }), READY)).toMatchObject({ text: "Run · stale", state: "stale" });
    expect(runReadout(run({ state: "interrupted" }), READY)).toMatchObject({ text: "Run · interrupted", state: "interrupted" });
    expect(runReadout(run({ state: "skipped", error: "run.no_host: OpenFOAM is not installed" }), READY)?.text).toBe("Run · skipped: OpenFOAM is not installed");
    expect(runReadout(run({ state: "cancelling" }), READY)?.text).toBe("Run · stopping");
  });
});

describe("stateLine", () => {
  it("never shows a spinner for an interrupted run and shows an unconverged run as a warning, not an error", () => {
    expect(stateLine(run({ state: "interrupted" }), 500)).toMatchObject({ tone: "warn" });
    expect(stateLine(run({ state: "done_unconverged", iterations: 1000, seconds: 12 }), 1000)).toEqual({ text: "Finished, not converged: 1000 iterations in 12 s", tone: "warn" });
    expect(stateLine(run({ state: "done", iterations: 52, seconds: 2.5 }), 500)).toEqual({ text: "Finished: converged in 52 iterations in 2.5 s", tone: "ok" });
    expect(stateLine(run({ iterations: 120, step: "foamRun" }), 500).text).toBe("Running: iteration 120 of 500 (foamRun)");
    expect(stateLine(null, null)).toEqual({ text: "No run yet", tone: "neutral" });
  });
});

describe("progressFraction", () => {
  it("uses the solver's fraction, else iterations over the limit", () => {
    expect(progressFraction(run({ fraction: 0.4 }), 500)).toBe(0.4);
    expect(progressFraction(run({ iterations: 100 }), 500)).toBe(0.2);
    expect(progressFraction(run({ iterations: 0 }), 500)).toBeNull();
    expect(progressFraction(run({ state: "done" }), 500)).toBe(1);
    expect(progressFraction(run({ state: "failed" }), 500)).toBeNull();
  });
});

describe("noticeToast", () => {
  it("words the end of a run", () => {
    const base = { seq: 1, runId: "r_1", iterations: 240, seconds: 12, converged: true, message: "" };
    expect(noticeToast({ ...base, state: "done" })).toEqual({ kind: "ok", text: "Run converged in 240 iterations · 12 s" });
    expect(noticeToast({ ...base, state: "done_unconverged", iterations: 1000 })).toEqual({ kind: "info", text: "Run stopped at 1000 iterations without converging" });
    expect(noticeToast({ ...base, state: "failed", message: "solver crashed" })?.kind).toBe("error");
    expect(noticeToast({ ...base, state: "cancelled" })).toBeNull();
  });
});

describe("records from the server", () => {
  it("reads run.list and run.status", () => {
    const list = readRunList({ runs: [{ id: "r_2", name: "duct", state: "done_unconverged", stale: false, iterations: 1000, converged: false, timings: { seconds: 12 }, last_residuals: { p: 1e-3 } }, { nope: 1 }] });
    expect(list.length).toBe(1);
    expect(list[0]).toMatchObject({ id: "r_2", state: "done_unconverged", seconds: 12, last: [{ name: "p", value: 1e-3 }] });
    expect(readRunRecord({ id: "r_3", state: "bogus", step: "foamRun", fraction: 2 })).toMatchObject({ state: "queued", step: "foamRun", fraction: 1 });
    expect(readRunRecord("x")).toBeNull();
  });

  it("reads solve.check", () => {
    expect(readSolveCheck({ state: "not_ready", next: "n", findings: [{ code: "solve.no_mesh", severity: "warning", message: "m", suggestion: "s", nodes: ["a"] }] })?.findings[0]).toMatchObject({ severity: "warning", suggestion: "s" });
    expect(readSolveCheck({ state: "maybe" })).toBeNull();
  });
});

describe("the answers the control keeps", () => {
  it("run.list replaces the records, keeps the chart of the same run and drops another's", () => {
    let live = feed(NO_RUN, "run.residual", { run_id: "r_1", blocks: [{ iteration: 1, time: 1, residuals: { p: 1 }, continuity: {} }] });
    expect(setRuns(live, [run({ id: "r_1" })]).residuals).toBe(live.residuals);
    expect(setRuns(live, [run({ id: "r_2" }), run({ id: "r_1" })]).residuals).toBeNull();
    live = setRuns(live, []);
    expect(live.activeRun).toBeNull();
  });

  it("run.status that finds a run ended raises the notice the lost event would have", () => {
    const live = setRuns(NO_RUN, [run({ state: "running", iterations: 10 })]);
    const merged = mergeRecord(live, run({ state: "done", iterations: 52, converged: true, seconds: 2.5 }));
    expect(merged.runs[0].state).toBe("done");
    expect(merged.notice).toMatchObject({ runId: "r_1", state: "done", iterations: 52 });
    expect(mergeRecord(merged, run({ state: "done", iterations: 52 })).notice).toBe(merged.notice);
  });

  it("run.start shows a new run at once, and a reused answer changes nothing", () => {
    const live = noteStart(NO_RUN, { run: "r_1", state: "queued", reused: false, name: "duct" });
    expect(live.runs[0]).toMatchObject({ id: "r_1", state: "queued", name: "duct" });
    expect(noteStart(live, { run: "r_9", state: "done", reused: true, name: "duct" })).toBe(live);
  });

  it("Stop makes the run say it is stopping", () => {
    const live = markCancelling(setRuns(NO_RUN, [run()]), "r_1");
    expect(live.runs[0].state).toBe("cancelling");
  });
});

describe("the settings node", () => {
  const node = (path: string, type: string, params: Record<string, unknown> = {}, children: NodeSummary[] = []): NodeSummary => ({ id: path, type, name: path, status: "ok", params: params as NodeSummary["params"], path, children_count: children.length, children });
  const tree = node("/", "root", {}, [node("/solver", "group", {}, [node("/solver/settings", "solver_settings", { iterations: 500, residual_target: 1e-4 })])]);

  it("finds the node and reads the target and the limit", () => {
    const settings = findSolverSettings(tree);
    expect(settings?.path).toBe("/solver/settings");
    expect(residualTarget(settings)).toBe(1e-4);
    expect(iterationLimit(settings)).toBe(500);
    expect(findSolverSettings(null)).toBeNull();
    expect(residualTarget(null)).toBeNull();
  });
});

describe("formatResidual", () => {
  it("prints a short exponent form", () => {
    expect(formatResidual(8e-5)).toBe("8e-5");
    expect(formatResidual(1.23e-3)).toBe("1.2e-3");
    expect(formatResidual(0)).toBe("-");
    expect(formatResidual(Number.NaN)).toBe("-");
  });
});

describe("turbulenceOptionsFor - the engine decides (live-review item 2)", () => {
  const engines = [
    { id: "of14.incompressibleFluid", turbulence: ["laminar"] },
    { id: "of14.kOmegaSST", turbulence: ["laminar", "kOmegaSST"] },
  ];
  const offered = ["laminar", "kOmegaSST"];

  it("offers a laminar engine ONLY laminar, though the schema lists both", () => {
    // **The inconsistency the review saw**: `solve.py` refuses `turbulence == "kOmegaSST"` with
    // `solve.needs_turbulence`, so offering it under a laminar engine is a choice that cannot run.
    expect(turbulenceOptionsFor(engines, "of14.incompressibleFluid", offered)).toEqual(["laminar"]);
  });

  it("offers the kOmegaSST engine both", () => {
    expect(turbulenceOptionsFor(engines, "of14.kOmegaSST", offered)).toEqual(["laminar", "kOmegaSST"]);
  });

  it("keeps the schema's OWN order, so the list does not jump as the engine changes", () => {
    expect(turbulenceOptionsFor([{ id: "e", turbulence: ["kOmegaSST", "laminar"] }], "e", offered)).toEqual(offered);
  });

  it("keeps the schema's list for an engine it does not know, rather than hiding options on a guess", () => {
    expect(turbulenceOptionsFor(engines, "of99.mystery", offered)).toEqual(offered);
    expect(turbulenceOptionsFor(engines, "", offered)).toEqual(offered);
  });
});

describe("Stop that saves, and Resume", () => {
  it("reads stopped and resumed_from off a record, and an old record as neither", () => {
    expect(readRunRecord({ id: "r_1", state: "done_unconverged", stopped: true, resumed_from: "r_0" })).toMatchObject({ stopped: true, resumedFrom: "r_0" });
    expect(readRunRecord({ id: "r_1", state: "done" })).toMatchObject({ stopped: false, resumedFrom: "" });
  });

  it("holds Stop disabled while a stopped run is saving its iteration", () => {
    const live = markStopped({ ...NO_RUN, runs: [run({ state: "running", iterations: 120 })] }, "r_1");
    expect(live.runs[0].stopped).toBe(true);
    expect(runButton(live.runs[0], null)).toMatchObject({ label: "Stop", enabled: false, reason: "Already stopping." });
    expect(stateLine(live.runs[0], 500).text).toBe("Stopping at iteration 120: saving it");
  });

  it("says a stopped run was saved and can be resumed", () => {
    const stopped = run({ state: "done_unconverged", iterations: 120, stopped: true });
    expect(stateLine(stopped, 500).text).toContain("Stopped and saved at iteration 120");
    expect(runReadout(stopped, null)?.text).toBe("Run · it 120 · stopped");
  });

  it("offers Resume for a stopped run or one short of the limit, never for a converged or finished-at-limit one", () => {
    expect(canResume(run({ state: "done_unconverged", iterations: 120, stopped: true }), 500)).toBe(true);
    expect(canResume(run({ state: "done_unconverged", iterations: 300 }), 500)).toBe(true);
    expect(canResume(run({ state: "done_unconverged", iterations: 500 }), 500)).toBe(false);
    expect(canResume(run({ state: "done", iterations: 120 }), 500)).toBe(false);
    expect(canResume(run({ state: "running" }), 500)).toBe(false);
    expect(canResume(null, 500)).toBe(false);
  });
});
