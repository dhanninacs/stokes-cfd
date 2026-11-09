/**
 * Vector arrows on a slice: density and scale, Show and Hide (claude-win2's item 2).
 *
 * **The arrows come from the SLICE's own data** — the full three-component velocity the run already
 * serves — so this control needs no new command and no round trip beyond the slice fetch it shares with
 * the colouring.
 *
 * **Why arrows at all**: a colour map says how fast, and nothing says where the flow goes. On a duct's
 * cross-section that is the half of the story a CFD user reads first.
 *
 * **Where the drawing goes, recorded rather than re-derived**: `viewport/sliceLayer.ts` already holds
 * what an arrow needs - `group.positions` (a `Float32Array` of slice points) and the field values it
 * fetched for the colouring, `usable.values.subarray(at, at + group.vertices)`. So the arrows are built
 * THERE, in the same pass that colours the cut, and **not** from a second fetch of the slice: the layer
 * has the positions and the values in hand, and a second request would be the same bytes twice.
 *
 * **The control ships before the drawing** because its state - shown, density, scale - is what the layer
 * reads; a drawing with no switch is a picture a user cannot turn off.
 */
import { useState } from "react";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import { ARROW_DENSITIES, VELOCITY_MAGNITUDE, isVectorFieldName } from "./arrowModel";
import { Note } from "../components/Note";

export default function ArrowsSection() {
  const arrows = useStore((s) => s.arrows);
  const setArrows = useStore((s) => s.setArrows);
  const hasSlice = useStore((s) => s.results.data?.slice === true);
  /*
    **The arrows need a VECTOR field.** Measured: with `|U|` chosen, the slice carries one number per
    vertex and there is no direction in it - so Show did nothing visible, which is the worst kind of
    control. The field menu offers the scalars (`|U|` first), and `U` is fetchable; until the field is
    a vector the section says so instead of pretending.
  */
  /*
    **Any field whose values can be a VELOCITY is enough**, because the slice layer asks the server for
    the vector behind a speed (`sliceValues.requestedFieldOf`, decision c). Measured: with `|U|`
    selected this section used to be disabled and now the arrows are drawn - the check was left over
    from before the substitution existed.
  */
  const field = useStore((s) => s.results.view.field);
  const vectorField = isVectorFieldName(field) || VELOCITY_MAGNITUDE.has(field);
  const [density, setDensity] = useState<number>(ARROW_DENSITIES[2]);
  const [scale, setScale] = useState(1);
  return (
    <section className="mesh-section" aria-label="Arrows" data-ws="results-arrows">
      <div className="section-title">Arrows</div>
      <div className="select-wrap">
        <select
          className="field"
          aria-label="How many arrows"
          value={String(density)}
          disabled={!hasSlice || !arrows.shown}
          onChange={(e) => setDensity(Number(e.target.value))}
          data-ws="arrows-density"
        >
          {ARROW_DENSITIES.map((n) => (
            <option key={n} value={n}>
              {n} arrows
            </option>
          ))}
        </select>
      </div>
      <label className="arrows-scale">
        <span className="muted">Length</span>
        <input
          type="range"
          min={0.2}
          max={3}
          step={0.1}
          aria-label="Arrow length"
          value={scale}
          disabled={!hasSlice || !arrows.shown}
          onChange={(e) => setScale(Number(e.target.value))}
          data-ws="arrows-scale"
        />
        <span className="muted mono">{scale.toFixed(1)}x</span>
      </label>
      <Button
        aria-label={arrows.shown ? "Hide the arrows" : "Show the arrows"}
        aria-pressed={arrows.shown}
        disabled={!hasSlice || !vectorField}
        onClick={() => setArrows({ shown: !arrows.shown, density, scale })}
        data-ws="arrows-toggle"
      >
        {arrows.shown ? "Hide" : "Show"}
      </Button>
      {!hasSlice && (
        <Note className="mesh-hint" tip="Add a slice first: the arrows are drawn from its velocity.">Add a slice first</Note>
      )}
      {hasSlice && !vectorField && (
        <Note className="mesh-hint" tip={`The arrows need the velocity field (U) rather than ${field}, which holds only its magnitude.`} data-ws="arrows-need-vector">
          Needs the velocity field
        </Note>
      )}
    </section>
  );
}
