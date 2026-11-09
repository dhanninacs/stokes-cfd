/**
 * The colour legend of the results (T-218): a small floating card in the viewport with the field's name
 * and unit, a vertical bar drawn from the colour map and five ticks from the top of the range to the
 * bottom. Shown only while a field colours the mesh (`resultsShown`). It takes no pointer, so the model
 * under it stays pickable, and it sits clear of the view cube's corner (the right) and of the scale bar
 * (the top centre): at the top left, past the left slot and under the strip's row (62 px, as in Guided mode).
 *
 * A slice painted with another field than the walls (its own choice in the Slices section) adds that field's
 * title and bar to the same card, so every colour on screen has its scale; a slice that follows the walls adds
 * nothing.
 *
 * The bar is a CSS gradient built from the view's colour map (`results.view.map`, `reverse`, `bands`), the
 * same table the tint is made from, so the legend and the surface cannot be two different maps. The ticks are
 * written in the view's format (`legendModel.formatTickWith`). Surfaces and ink are the shared tokens (`glass`, `--text`,
 * `--text-muted`); the only colours that are not are the map's own.
 */
import { useMemo, useState } from "react";
import { NO_FIELD, fieldTitle, fieldUnits, parseRangeInput, rangeForField, resultsShown, type Range } from "../panels/fieldsModel";
import { dropReadout, legendLabels, legendOf, setManualEnd } from "../panels/legendModel";
import { sliceFieldOf, visibleSlices } from "../panels/sliceModel";
import { monitorsOf, pressureDrop, runForMonitors } from "../panels/monitorsModel";
import { rangeScopeOf } from "../panels/fieldsModel";
import { steppedGradientCssFor } from "../state/colormap";
import { useStore } from "../state/store";

const TICKS = 5;

/**
 * The drop from the inlet to the outlet that the run's monitors give, as a line under the legend (T-219 D).
 * In Pa when the material's density is known (`dropReadout`), else the kinematic `Δp/ρ`; the tooltip holds the
 * kinematic value and the density the Pa figure was made from.
 */
function DropReadout({ value, inlet, outlet, units, density }: { value: number; inlet: string; outlet: string; units: string; density: number | null }) {
  const shown = dropReadout(value, units, density);
  return (
    <div className="field-legend-drop" data-ws="field-drop" title={shown.detail === "" ? undefined : shown.detail}>
      <span className="muted">{`Pressure drop, ${inlet} to ${outlet}`}</span>
      <span className="field-legend-drop-value">{`${shown.symbol} ${shown.text} ${shown.units}`}</span>
    </div>
  );
}

/** One field's title and bar with its five ticks, highest first. */
function LegendBlock({
  title,
  bar,
  ticks,
  fixed,
  scope,
  onEnd,
  values,
  onPrint,
}: {
  title: string;
  bar: string;
  ticks: string[];
  fixed: boolean;
  scope: string;
  /** Writes one end of the range (R6): present only for the field the colours follow. */
  onEnd?: (end: "min" | "max", value: number) => void;
  values?: { min: number; max: number };
  /** Toggles the print size of the whole legend (R6). */
  onPrint?: () => void;
}) {
  const [editing, setEditing] = useState<"min" | "max" | null>(null);
  const [draft, setDraft] = useState("");
  const begin = (end: "min" | "max") => {
    if (onEnd === undefined || values === undefined) return;
    setDraft(String(Number(values[end].toPrecision(4))));
    setEditing(end);
  };
  const commit = () => {
    const parsed = parseRangeInput(draft);
    if (editing !== null && parsed !== null && onEnd !== undefined) onEnd(editing, parsed);
    setEditing(null);
  };
  return (
    <>
      <div className="field-legend-title" onDoubleClick={onPrint} data-editable={onPrint ? "" : undefined} title={onPrint ? "Double-click for print size" : undefined}>
        {title}
      </div>
      {/*
        **The range's SCOPE is stated, because a component's is not the run's.** A derived `Ux` is
        ranged over the samples the viewed cut actually has - a LOCAL range - while `|U|` is ranged over
        every surface the run measured. Without the label the two read identically, and a user comparing
        two slices of one run would take their different legends as a physics change rather than a
        difference in what was sampled.
      */}
      {scope !== "" && (
        <div className="field-legend-scope muted" data-ws="legend-scope">
          {scope}
        </div>
      )}
      <div className="field-legend-body">
        <div className="field-legend-bar" style={{ background: bar }} aria-hidden="true" />
        <ol className="field-legend-ticks" aria-label="Values from highest to lowest">
          {ticks.map((label, i) => (
            <li
              key={i}
              data-end={i === 0 ? "max" : i === TICKS - 1 ? "min" : undefined}
              data-editable={onEnd !== undefined && (i === 0 || i === TICKS - 1) ? "" : undefined}
              title={onEnd !== undefined && (i === 0 || i === TICKS - 1) ? "Double-click to set this end" : undefined}
              onDoubleClick={onEnd !== undefined && (i === 0 || i === TICKS - 1) ? () => begin(i === 0 ? "max" : "min") : undefined}
            >
              {editing !== null && (i === 0 ? editing === "max" : i === TICKS - 1 && editing === "min") ? (
                <input
                  className="field mono field-legend-edit"
                  value={draft}
                  autoFocus
                  aria-label={`${editing === "max" ? "Maximum" : "Minimum"} of the range`}
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={commit}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commit();
                    else if (e.key === "Escape") setEditing(null);
                  }}
                />
              ) : (
                label
              )}
            </li>
          ))}
        </ol>
      </div>
      {fixed && <div className="field-legend-note">fixed range</div>}
    </>
  );
}

