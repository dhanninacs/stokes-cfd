/**
 * The measurement's readout (T-098): the distance, printed at the middle of the line it belongs to.
 *
 * The viewport draws the measurement itself in world space, so the line and its end markers are exact
 * at any orbit; the figure has to be HTML, because a texture with a distance baked into it would
 * blur when the camera moves toward it and would have to be rebuilt for every theme. So the scene
 * projects the midpoint and tells the store where the label goes (`setMeasureReadout`), and this
 * component prints it there — the same split the scale bar uses.
 *
 * It shows the figure and a caption saying what the figure is: an edge's length, a face's area, a
 * body's volume or the distance between two picked points.
 *
 * It must not take the pointer: the next click on the model starts the next measurement, and a label
 * sitting under the cursor would swallow it. It is kept clear of the top strip by the scene, which is
 * the only side that knows both the camera and the viewport's size (see MEASURE_LABEL_TOP in
 * scene.ts), because the top centre is already shared by the scale bar and the import line.
 */
import { useStore } from "../state/store";

export function MeasureReadout() {
  const readout = useStore((s) => s.measureReadout);
  if (readout === null) return null;
  return (
    <div
      className="measure-readout"
      data-ws="measure-readout"
      style={{ left: `${readout.x}px`, top: `${readout.y}px` }}
      title={readout.caption}
    >
      {/* The figure and what it IS. A bare number does not say whether it is the gap between two
          things or the size of one, and with four kinds of result that distinction is the whole
          message: `12 mm` and `12 mm²` must not be confusable, nor a distance with a length. */}
      <span className="measure-readout-label">{readout.label}</span>
      <span className="measure-readout-caption">{readout.caption}</span>
    </div>
  );
}
