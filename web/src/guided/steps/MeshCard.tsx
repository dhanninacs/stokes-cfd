/**
 * Mesh: how fine, whether the walls get layers, Generate, and what came out.
 *
 * Coarse, Medium and Fine set the mesh settings' base size to a twentieth, a fortieth or an eightieth of the
 * fluid body's smallest side, and the card shows the length each one means. Boundary layers is the settings' own
 * layer count on every wall (three when it is switched on). Cores is the mesh settings' own `cores` (the MPI
 * processes snappyHexMesh runs on), separate from the Run card's, which is the solver's. Generate and Cancel are the Mesh panel's buttons;
 * the result is the cell count and the mesher's own verdict on quality, and a switch draws the cell edges. The 2D
 * switch and plane picker are the settings' own `two_d` and `plane` (ADR-0031).
 */
import { useMemo, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Button, Switch } from "../../components/ui";
import { cancelMesh, generateMesh, useGenerateState, useMeshContext } from "../../panels/meshRun";
import { followsShape } from "../../panels/mesherKinds";
import { cancelState, findMeshNodes, formatCount, formatSeconds, isActive, keptMeshes, phaseNow, progressOf, qualityRows, readResult, stateLine } from "../../panels/meshPanelModel";
import { useStore } from "../../state/store";
import { currentScene } from "../../viewport/scene";
import GuidedCard from "../GuidedCard";
import { ErrorLine, Note } from "../controls";
import CaseMeshCard from "./CaseMeshCard";
import FinenessSlider from "./FinenessSlider";
import TwoDControls from "../../panels/TwoDControls";
import { baseSizeOf, chooseMeshResult, configureMesh, coresOf, layersOf, mesherOf, setMeshCores, setMesher } from "../commands";
import type { GuidedStep } from "../guidedModel";
import { fluidBody, meshSettings, readBodies, readCase, unmeshedFlowBoundaries } from "../guidedTree";
import { SWITCH_LAYERS, finenessOf, lengthText, type Fineness } from "../sizing";
import { useAction } from "../useAction";
import { plainSentence } from "../words";

const WORDS: Record<Fineness, string> = { coarse: "Coarse", medium: "Medium", fine: "Fine" };

