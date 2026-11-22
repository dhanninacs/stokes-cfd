import { isoKey, isoViewportText } from "../panels/isoModel";
import { contoursLine } from "./contourLayerModel";
import { useStore } from "../state/store";

/**
 * One line at the top of the viewport while an iso-surface is in the scene: `iso T = 300 K, 12,345 triangles`,
 * the count of the mesh the scene holds (`state.isoDrawn`, written by `IsoLayer`). Gone when the surface is
 * hidden or removed. It never takes the pointer.
 */
export function IsoStatusLine() {
  const iso = useStore((s) => s.iso);
  const drawn = useStore((s) => s.isoDrawn);
  const contours = useStore((s) => s.contours);
  const contoursDrawn = useStore((s) => s.contoursDrawn);
  const surface = iso.surface;
  if (contours !== null && contoursDrawn.total > 0) {
    return (
      <div className="iso-status" data-testid="viewport-iso-status" aria-live="polite">
        {contoursLine(contours, contoursDrawn)}
      </div>
    );
  }
  if (surface === null || drawn.state !== "shown" || drawn.key !== isoKey(surface) || !iso.visible) return null;
  return (
    <div className="iso-status" data-testid="viewport-iso-status" aria-live="polite">
      {isoViewportText(surface, drawn.triangles)}
    </div>
  );
}
