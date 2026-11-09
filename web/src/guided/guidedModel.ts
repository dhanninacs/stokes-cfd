/**
 * The flow strip's model: where the case stands, as six steps (Geometry, Fluid, Boundaries, Mesh, Run,
 * Results), each with a state dot and a one-line hint, and the step that comes next.
 *
 * One pure function over what the stores already know (the tree, the last `setup.check`, the mesh
 * result, the run records, the results), so every state is tested in Node and the strip and the cards
 * only draw. It follows the server's own checks and does not second-guess them: Boundaries is done
 * when `setup.check` says `ready`, Mesh when the mesh result is `done` and current, Run when the newest
 * run finished `done` or `done_unconverged` and is not out of date.
 *
 * The four states: `todo` is a step not started (or not reachable yet, and the hint says what is
 * missing first), `attention` is a step that is started and wrong or out of date (a body with no
 * material, a stale mesh, a failed run), `running` is a job in flight and `done` is finished.
 *
 * Import rule: no React, no store, no DOM.
 */
import type { NodeSummary, SetupCheck } from "../api/types";
import { formatCount, type MeshPhase } from "../panels/meshPanelModel";
import { isRunActive, type RunSummary } from "../panels/runApi";
import { stateLine, type Tone } from "../panels/runPanelModel";
import { fluidBody, readBodies, readBoundaries, readCase, readFluidRegion, type Body, type FoamCase } from "./guidedTree";

export type GuidedStepId = "geometry" | "fluid" | "boundaries" | "mesh" | "run" | "results";
export const GUIDED_STEPS: readonly GuidedStepId[] = ["geometry", "fluid", "boundaries", "mesh", "run", "results"];

export type StepState = "todo" | "attention" | "done" | "running";

export interface GuidedStep {
  id: GuidedStepId;
  label: string;
  state: StepState;
  /** One line: what the step needs, or what it has. */
  hint: string;
}

export interface GuidedView {
  steps: GuidedStep[];
  /** The first step that is not done, or the last one when every step is. */
  active: GuidedStepId;
}

export interface GuidedInput {
  tree: NodeSummary | null;
  hasProject: boolean;
  /** A file is going up or being read. */
  importing: boolean;
  /** The last `setup.check`, or null before it has answered. */
  setup: SetupCheck | null;
  meshPhase: MeshPhase;
  /** The mesh result says the geometry, tags or settings changed since it was made. */
  meshStale: boolean;
  meshCells: number;
  /** The run records, newest first. */
  runs: readonly (Pick<RunSummary, "state" | "stale" | "iterations" | "converged"> & { kind?: string })[];
  /** The newest finished run's fields are read. */
  resultsLoaded: boolean;
  /** A field colours the model. */
  fieldChosen: boolean;
}

const LABELS: Record<GuidedStepId, string> = {
  geometry: "Geometry",
  fluid: "Fluid",
  boundaries: "Boundaries",
  mesh: "Mesh",
  run: "Run",
  results: "Results",
};

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function geometry(i: GuidedInput, bodies: readonly Body[]): Omit<GuidedStep, "id" | "label"> {
  if (!i.hasProject) return { state: "todo", hint: "Open or create a project" };
  if (i.importing) return { state: "running", hint: "Reading the file" };
  if (bodies.length === 0) return { state: "todo", hint: "Import a CAD file" };
  const fluid = fluidBody(bodies);
  if (fluid === null) return { state: "attention", hint: "Choose which body is the fluid" };
  return { state: "done", hint: `${plural(bodies.length, "body", "bodies")} · ${fluid.name} is the fluid` };
}

function fluid(geometryDone: boolean, bodies: readonly Body[], tree: NodeSummary | null): Omit<GuidedStep, "id" | "label"> {
  if (!geometryDone) return { state: "todo", hint: "Choose the fluid body first" };
  const body = fluidBody(bodies);
  const region = readFluidRegion(tree);
  if (region === null || region.materialName === null) return { state: "attention", hint: `Pick the fluid in ${body?.name ?? "the fluid body"}` };
  return { state: "done", hint: region.materialName };
}

function boundaries(fluidDone: boolean, i: GuidedInput): Omit<GuidedStep, "id" | "label"> {
  if (!fluidDone) return { state: "todo", hint: "Choose the fluid first" };
  if (i.setup === null) return { state: "todo", hint: "Click faces of the fluid to say what they are" };
  const count = readBoundaries(i.tree).length;
  if (i.setup.state === "ready") {
    const loose = i.setup.counts.uncovered_faces;
    return { state: "done", hint: loose > 0 ? `${plural(count, "boundary", "boundaries")} · ${plural(loose, "face")} left as wall` : plural(count, "boundary", "boundaries") };
  }
  const said = i.setup.next.trim();
  const hint = said !== "" ? said.charAt(0).toUpperCase() + said.slice(1) : "Add an inlet and an outlet";
  return { state: count > 0 ? "attention" : "todo", hint };
}