export default function MeshCard({ step }: { step: GuidedStep }) {
  const tree = useStore((s) => s.tree);
  const live = useStore((s) => s.mesh);
  const meshDisplay = useStore((s) => s.meshDisplay);
  const action = useAction();
  const generate = useGenerateState();

  const settings = useMemo(() => meshSettings(tree), [tree]);
  const view = useMemo(() => readResult(findMeshNodes(tree).result), [tree]);
  const phase = phaseNow(view, live);
  const active = isActive(phase);
  const progress = progressOf(view, live);
  const line = stateLine(view, live);
  const cancel = cancelState(phase);

  // The fluid body's box, asked of the scene when the tree or the drawn geometry changes (the scene is not a store).
  const geometry = useStore((s) => s.geometry);
  const fluidId = useMemo(() => fluidBody(readBodies(tree))?.id ?? null, [tree]);
  const foamCase = useMemo(() => readCase(tree), [tree]);
  const box = useMemo(() => (fluidId === null ? null : (currentScene()?.sourceBounds(fluidId) ?? null)), [fluidId, geometry]);
  const baseSize = baseSizeOf(settings);
  const level = finenessOf(box, baseSize);
  const layers = layersOf(settings);
  const cores = coresOf(settings);
  const meshers = useMeshContext((c) => c.meshers);
  const mesherId = mesherOf(settings) ?? "blockmesh";
  const mesherInfo = meshers.find((m) => m.id === mesherId) ?? null;
  // The meshes kept, one per mesher (ADR-0034): a choice only when there is more than one.
  const kept = useMemo(() => keptMeshes(tree, new Map(meshers.map((m) => [m.id, m.label]))), [tree, meshers]);
  const [coresDraft, setCoresDraft] = useState<string | null>(null);
  const commitCores = () => {
    const text = coresDraft;
    setCoresDraft(null);
    if (text === null) return;
    const n = Number(text.trim());
    if (!Number.isInteger(n) || n < 1 || n > 256) {
      action.fail("Cores is a whole number from 1 to 256.");
      return;
    }
    if (n !== (cores ?? 1)) void action.run(() => setMeshCores(n));
  };

  const done = view !== null && view.phase === "done";
  const lost = useMemo(() => unmeshedFlowBoundaries(tree), [tree]);
  const indeterminate = active && (progress.fraction === null || progress.fraction === 0);

  if (foamCase !== null) return <CaseMeshCard step={step} caseId={foamCase.id} />;

  return (
    <GuidedCard step={step}>
      <label className="guided-field">
        <span>Mesher</span>
        <select
          className="field"
          aria-label="Mesher"
          title={mesherInfo?.why || "The program that builds the mesh"}
          disabled={action.busy || active || meshers.length === 0}
          value={mesherId}
          onChange={(e) => {
            const next = e.target.value;
            if (next !== mesherId) void action.run(() => setMesher(next));
          }}
          data-ws="guided-mesher"
        >
          {meshers.length === 0 && <option value={mesherId}>{mesherId}</option>}
          {meshers.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.built} title={m.built ? "" : m.why}>
              {m.label}
              {m.built ? "" : " (not available)"}
            </option>
          ))}
        </select>
      </label>
      {kept.length > 0 && (
        <label className="guided-field">
          <span>Mesh to use</span>
          <select
            className="field"
            aria-label="Mesh to use"
            title="The meshes made so far, one per mesher. The one chosen is shown and is what Run solves on."
            disabled={action.busy || active}
            value={kept.find((k) => k.active)?.id ?? ""}
            onChange={(e) => {
              const chosen = kept.find((k) => k.id === e.target.value);
              if (chosen !== undefined && !chosen.active) void action.run(() => chooseMeshResult(chosen.id, chosen.mesher));
            }}
            data-ws="guided-mesh-use"
          >
            {kept.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {!followsShape(mesherId) && <Note tip="blockMesh fills the bounding box; snappyHexMesh and the cfMesh types follow the part's surfaces.">Meshes the bounding box, not the shape</Note>}
      <FinenessSlider box={box} baseSize={baseSize} disabled={action.busy || active} onCommit={(size) => void action.run(() => configureMesh({ base_size: size }))} />
      {baseSize !== null && <Note>{level === null ? "Custom size" : WORDS[level]} · {lengthText(baseSize)} cells</Note>}
      <Switch
        checked={layers > 0}
        label="Boundary layers"
        description={layers > 0 ? `${layers} layers on every wall` : "Thin cells along the walls"}
        disabled={action.busy || active}
        onChange={(on) => void action.run(() => configureMesh({ layers: on ? SWITCH_LAYERS : 0 }))}
      />
      <TwoDControls settings={settings} disabled={action.busy || active} configure={(values) => void action.run(() => configureMesh(values))} />
      <label className="guided-field">
        <span>Cores</span>
        <input
          className="field"
          inputMode="numeric"
          spellCheck={false}
          autoComplete="off"
          aria-label="Number of processor cores for meshing"
          title="Processor cores snappyHexMesh uses. More cores mesh faster; blockMesh always runs on one."
          disabled={active || action.busy}
          value={coresDraft ?? (cores === null ? "1" : String(cores))}
          onChange={(e) => setCoresDraft(e.target.value)}
          onBlur={commitCores}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") setCoresDraft(null);
          }}
          data-ws="guided-mesh-cores"
        />
      </label>
      <div className="guided-actions">
        <Button
          variant="primary"
          aria-disabled={!generate.enabled || action.busy || undefined}
          title={generate.enabled ? "Build the mesh" : generate.reason}
          onClick={() => generate.enabled && void generateMesh()}
          data-ws="guided-generate"
        >
          {active ? "Generating…" : generate.label}
        </Button>
        {cancel.visible && (
          <Button aria-disabled={!cancel.enabled || undefined} title={cancel.enabled ? "Stop building the mesh" : cancel.reason} onClick={() => cancel.enabled && void cancelMesh()} data-ws="guided-cancel-mesh">
            Cancel
          </Button>
        )}
      </div>
      {!generate.enabled && !active && <Note>{generate.reason}</Note>}
      {active && (
        <div className="mesh-bar" role="progressbar" aria-label="Mesh progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={indeterminate || progress.fraction === null ? undefined : Math.round(progress.fraction * 100)} data-indeterminate={indeterminate} data-tone="busy">
          <span style={{ width: `${Math.round((progress.fraction ?? 0) * 100)}%` }} />
        </div>
      )}
      {active && (
        <p className="guided-line muted">
          <LoaderCircle size={13} className="mesh-spin" aria-hidden="true" /> {line.text}
        </p>
      )}
      {done && !active && view !== null && (
        <div className="guided-readout" data-ws="guided-mesh-result" data-ok={view.ok}>
          <strong>
            {formatCount(view.cells)} cells · quality {view.ok ? "ok" : "needs a look"}
          </strong>
          {view.seconds > 0 && <span className="muted">built in {formatSeconds(view.seconds)}</span>}
          {step.state === "attention" && <span className="muted" title="The case changed since this mesh was built.">Out of date</span>}
          {/* What "needs a look" means: the checks above their limits (the elbow's tet mesh said only
              "needs a look", and the numbers were in Full mode's Mesh panel). */}
          {!view.ok &&
            qualityRows(view)
              .filter((row) => row.verdict === "warn")
              .map((row) => (
                <span key={row.key} className="muted" data-ws="guided-mesh-issue">
                  {row.label} {row.valueText} · {row.limitText}
                </span>
              ))}
          {/* The service's own findings about this mesh: a bent symmetry plane, short layers. */}
          {view.warnings.map((warning) => (
            <span key={warning} className="muted" data-ws="guided-mesh-warning">
              {plainSentence(warning)}
            </span>
          ))}
        </div>
      )}
      {done && !active && lost.length > 0 && (
        <ErrorLine
          text={`The mesh has no faces for ${lost.join(", ")}: the mesher lost ${lost.length === 1 ? "it" : "them"}. Make the cells smaller than ${lost.length === 1 ? "its" : "their"} width or use snappyHexMesh, then generate again.`}
        />
      )}
      {!done && !active && view !== null && (view.phase === "failed" || view.phase === "skipped" || view.phase === "interrupted") && <ErrorLine text={plainSentence(view.error !== "" ? view.error : line.text)} />}
      {done && (
        <Switch
          checked={meshDisplay === "cells"}
          label="Show mesh cells"
          description="Draw the edges of the cells"
          onChange={(on) => useStore.getState().setMeshDisplay(on ? "cells" : "surface")}
        />
      )}
      <ErrorLine text={action.error} />
    </GuidedCard>
  );
}
