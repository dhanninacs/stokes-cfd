/**
 * Mesh, for an imported OpenFOAM case (a tutorial): Generate runs the case's own mesh steps and shows
 * the mesh, so a case with no CAD is meshed and looked at before anything is solved.
 *
 * The steps are the case's recipe (blockMesh, snappyHexMesh, createZones, ...), run as a mesh-only run
 * (`run.start {case, mesh_only}`); there is no size to choose because the case's dictionaries say it.
 * When the run ends the mesh is read like a run's fields and drawn in the viewport, and the cell edges
 * switch draws the cells. A full Run builds the mesh too, so this step is optional but never wasted.
 */
import { LoaderCircle } from "lucide-react";
import { Button, Switch } from "../../components/ui";
import { isRunActive } from "../../panels/runApi";
import { generateCaseMesh, useRunContext } from "../../panels/runControl";
import { stateLine } from "../../panels/runPanelModel";
import { useStore } from "../../state/store";
import GuidedCard from "../GuidedCard";
import { ErrorLine, Note } from "../controls";
import type { GuidedStep } from "../guidedModel";

export default function CaseMeshCard({ step, caseId }: { step: GuidedStep; caseId: string }) {
  const runs = useStore((s) => s.run.runs);
  const meshDisplay = useStore((s) => s.meshDisplay);
  const busy = useRunContext((c) => c.busy);
  const error = useRunContext((c) => c.error);
  const meshRun = runs.find((r) => r.kind === "mesh") ?? null;
  const anyActive = runs.some((r) => isRunActive(r.state));
  const building = meshRun !== null && isRunActive(meshRun.state);
  const built = meshRun !== null && meshRun.state === "done";
  const line = meshRun !== null ? stateLine(meshRun, null) : null;

  return (
    <GuidedCard step={step}>
      <Note tip="The steps come from the case's own Allrun or blockMeshDict; edit its dictionaries from the tree (Full mode) to change the mesh.">
        Builds the mesh from the case's own mesh steps
      </Note>
      <div className="guided-actions">
        <Button
          variant={built ? "default" : "primary"}
          aria-disabled={busy || anyActive || undefined}
          title={anyActive ? "A run is going; wait for it to finish" : "Run the case's mesh steps and show the mesh"}
          onClick={() => !busy && !anyActive && void generateCaseMesh(caseId)}
          data-ws="guided-case-generate"
        >
          {building ? "Generating…" : built ? "Generate again" : "Generate"}
        </Button>
      </div>
      {line !== null && (
        <p className={`guided-line${building ? " muted" : ""}`} data-ws="guided-case-mesh-state">
          {building && <LoaderCircle size={13} className="mesh-spin" aria-hidden="true" />} {line.text}
        </p>
      )}
      {built && (
        <Switch
          checked={meshDisplay === "cells"}
          label="Show mesh cells"
          description="Draw the edges of the cells"
          onChange={(on) => useStore.getState().setMeshDisplay(on ? "cells" : "surface")}
        />
      )}
      <ErrorLine text={error} />
    </GuidedCard>
  );
}
