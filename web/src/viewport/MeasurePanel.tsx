/**
 * The Measure panel: the CAD-style readout that sits beside the view while the Measure tool is on.
 *
 * The floating label at the measurement's midpoint gives one figure at a glance; this panel gives the
 * whole answer, the way a CAD measure dialog does:
 *
 * * **What to pick** — Vertex, Edge, Face or Body. These are the view's own pick modes, so the edge or
 *   vertex under the pointer is highlighted before the click exactly as it is when selecting, and the
 *   click measures what was highlighted.
 * * **What was picked** — the first and the second element, described (`Line 12`, `Face 3 (curved)`).
 * * **Every figure the pair supports** — the distance and its dX, dY and dZ components, the angle when
 *   both elements have a direction, and each element's own length, area or volume.
 *
 * The rows are built by `measureDetails` (measure.ts), which is pure and tested; this component only
 * draws them. Clear starts a new measurement without leaving the tool; the close button leaves it.
 */
import { X } from "lucide-react";
import type { PickMode } from "../state/pick";
import { useStore } from "../state/store";
import { Segmented } from "../components/ui";

const TARGETS: { value: PickMode; label: string }[] = [
  { value: "point", label: "Vertex" },
  { value: "edge", label: "Edge" },
  { value: "face", label: "Face" },
  { value: "body", label: "Body" },
];

const HINT: Record<"first" | "second" | "done", string> = {
  first: "Click a first element.",
  second: "Click a second element to measure between them.",
  done: "Click again to start a new measurement.",
};

export function MeasurePanel() {
  const details = useStore((s) => s.measureDetails);
  const pickMode = useStore((s) => s.pickMode);
  if (details === null) return null;
  const { setPickMode, resetMeasure, setMeasureMode } = useStore.getState();
  return (
    <section className="measure-panel" data-ws="measure-panel" aria-label="Measure">
      <header className="measure-panel-head">
        <strong>Measure</strong>
        <button type="button" className="measure-panel-close" aria-label="Close Measure" title="Close Measure (Esc)" onClick={() => setMeasureMode(false)}>
          <X size={13} aria-hidden="true" />
        </button>
      </header>
      <Segmented label="Measure target" value={pickMode} options={TARGETS} onChange={(mode) => setPickMode(mode)} />
      <p className="measure-panel-hint">{HINT[details.step]}</p>
      {details.first !== null && (
        <p className="measure-panel-pair" data-ws="measure-pair">
          {details.first}
          {details.second !== null && <> → {details.second}</>}
        </p>
      )}
      {details.step !== "first" && <p className="measure-panel-title">{details.title}</p>}
      {details.rows.length > 0 && (
        <dl className="measure-panel-rows" data-ws="measure-rows">
          {details.rows.map((row) => (
            <div key={row.label} className="measure-panel-row" data-primary={row.label === "Distance" || undefined}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {details.step !== "first" && (
        <button type="button" className="measure-panel-clear" onClick={() => resetMeasure()} data-ws="measure-clear">
          Clear
        </button>
      )}
    </section>
  );
}
