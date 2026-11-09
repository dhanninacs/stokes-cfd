/**
 * The scale crosshair: how long the model is at the size it is drawn (T-097).
 *
 * Placed at the middle of the top edge rather than in a corner. A corner is where the islands live
 * and where their offsets move as panels open, collapse and resize, so a scale pinned to a corner
 * disappears behind a panel on a narrow window — which is exactly what happened on the first
 * attempt. The top centre is contested by nothing: the islands own the four corners, and the middle
 * of the top edge is free at every window size.
 *
 * The mark is a crosshair because a horizontal bar alone reads as chrome. An arm either side of the
 * centre line says "this distance, measured from here", which is how a CAD scale bar is normally
 * drawn, and it is symmetric so it stays centred wherever the figure lands.
 *
 * The arms are the measurement: their length is what the stated distance actually measures on
 * screen at the orbit target, so the drawing is the length rather than decoration beside a number.
 * Under the orthographic camera that is exact everywhere; under a perspective camera it is taken at
 * the point the user is looking at, and the tooltip says so.
 */
import { useStore } from "../state/store";

/** Height of the vertical centre line of the crosshair, in CSS pixels: a hair either side of the
 * arms, so the mark reads as a dimension rather than as a plus sign. */
const CENTRE_RISE = 4;

export function ScaleBar() {
  const bar = useStore((s) => s.scaleBar);
  if (bar === null) return null;
  const arm = Math.max(6, Math.round(bar.pixels / 2));
  const hint = bar.perspective
    ? "Scale at the view centre (a perspective view has no single scale)"
    : "Scale (orthographic: exact everywhere in the view)";
  return (
    <div className="scale-bar" data-ws="scale-bar" title={hint}>
      <div className="scale-bar-mark">
        {/* The centre line, then an arm either side: the arms together span one arm length. */}
        <span className="scale-bar-centre" style={{ height: `${CENTRE_RISE * 2}px` }} />
        <span className="scale-bar-arm" style={{ width: `${arm}px` }} />
        <span className="scale-bar-arm" style={{ width: `${arm}px` }} />
      </div>
      <span className="scale-bar-label">{bar.label}</span>
    </div>
  );
}
