/**
 * Geometry: bring in a CAD file and say which body is the fluid.
 *
 * Every body is a row with one choice: Fluid, Solid or Leave out. Choosing Fluid is one decision that sets
 * all of it (the body's role for the mesh and the mesh settings' domain part), through the same call the
 * Mesh stepper's button makes. A body named like the fluid (fluid, air, water, domain), or the only body,
 * is marked Fluid when the file arrives; the rows still show it, and the user can change it.
 */
import { useEffect, useMemo } from "react";
import { Upload } from "lucide-react";
import { runAction } from "../../app/actions";
import { Button } from "../../components/ui";
import { useStore } from "../../state/store";
import type { Role } from "../../panels/meshTaskPages/bodiesModel";
import GuidedCard from "../GuidedCard";
import { Chips, ErrorLine, Note, type Choice } from "../controls";
import { setBodyRole } from "../commands";
import type { GuidedStep } from "../guidedModel";
import { defaultFluidBody, readBodies, readCase } from "../guidedTree";
import { useAction } from "../useAction";

const ROLES: readonly Choice<"fluid" | "solid" | "left_out">[] = [
  { value: "fluid", label: "Fluid" },
  { value: "solid", label: "Solid" },
  { value: "left_out", label: "Leave out" },
];

/** The guesses already made, by project and set of bodies, so a user's own choice is never overruled. */
const guessed = new Set<string>();

export default function GeometryCard({ step }: { step: GuidedStep }) {
  const tree = useStore((s) => s.tree);
  const projectKey = useStore((s) => s.project?.path ?? s.project?.name ?? "");
  const bodies = useMemo(() => readBodies(tree), [tree]);
  const foamCase = useMemo(() => readCase(tree), [tree]);
  const action = useAction();
  const importing = step.state === "running";

  // The default choice, made once per set of bodies and not while the file is still being read.
  const guess = useMemo(() => defaultFluidBody(bodies), [bodies]);
  const { run } = action;
  useEffect(() => {
    if (guess === null || importing) return;
    const key = `${projectKey}|${bodies.map((b) => b.id).join(",")}`;
    if (guessed.has(key)) return;
    guessed.add(key);
    void run(() => setBodyRole(guess.id, "fluid"));
  }, [guess, importing, projectKey, bodies, run]);

  if (foamCase !== null) {
    // An imported OpenFOAM case: nothing to import or to mark; its files are edited in the tree.
    return (
      <GuidedCard step={step}>
        <Note tip="An OpenFOAM case opened from the tutorials. Its mesh and set-up are its own files; edit them from the tree (Tree, or Ctrl+B).">
          {foamCase.solver !== "" ? `${foamCase.name} · ${foamCase.solver}` : foamCase.name}
        </Note>
      </GuidedCard>
    );
  }

  return (
    <GuidedCard step={step}>
      <Button variant={bodies.length === 0 ? "primary" : "default"} onClick={() => runAction("project.importStep")} disabled={importing} data-ws="guided-import">
        <Upload size={14} aria-hidden="true" />
        {bodies.length === 0 ? "Import CAD file" : "Import another file"}
      </Button>
      {bodies.length === 0 && <Note tip="STEP, IGES, BREP, STL, OBJ or 3MF, dropped anywhere on the window.">Or drop a CAD file here</Note>}
      {bodies.length > 0 && (
        <ul className="guided-rows" aria-label="Bodies" data-ws="guided-bodies">
          {bodies.map((body) => (
            <li key={body.id} className="guided-row">
              <span className="guided-row-name" title={body.name}>
                {body.name}
              </span>
              {body.role === "hole" ? (
                <span className="muted guided-row-note">hole in the flow</span>
              ) : (
                <Chips<"fluid" | "solid" | "left_out">
                  label={`What ${body.name} is`}
                  value={body.role as "fluid" | "solid" | "left_out"}
                  choices={ROLES}
                  busy={action.busy}
                  onChange={(role) => void action.run(() => setBodyRole(body.id, role as Role))}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      {bodies.length > 0 && step.state === "attention" && <Note tip="The flow fills the Fluid body; Solid bodies stay out of the mesh.">Mark the fluid body</Note>}
      <ErrorLine text={action.error} />
    </GuidedCard>
  );
}