function mesh(boundariesDone: boolean, i: GuidedInput): Omit<GuidedStep, "id" | "label"> {
  if (i.meshPhase === "queued" || i.meshPhase === "running" || i.meshPhase === "cancelling") return { state: "running", hint: "Building the mesh" };
  if (i.meshPhase === "done") {
    if (i.meshStale) return { state: "attention", hint: "Out of date: build it again" };
    return { state: "done", hint: `${formatCount(i.meshCells)} cells` };
  }
  if (i.meshPhase === "failed" || i.meshPhase === "interrupted" || i.meshPhase === "skipped") return { state: "attention", hint: "The mesh was not built: see the card" };
  if (!boundariesDone) return { state: "todo", hint: "Set the boundaries first" };
  return { state: "todo", hint: "Choose a size and generate" };
}

function run(meshDone: boolean, i: GuidedInput): Omit<GuidedStep, "id" | "label"> {
  const latest = i.runs[0];
  if (latest !== undefined && isRunActive(latest.state)) return { state: "running", hint: latest.iterations > 0 ? `Iteration ${latest.iterations}` : "Starting" };
  if (latest !== undefined && (latest.state === "done" || latest.state === "done_unconverged")) {
    if (latest.stale) return { state: "attention", hint: "Out of date: run again" };
    // "Converged" only when the solver said so; a case that ran to its End finished, and a run
    // that hit its iteration cap stopped (`done_unconverged`).
    const word = latest.state !== "done" ? "Stopped at" : latest.converged ? "Converged in" : "Finished in";
    return { state: "done", hint: `${word} ${plural(latest.iterations, "iteration")}` };
  }
  if (latest !== undefined && (latest.state === "failed" || latest.state === "interrupted" || latest.state === "skipped")) return { state: "attention", hint: "The run did not finish: see the card" };
  if (!meshDone) return { state: "todo", hint: "Build the mesh first" };
  return { state: "todo", hint: "Press Run" };
}

function results(runDone: boolean, i: GuidedInput): Omit<GuidedStep, "id" | "label"> {
  if (!runDone) return { state: "todo", hint: "Run the case first" };
  if (!i.resultsLoaded) return { state: "todo", hint: "Show the fields of the run" };
  if (!i.fieldChosen) return { state: "attention", hint: "Choose a field to colour the model" };
  return { state: "done", hint: "Showing the fields" };
}

/**
 * The first four steps of an imported OpenFOAM case. The case IS the geometry, the fluid and the
 * boundaries, so those are done the moment it is imported and say where they came from. **The
 * mesh is a step of its own**: a case has no CAD, so after opening one the user lands on Mesh and
 * presses Generate, which runs the case's own mesh steps (`run.start case mesh_only`) and shows the
 * mesh before anything is solved. A full run also builds it, so a finished run counts as meshed.
 */
function caseSteps(found: FoamCase, i: GuidedInput): [GuidedStepId, Omit<GuidedStep, "id" | "label">][] {
  const from = { state: "done" as const, hint: "From the case" };
  const finished = (r: { state: string }) => r.state === "done" || r.state === "done_unconverged";
  const sim = i.runs.find((r) => r.kind !== "mesh");
  const meshRun = i.runs.find((r) => r.kind === "mesh");
  const what = found.solver !== "" ? `${found.name} · ${found.solver}` : found.name;
  let meshStep: Omit<GuidedStep, "id" | "label">;
  if (meshRun !== undefined && isRunActive(meshRun.state)) meshStep = { state: "running", hint: "Building the mesh" };
  else if ((meshRun !== undefined && finished(meshRun)) || (sim !== undefined && (finished(sim) || isRunActive(sim.state)))) meshStep = { state: "done", hint: "Built from the case's own mesh steps" };
  else if (meshRun !== undefined && (meshRun.state === "failed" || meshRun.state === "interrupted" || meshRun.state === "skipped")) meshStep = { state: "attention", hint: "The mesh was not built: see the card" };
  else meshStep = { state: "todo", hint: "Press Generate to build the case's mesh" };
  return [
    ["geometry", found.status === "ready" || found.status === "" ? { state: "done", hint: what } : { state: "attention", hint: `${what} · ${found.status.replace(/_/g, " ")}` }],
    ["fluid", from],
    ["boundaries", from],
    ["mesh", meshStep],
  ];
}

