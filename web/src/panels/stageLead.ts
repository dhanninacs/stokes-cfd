/**
 * What a stage is FOR, in one line — the card's heading when the next step is not the stage's own.
 *
 * **Claude's GUI review item 6, second half**: *"it is the Geometry stage, so the card should lead with
 * the geometry step ("Import your geometry or use the demo, then choose Mesh") and show the host
 * problem as a short secondary line."* The card was saying *"The mesh is out of date: build it again"*
 * under a **GEOMETRY** heading, which is a step from the NEXT stage.
 *
 * **Only when the step belongs to another stage.** When the step is this stage's, the step IS the
 * answer and a lead line above it would be a second heading for one subject.
 */
import type { Stage } from "../state/store";

/** The stage's own purpose, in the words the plan uses. */
export const STAGE_LEAD: Record<Stage, string> = {
  Geometry: "Import your geometry or use the demo, then choose Mesh.",
  Mesh: "Choose a mesher and its sizes, then generate the mesh.",
  Physics: "Give the fluid a material and the boundaries their conditions.",
  Solve: "Choose the solver and the run controls, then start the run.",
  Results: "Read the fields, the residuals and the reports of a finished run.",
  Explore: "Sweep a design, sample the space and read what moves the result.",
};

/**
 * The same purposes in a few words, for the card's visible line; `STAGE_LEAD` is its tooltip (the owner's
 * rule: a few words on the screen, the sentence in the tooltip).
 */
export const STAGE_WORDS: Record<Stage, string> = {
  Geometry: "Import the geometry",
  Mesh: "Mesher and sizes",
  Physics: "Material and boundaries",
  Solve: "Solver and run controls",
  Results: "Fields and reports",
  Explore: "Sweeps and studies",
};

/** The stage a next-step id belongs to, from its prefix: `mesh.*`, `geometry.*`, `solve.*`. */
export function stageOfStep(id: string): Stage | null {
  const head = id.split(".")[0];
  const known: Record<string, Stage> = {
    geometry: "Geometry",
    import: "Geometry",
    mesh: "Mesh",
    physics: "Physics",
    boundary: "Physics",
    region: "Physics",
    solve: "Solve",
    run: "Solve",
    results: "Results",
  };
  return known[head] ?? null;
}
