/**
 * The 2D switch and the plane picker (ADR-0031), shared by the Guided Mesh card and the Mesh page's Basic block.
 *
 * Every control is one `mesh.configure` of the settings' own `two_d` and `plane`; the server validates and the
 * refusal comes back as its sentence. Nothing is decided here beyond which choices make sense to offer: snappyHexMesh
 * builds planar 2D only, so Axisymmetric is shown with the reason when snappy is the mesher.
 */
import type { NodeSummary } from "../api/types";
import { Segmented, Switch } from "../components/ui";
import { PLANES, planeLabel, twoDOf, type TwoDMode, type TwoDPlane } from "../viewport/twoD";

const SNAPPY_AXISYMMETRIC = "Axisymmetric is built for blockMesh; choose blockMesh in Advanced";

/** What the switch's line says under it, for the mode and plane chosen. */
export function twoDDescription(mode: TwoDMode | null, plane: TwoDPlane, mesher: string): string {
  if (mode === null) return "One cell thick, for a planar or axisymmetric run";
  if (mode === "axisymmetric") return `A wedge about ${plane[0]}, radius ${plane[1]}`;
  return mesher === "snappy" ? `In ${planeLabel(plane)}: the fluid body is a slab across it` : `In ${planeLabel(plane)}, one cell thick`;
}

export default function TwoDControls({
  settings,
  disabled,
  configure,
}: {
  settings: NodeSummary | null;
  disabled: boolean;
  configure: (values: Record<string, unknown>) => void;
}) {
  const current = twoDOf(settings);
  const mesher = typeof settings?.params.mesher === "string" ? settings.params.mesher : "snappy";
  const plane: TwoDPlane = current?.plane ?? (settings?.params.plane === "xz" || settings?.params.plane === "yz" ? settings.params.plane : "xy");
  return (
    <div className="flex flex-col gap-2" data-ws="mesh-two-d">
      <Switch
        checked={current !== null}
        label="2D"
        description={twoDDescription(current?.mode ?? null, plane, mesher)}
        disabled={disabled}
        onChange={(on) => configure({ two_d: on ? "planar" : "off" })}
      />
      {current !== null && (
        <>
          <Segmented<TwoDMode>
            label="2D kind"
            value={current.mode}
            options={[
              { value: "planar", label: "Planar" },
              { value: "axisymmetric", label: "Axisymmetric", disabled: mesher === "snappy" && current.mode !== "axisymmetric" ? SNAPPY_AXISYMMETRIC : undefined },
            ]}
            onChange={(mode) => !disabled && mode !== current.mode && configure({ two_d: mode })}
          />
          <Segmented<TwoDPlane>
            label="2D plane"
            value={current.plane}
            options={PLANES.map((p) => ({ value: p, label: planeLabel(p) }))}
            onChange={(p) => !disabled && p !== current.plane && configure({ plane: p })}
          />
        </>
      )}
    </div>
  );
}