/** The six steps, their states and hints, and the step to work on next. */
export function guidedView(i: GuidedInput): GuidedView {
  const found = readCase(i.tree);
  if (found !== null) {
    const head = caseSteps(found, i);
    // Run and Results read the simulations only: a mesh-only run solved nothing.
    const sims = { ...i, runs: i.runs.filter((r) => r.kind !== "mesh") };
    const r = run(head[3][1].state === "done", sims);
    const s = results(r.state === "done", sims);
    const steps: GuidedStep[] = [...head, ["run", r] as const, ["results", s] as const].map(([id, rest]) => ({ id, label: LABELS[id], ...rest }));
    const active = steps.find((step) => step.state !== "done")?.id ?? "results";
    return { steps, active };
  }
  const bodies = readBodies(i.tree);
  const g = geometry(i, bodies);
  const f = fluid(g.state === "done", bodies, i.tree);
  const b = boundaries(f.state === "done", i);
  const m = mesh(b.state === "done", i);
  const r = run(m.state === "done", i);
  const s = results(r.state === "done", i);
  const parts: [GuidedStepId, Omit<GuidedStep, "id" | "label">][] = [
    ["geometry", g],
    ["fluid", f],
    ["boundaries", b],
    ["mesh", m],
    ["run", r],
    ["results", s],
  ];
  const steps: GuidedStep[] = parts.map(([id, rest]) => ({ id, label: LABELS[id], ...rest }));
  const active = steps.find((step) => step.state !== "done")?.id ?? "results";
  return { steps, active };
}

/** The step a name or an older name refers to ("define" is Boundaries, "import" is Geometry), or null. */
export function stepFromName(name: string): GuidedStepId | null {
  if ((GUIDED_STEPS as readonly string[]).includes(name)) return name as GuidedStepId;
  switch (name) {
    case "import":
    case "project":
      return "geometry";
    case "define":
      return "boundaries";
    case "material":
      return "fluid";
    default:
      return null;
  }
}

/** The stage each step works in (the viewport draws the mesh in Mesh and the fields in Results). */
export const STEP_STAGE: Record<GuidedStepId, "Geometry" | "Physics" | "Mesh" | "Solve" | "Results"> = {
  geometry: "Geometry",
  fluid: "Physics",
  boundaries: "Physics",
  mesh: "Mesh",
  run: "Solve",
  results: "Results",
};

/** The step after `id`, or null after the last. */
export function nextStepId(id: GuidedStepId): GuidedStepId | null {
  const at = GUIDED_STEPS.indexOf(id);
  return at >= 0 && at + 1 < GUIDED_STEPS.length ? GUIDED_STEPS[at + 1] : null;
}

/** An area in the unit a user would read it in: mm² under 1 cm², cm² under 1 m², else m². */
export function areaText(area: number): string {
  const fmt = (v: number) => (v >= 100 ? Math.round(v).toLocaleString("en-US") : Number(v.toPrecision(3)).toString());
  if (area < 1e-4) return `${fmt(area * 1e6)} mm²`;
  if (area < 1) return `${fmt(area * 1e4)} cm²`;
  return `${fmt(area)} m²`;
}

/**
 * One row of the Boundaries card's list of faces nobody assigned. With the server's description
 * it says what the face looks like ("Face 7 · flat · 39.3 cm²"), because a bare number did not let
 * a user tell the inlet of a pipe from its outlet without picking each in turn; the flat ones
 * are the candidates for an inlet, an outlet or a symmetry plane.
 */
export function faceRowText(id: number, look?: { area: number; flat: boolean }): string {
  if (look === undefined) return `Face ${id} · not assigned`;
  return `Face ${id} · ${look.flat ? "flat" : "curved"} · ${areaText(look.area)}`;
}

/** The two fields the Results card colours by, in plain words, by the run's own field name. */
export const RESULT_FIELD_WORDS: Record<string, string> = { U_mag: "Velocity", p: "Pressure" };

/**
 * The Velocity and Pressure choices. `names` is what the run's fields hold, or null before they are read:
 * then both are offered, and pressing one reads the results first.
 */
export function resultChoices(names: readonly string[] | null): { value: string; label: string }[] {
  return Object.entries(RESULT_FIELD_WORDS)
    .filter(([name]) => names === null || names.includes(name))
    .map(([value, label]) => ({ value, label }));
}

/** Why the Results card's Report button is off, or null when it can be pressed. */
export function reportBlocker(finished: boolean, hasResults: boolean): string | null {
  if (!finished) return "Finish a run first";
  return hasResults ? null : "The report needs the results";
}

/** The Run card's state line: the Solve panel's wording, but a record that still says "queued" while it reports iterations is running. */
export function runLine(run: RunSummary | null, limit: number | null): { text: string; tone: Tone } {
  if (run !== null && (run.state === "queued" || run.state === "running")) {
    const going = run.state === "running" || run.iterations > 0 || run.fraction !== null;
    if (going) return { text: run.iterations > 0 ? `Running · iteration ${run.iterations}` : "Running", tone: "busy" };
  }
  if (run !== null && run.state === "cancelling") return { text: "Stopping…", tone: "busy" };
  return stateLine(run, limit);
}
