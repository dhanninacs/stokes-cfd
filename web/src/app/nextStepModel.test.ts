/** The Next step button's order, labels and reasons (T-219 C). */
import { describe, expect, it } from "vitest";
import type { SetupCheck } from "../api/types";
import { nextStep, type NextInput } from "./nextStepModel";

const READY: SetupCheck = { state: "ready", next: "", counts: { regions: 1, materials: 1, boundaries: 3, uncovered_faces: 0, legacy: 0 }, findings: [] };
const INCOMPLETE: SetupCheck = { ...READY, state: "incomplete", next: "needs an outlet" };

function input(over: Partial<NextInput> = {}): NextInput {
  return {
    hasProject: true,
    hasParts: true,
    importing: false,
    setup: READY,
    meshPhase: "none",
    meshStale: false,
    generate: { enabled: true, reason: "" },
    runs: [],
    runButton: { enabled: false, reason: "Generate the mesh in the Mesh stage." },
    resultsShown: false,
    ...over,
  };
}

describe("the order of the steps", () => {
  it("starts at a project, then geometry, then boundaries", () => {
    expect(nextStep(input({ hasProject: false })).id).toBe("project");
    expect(nextStep(input({ hasProject: false })).enabled).toBe(false);
    expect(nextStep(input({ hasParts: false }))).toMatchObject({ id: "import", label: "Import geometry", enabled: true });
    expect(nextStep(input({ setup: INCOMPLETE }))).toMatchObject({ id: "define", label: "Define boundaries", enabled: true });
  });

  it("says what is left from setup.check, and asks for the check when it has not answered", () => {
    expect(nextStep(input({ setup: INCOMPLETE })).reason).toBe("needs an outlet");
    expect(nextStep(input({ setup: null }))).toMatchObject({ id: "define", enabled: true });
    expect(nextStep(input({ setup: { ...READY, state: "not_defined", next: "" } })).reason).toContain("material");
  });

  it("goes on to the mesh once the set-up is ready, then to the run, then to the results", () => {
    expect(nextStep(input())).toMatchObject({ id: "mesh", label: "Generate mesh", enabled: true });
    expect(nextStep(input({ meshPhase: "done", runButton: { enabled: true, reason: "" } }))).toMatchObject({ id: "run", label: "Run", enabled: true });
    expect(nextStep(input({ meshPhase: "done", runs: [{ state: "done", stale: false }] }))).toMatchObject({ id: "results", label: "Show results", enabled: true });
    expect(nextStep(input({ meshPhase: "done", runs: [{ state: "done", stale: false }], resultsShown: true }))).toMatchObject({ id: "done", enabled: false });
  });
});

describe("blocked steps say why", () => {
  it("is disabled with the Generate reason when the mesh cannot start", () => {
    const step = nextStep(input({ generate: { enabled: false, reason: "No host can mesh: OpenFOAM is not installed." } }));
    expect(step).toMatchObject({ id: "mesh", enabled: false });
    expect(step.reason).toBe("No host can mesh: OpenFOAM is not installed.");
  });

  it("is disabled with the Run reason when solve.check is not ready", () => {
    const step = nextStep(input({ meshPhase: "done", runButton: { enabled: false, reason: "Give the region a material in the Physics stage." } }));
    expect(step).toMatchObject({ id: "run", enabled: false });
    expect(step.reason).toContain("material");
  });

  it("waits while a file is read, a mesh is made and a run goes", () => {
    expect(nextStep(input({ importing: true, hasParts: false }))).toMatchObject({ id: "import", enabled: false });
    expect(nextStep(input({ meshPhase: "running" }))).toMatchObject({ id: "mesh", label: "Meshing…", enabled: false });
    expect(nextStep(input({ meshPhase: "done", runs: [{ state: "running", stale: false }] }))).toMatchObject({ id: "run", label: "Running…", enabled: false });
  });
});

describe("going round again", () => {
  it("a stale mesh comes before a finished run", () => {
    const step = nextStep(input({ meshPhase: "done", meshStale: true, runs: [{ state: "done", stale: false }] }));
    expect(step).toMatchObject({ id: "mesh", label: "Generate mesh", enabled: true });
    expect(step.reason).toContain("out of date");
  });

  it("a stale run is run again, and an unconverged one still counts as finished", () => {
    expect(nextStep(input({ meshPhase: "done", runs: [{ state: "done", stale: true }], runButton: { enabled: true, reason: "" } })).id).toBe("run");
    expect(nextStep(input({ meshPhase: "done", runs: [{ state: "done_unconverged", stale: false }] })).id).toBe("results");
  });

  it("a failed or cancelled run is run again", () => {
    expect(nextStep(input({ meshPhase: "done", runs: [{ state: "failed", stale: false }], runButton: { enabled: true, reason: "" } })).id).toBe("run");
    expect(nextStep(input({ meshPhase: "done", runs: [{ state: "cancelled", stale: false }], runButton: { enabled: true, reason: "" } })).id).toBe("run");
  });
});