export function FieldLegend() {
  const [print, setPrint] = useState(false);
  const stage = useStore((s) => s.stage);
  const view = useStore((s) => s.results.view);
  const data = useStore((s) => s.results.data);
  const slices = useStore((s) => s.slices);
  const runs = useStore((s) => s.run.runs);
  const resultsRun = useStore((s) => s.results.run);
  // **The bar follows the map, its direction and the band count** (results item 3, R1): a gradient beside a
  // banded surface would be a legend that lies about its own colours.
  const look = legendOf(view);
  // Only in the Results stage, with a finished run whose monitors have an inlet and an outlet.
  const drop = useMemo(() => {
    const run = runForMonitors(runs, resultsRun);
    return run === null ? null : pressureDrop(monitorsOf(run), fieldUnits(data, "p"));
  }, [runs, resultsRun, data]);
  /*
    TODO(R1 follow-up): the density is not read here. It is the fluid region's material density at its
    reference temperature: `readFluidRegion(tree)` (guided/guidedTree.ts) names the material and T_ref, and the
    value is the server's `material.evaluate` call (`materialReadout` in guided/commands.ts asks it for
    `density` and `viscosity`). That is an async server call, so it belongs in a small store field filled when
    the results load, not in a render; until then the readout stays kinematic (`Δp/ρ`) and says so.
  */
  const density: number | null = null;
  const readout =
    stage === "Results" && drop !== null ? <DropReadout value={drop.value} inlet={drop.inlet} outlet={drop.outlet} units={drop.units} density={density} /> : null;
  // The walls' field, then the fields only the slices use (a slice that follows the walls adds none).
  const wallsShown = resultsShown(stage, view, data !== null);
  const fields: string[] = [];
  if (wallsShown) fields.push(view.field);
  if (stage === "Results" && data !== null) {
    for (const spec of visibleSlices(slices)) {
      const field = sliceFieldOf(spec, view.field);
      if (field !== NO_FIELD && !fields.includes(field) && rangeForField(view, data, field) !== null) fields.push(field);
    }
  }
  const blocks = fields.map((field) => ({ field, range: rangeForField(view, data, field) })).filter((b): b is { field: string; range: Range } => b.range !== null);
  if (blocks.length === 0) {
    return readout === null ? null : (
      <div className="field-legend glass" role="group" aria-label="Pressure drop" data-ws="field-legend-drop">
        {readout}
      </div>
    );
  }
  const label = blocks.map((b) => fieldTitle(data, b.field)).join("; ");
  return (
    <div
      className={`field-legend glass${print ? " field-legend--print" : ""}`}
      role="group"
      aria-label={`Colour legend: ${label}`}
      data-ws="field-legend"
    >
      {blocks.map((b) => (
        <LegendBlock
          key={b.field}
          title={fieldTitle(data, b.field)}
          bar={steppedGradientCssFor(look.map, look.bands, look.reverse, "to top", b.range)}
          ticks={legendLabels(b.range, view, TICKS)}
          fixed={view.lock !== null && view.lock.field === b.field}
          scope={rangeScopeOf(b.field, view.lock !== null && view.lock.field === b.field)}
          onPrint={() => setPrint((on) => !on)}
          values={b.field === view.field ? { min: b.range.min, max: b.range.max } : undefined}
          onEnd={
            b.field === view.field
              ? (end, value) =>
                  useStore.setState((s) => {
                    const next = { ...s.results, view: setManualEnd(s.results.view, s.results.data, end, value) };
                    return next.view === s.results.view ? s : { results: next };
                  })
              : undefined
          }
        />
      ))}
      {readout}
    </div>
  );
}
