/**
 * The Mesh page's Basic block: how fine (Coarse / Medium / Fine) and whether the walls get layers.
 *
 * The same two decisions the Mesh card makes, through the same command (`configureMesh` is one
 * `mesh.configure`) and the same sizes (`guided/sizing.ts`). The exact base size and layer count stay in
 * Advanced; a size typed there that matches no choice leaves all three unselected. A few words on the
 * controls, the sentences in tooltips.
 */
import { useEffect, useMemo, useState } from "react";
import type { NodeSummary } from "../api/types";
import { Switch } from "../components/ui";
import { baseSizeOf, configureMesh, layersOf } from "../guided/commands";
import { fluidBody, readBodies } from "../guided/guidedTree";
import { useAction } from "../guided/useAction";
import { DEFAULT_CONTEXT, visibleFields } from "../setup/basicSets";
import { layersFor, finenessChoices } from "../setup/meshBasic";
import { useStore } from "../state/store";
import { currentScene } from "../viewport/scene";
import { estimateLine } from "./sizingModel";
import TwoDControls from "./TwoDControls";

interface Estimate {
  low: number;
  high: number;
  max_cells: number;
  over: boolean;
}

export default function MeshBasic({ node, locked }: { node: NodeSummary; locked: boolean }) {
  const tree = useStore((s) => s.tree);
  const geometry = useStore((s) => s.geometry);
  const connected = useStore((s) => s.connected);
  const execute = useStore((s) => s.execute);
  const action = useAction();
  const [estimate, setEstimate] = useState<Estimate | null>(null);

  const fluidId = useMemo(() => fluidBody(readBodies(tree))?.id ?? null, [tree]);
  const box = useMemo(() => (fluidId === null ? null : (currentScene()?.sourceBounds(fluidId) ?? null)), [fluidId, geometry]);
  const baseSize = baseSizeOf(node);
  const layers = layersOf(node);
  const choices = finenessChoices(box, baseSize);
  // Layers need snappyHexMesh: with blockMesh the switch is not drawn (the setting keeps its value).
  const showLayers = visibleFields("mesh_settings", ["layers"], node.params, DEFAULT_CONTEXT).layers !== "hidden";
  const busy = locked || action.busy;

  // The cell range the size gives, asked the way the Sizing page asks (debounced, on a change of the tree).
  useEffect(() => {
    if (!connected) return;
    let live = true;
    const timer = setTimeout(() => {
      void execute("mesh.estimate", {}, { silent: true })
        .then((answer) => live && setEstimate(answer as Estimate))
        .catch(() => undefined);
    }, 150);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [tree, connected, execute]);

  const current = choices.find((c) => c.active);
  const line = [current?.length ?? "", estimate === null ? "" : estimateLine(estimate)].filter((part) => part !== "").join(" · ");

  return (
    <div className="flex flex-col gap-2" data-ws="mesh-basic">
      <div className="segmented" role="radiogroup" aria-label="How fine the mesh is" data-ws="mesh-fineness">
        {choices.map((c, index) => {
          const unknown = c.size === null;
          return (
            <button
              key={c.value}
              type="button"
              role="radio"
              aria-checked={c.active}
              aria-disabled={busy || unknown ? true : undefined}
              tabIndex={c.active || (current === undefined && index === 0) ? 0 : -1}
              title={unknown ? "The size of the fluid is not known yet." : `Cells about ${c.length}`}
              className="no-drag"
              data-choice={c.value}
              onClick={() => {
                if (busy || c.size === null || c.active) return;
                const size = c.size;
                void action.run(() => configureMesh({ base_size: size }));
              }}
            >
              {c.label}
            </button>
          );
        })}
      </div>
      {line !== "" && (
        <p className="muted text-[11.5px]" data-ws="mesh-fineness-line" title="The cell size and the number of cells it is expected to give">
          {line}
        </p>
      )}
      {showLayers && (
        <Switch
          checked={layers > 0}
          label="Boundary layers"
          description={layers > 0 ? `${layers} on walls` : undefined}
          disabled={busy}
          onChange={(on) => void action.run(() => configureMesh({ layers: layersFor(on) }))}
        />
      )}
      <TwoDControls settings={node} disabled={busy} configure={(values) => void action.run(() => configureMesh(values))} />
      {action.error !== "" && (
        <p role="alert" className="mesh-error" data-ws="mesh-basic-error">
          {action.error}
        </p>
      )}
    </div>
  );
}
