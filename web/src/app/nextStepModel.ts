/**
 * The "Next step" button of the stage bar (T-219 C): what the case needs next, in the order a person works
 * it: Import geometry, Define boundaries, Generate mesh, Run, Show results. One pure function over what the
 * stores already know, so the order, the labels and the reasons are tested in Node and the button is a
 * thin renderer.
 *
 * It follows the server's own checks and does not second-guess them: the set-up step is `setup.check`'s
 * state and its `next` sentence; the mesh step is the Generate button's state and reason; the run step is
 * the Run button's state and reason (`solve.check`'s first problem). A step that cannot be taken yet is
 * returned disabled with the reason, which the button carries as its tooltip.
 *
 * Import rule: no React, no store, no DOM.
 */
import type { SetupCheck } from "../api/types";
import type { MeshPhase } from "../panels/meshPanelModel";
import { isRunActive, type RunSummary } from "../panels/runApi";

export type StepId = "project" | "import" | "define" | "mesh" | "run" | "results" | "done";

export interface NextStep {
  id: StepId;
  /** The button's words: "Generate mesh", "Run", "Show results". */
  label: string;
  enabled: boolean;
  /** Why it is disabled, or what pressing it does when it is not. Always a sentence: it is the tooltip. */
  reason: string;
}

export interface NextInput {
  hasProject: boolean;
  /** A part exists in the project. */
  hasParts: boolean;
  /** A file is going up or being read. */
  importing: boolean;
  /** The last `setup.check`, or null before it has answered. */
  setup: SetupCheck | null;
  meshPhase: MeshPhase;
  /** The mesh result says the geometry, tags or settings changed since it was made. */
  meshStale: boolean;
  /** Generate's state (`generateState`). */
  generate: { enabled: boolean; reason: string };
  /** The run records, newest first. */
  runs: readonly Pick<RunSummary, "state" | "stale">[];
  /** Run's state (`runButton`). */
  runButton: { enabled: boolean; reason: string };
  /** The Results stage is in front with a run's fields read. */
  resultsShown: boolean;
}

const MESH_ACTIVE: readonly MeshPhase[] = ["queued", "running", "cancelling"];

export function nextStep(i: NextInput): NextStep {
  if (!i.hasProject) return { id: "project", label: "New project", enabled: false, reason: "Create or open a project first (the project menu)." };
  if (i.importing) return { id: "import", label: "Importing…", enabled: false, reason: "The file is still being read. The part appears when it is done." };
  if (!i.hasParts) return { id: "import", label: "Import geometry", enabled: true, reason: "Load a CAD file: STEP, IGES, BREP, STL, OBJ or 3MF." };

  if (i.setup === null || i.setup.state !== "ready") {
    const said = i.setup?.next.trim() ?? "";
    return { id: "define", label: "Define boundaries", enabled: true, reason: said !== "" ? said : "Give the body a material and its inlet, outlet and walls." };
  }

  if (MESH_ACTIVE.includes(i.meshPhase)) return { id: "mesh", label: "Meshing…", enabled: false, reason: "The mesh is being generated." };
  if (i.meshPhase !== "done" || i.meshStale) {
    return { id: "mesh", label: "Generate mesh", enabled: i.generate.enabled, reason: i.generate.enabled ? (i.meshStale ? "The mesh is out of date: build it again." : "Build the mesh from the geometry and tags.") : i.generate.reason };
  }

  const latest = i.runs[0];
  if (latest !== undefined && isRunActive(latest.state)) return { id: "run", label: "Running…", enabled: false, reason: "A run is in progress. Stop it with the button in the top bar." };
  const finished = latest !== undefined && (latest.state === "done" || latest.state === "done_unconverged") && !latest.stale;
  if (!finished) {
    return { id: "run", label: "Run", enabled: i.runButton.enabled, reason: i.runButton.enabled ? "Run the simulation." : i.runButton.reason };
  }

  if (i.resultsShown) return { id: "done", label: "Results shown", enabled: false, reason: "The run's fields are on screen. Change something to go round again." };
  return { id: "results", label: "Show results", enabled: true, reason: "Read the run's fields and show them on the mesh." };
}
