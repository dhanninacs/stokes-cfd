/**
 * The Results panel (T-218): which field colours the mesh, over what range, and the mid-plane slice. A tab
 * beside Solve in the right slot; in the Results stage it is the tab in front.
 *
 * Everything that decides something is `fieldsModel.ts` (tested); `resultsControl.ts` reads the results
 * from the server; `viewport/fields.ts` colours the patches; the slices at any plane are `SlicesSection.tsx`.
 * This file only draws. It writes no project
 * state: the field, the range and the slice switch are view state in the store's `results` slice and are
 * never journaled.
 */
import { useEffect, useId, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ChevronsUpDown, CircleAlert, ClipboardCopy, Download, ImageDown, LoaderCircle, RefreshCw } from "lucide-react";
import { Button, Switch } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { collectParts } from "../state/parts";
import { BAND_CHOICES, SMOOTH, bandCount, hasPercentiles, setBandCount } from "./fieldsModel";
import { COLORMAPS, COLORMAP_IDS, gradientCssFor, type ColormapId } from "../state/colormap";
import { RANGE_MODE_WORDS, chooseRange, legendOf, rangeModeOf, setManualEnd, setMap, setReverse, type RangeChoice } from "./legendModel";
import { fadeAvailable, fadeTargets } from "./fadeResults";
import { useStore } from "../state/store";
import { placeOf } from "../workspace/geometry";
import { useWorkspace } from "../workspace/store";
import {
  NO_FIELD,
  chooseField,
  effectiveRange,
  fieldTitle,
  fieldUnits,
  formatTick,
  isLocked,
  loadedLine,
  parseRangeInput,
  pressureLabel,
  setSlice,
  setSliceEdges,
  setView,
  slicePlaneText,
  type ResultsLive,
} from "./fieldsModel";
import { showResults, useFieldOptions, useHasFinishedRun } from "./resultsControl";
import SlicesSection from "./SlicesSection";
import IsoSection from "./IsoSection";
import { copyTableText, saveTextFile } from "./monitorsExport";
import { FLOW_UNITS, csvFileName, dropSymbol, csvText, monitorRows, monitorsOf, pressureDrop, runForMonitors, tableText } from "./monitorsModel";
import ProbePanel from "./ProbePanel";
import ArrowsSection from "./ArrowsSection";
import ReportSection from "./ReportSection";
import { openExportDialog } from "./ExportImageDialog";
import LODSection from "./LODSection";
import StreamlineSection from "./StreamlineSection";
import ResidualPlot from "./ResidualPlot";
import { findSolverSettings, formatResidual, residualTarget } from "./runPanelModel";
import { Note } from "../components/Note";

function update(change: (live: ResultsLive) => ResultsLive): void {
  useStore.setState((s) => {
    const next = change(s.results);
    return next === s.results ? s : { results: next };
  });
}

/** A number field that commits on Enter or when it loses focus, and puts back what it had on Escape or on text that is not a number. */
/**
 * **Fade the results** (G-301's D4): one slider that makes the walls translucent so a cut inside them can
 * be seen.
 *
 * **Everything it needs already existed and nothing joined it up**: `part.opacity` is a model parameter
 * (ADR-0021 4.4), `StylePopover` already has a slider for ONE part, and `geometry.style` already takes a
 * LIST of nodes. So there was no way to fade the results with one gesture - a user had to open the tree
 * and slide each part - which is exactly the "not full" the audit was looking for.
 *
 * **One command, one undo step**, because the LIST is what `geometry.style` takes: sliding it and then
 * pressing Undo returns every part together rather than one at a time.
 */
function FadeSection({ hasResults }: { hasResults: boolean }) {
  const tree = useStore((s) => s.tree);
  const execute = useStore((s) => s.execute);
  const [value, setValue] = useState(1);
  const targets = useMemo(
    () => fadeTargets(collectParts(tree).map((p) => ({ id: p.id, simulate: p.simulate }))),
    [tree],
  );
  const available = fadeAvailable(hasResults, targets);
  const apply = async (next: number) => {
    setValue(next);
    try {
      // `geometry.style` takes the nodes as a LIST: one write, one undo step, every faded part together.
      await execute("geometry.style", { nodes: targets, opacity: next });
    } catch {
      /* execute logged the error and showed a toast */
    }
  };
  return (
    <section className="mesh-section" aria-label="Fade" data-ws="results-fade">
      <div className="section-title">Fade the results</div>
      <Tooltip label="Make the walls translucent so a cut inside them is visible" side="top">
        <div className="fade-row">
          <input
            type="range"
            className="fade-slider"
            aria-label="Fade the results"
            aria-valuetext={`${Math.round(value * 100)} percent opaque`}
            min={5}
            max={100}
            step={5}
            // **The slider is disabled, not hidden**, so the panel does not reflow when results arrive.
            disabled={!available}
            value={Math.round(value * 100)}
            onChange={(e) => void apply(Number(e.target.value) / 100)}
            data-ws="results-fade-slider"
          />
          <span className="muted mono fade-value">{Math.round(value * 100)}%</span>
        </div>
      </Tooltip>
      {!available && <p className="muted mesh-hint">{hasResults ? "No walls to fade." : "Show the results first."}</p>}
    </section>
  );
}

function RangeInput({
  label,
  value,
  disabled,
  why,
  onCommit,
}: {
  label: string;
  value: number | null;
  disabled: boolean;
  /** Why the input is disabled, so a screen reader says more than "dimmed" (claude-win2's 0430 brief). */
  why: string;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value === null ? "" : String(Number(value.toPrecision(4))));
  const commit = () => {
    const parsed = draft === null ? null : parseRangeInput(draft);
    setDraft(null);
    if (parsed !== null) onCommit(parsed);
  };
  const describedBy = useId();
  const keys = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") commit();
    else if (e.key === "Escape") setDraft(null);
  };
  return (
    <label className="results-range-cell">
      <span className="muted">{label}</span>
      <input
        className="field mono"
        inputMode="decimal"
        spellCheck={false}
        aria-label={label}
        disabled={disabled}
        /* **A disabled input tells assistive technology WHAT, not WHY.** The reason goes in the
           description so the field reads as "Min, Turn on Lock range ..." rather than "Min, dimmed". */
        aria-describedby={disabled ? describedBy : undefined}
        value={shown}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={keys}
        data-ws={`results-range-${label.toLowerCase()}`}
      />
      {disabled && (
        <span id={describedBy} className="sr-only">
          {why}
        </span>
      )}
    </label>
  );
}

/**
 * **How many bands the colour map is cut into** (claude-win2's results item 3).
 *
 * A select rather than a slider, because the useful counts are a handful and a slider invites values
 * nobody wants. **"Smooth" is first and is the default**: the dark palette is approved and banding is an
 * addition to it.
 */
function BandSection({ bands, disabled, onChange }: { bands: number; disabled: boolean; onChange: (bands: number) => void }) {
  return (
    <section className="mesh-section" aria-label="Bands" data-ws="results-bands">
      <div className="section-title">Bands</div>
      <Tooltip label="Cut the colour map into flat steps, so a value can be read off the model" side="top">
        <div className="select-wrap">
          <select
            className="field"
            aria-label="How many bands the colour map is cut into"
            value={String(bandCount(bands))}
            disabled={disabled}
            onChange={(e) => onChange(Number(e.target.value))}
            data-ws="results-bands-select"
          >
            {BAND_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n === SMOOTH ? "Smooth" : `${n} bands`}
              </option>
            ))}
          </select>
          <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
        </div>
      </Tooltip>
    </section>
  );
}

/**
 * **The colour map** (R1): which table colours the walls, the slices, the iso-surface and the legend, and
 * whether it is read from the other end. One word on the control; the sentence is the tooltip.
 *
 * A native select like the field and band ones, so the menu looks and behaves the same; a strip under it
 * shows the map as it will be drawn (reversed too), because a name does not say what a map looks like.
 */
function MapSection({ map, reverse, disabled, onMap, onReverse }: { map: ColormapId; reverse: boolean; disabled: boolean; onMap: (map: ColormapId) => void; onReverse: (reverse: boolean) => void }) {
  return (
    <section className="mesh-section" aria-label="Map" data-ws="results-map">
      <div className="section-title">Map</div>
      <Tooltip label="The colours the field is drawn with, on the walls, cuts and legend" side="top">
        <div className="select-wrap">
          <select className="field" aria-label="Colour map" value={map} disabled={disabled} onChange={(e) => onMap(e.target.value as ColormapId)} data-ws="results-map-select">
            {COLORMAP_IDS.map((id) => (
              <option key={id} value={id}>
                {COLORMAPS[id].label}
              </option>
            ))}
          </select>
          <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
        </div>
      </Tooltip>
      <div
        aria-hidden="true"
        data-ws="results-map-strip"
        style={{ height: 8, borderRadius: 4, marginTop: 6, background: gradientCssFor(map, reverse, "to right"), border: "1px solid var(--panel-border)" }}
      />
      <Tooltip label="Read the map from the other end: high values take the low colour" side="top">
        <div>
          <Switch checked={reverse} label="Reverse" disabled={disabled} onChange={onReverse} />
        </div>
      </Tooltip>
    </section>
  );
}

/**
 * **Which parts of the page a tree row shows** (the Details pane lists only the selected row's settings).
 *
 * The keys are the `section` a row opens (`openTargetOf`); the values are the parts of this panel that row
 * owns. The Results state line is always shown, so it is not listed. Without a section the whole page
 * shows, as it does on the empty Results stage.
 */
const SECTION_PARTS: Record<string, readonly string[]> = {
  "results-field": ["field", "map", "bands", "range", "fade"],
  "results-slice": ["slice", "slices", "iso"],
  "results-residuals": ["residuals"],
  "results-monitors": ["monitors"],
  "results-arrows": ["arrows"],
  "results-streamlines": ["streamlines"],
  "results-probe": ["probe"],
  "results-report": ["report"],
  "results-lod": ["lod"],
};

export default function FieldsPanel({ section }: { section?: string } = {}) {
  const parts = section === undefined ? null : SECTION_PARTS[section] ?? null;
  const shown = (part: string) => parts === null || parts.includes(part);
  const stage = useStore((s) => s.stage);
  const connected = useStore((s) => s.connected);
  const results = useStore((s) => s.results);
  const options = useFieldOptions();
  const hasRun = useHasFinishedRun();
  const { view, data, status } = results;
  const range = effectiveRange(view, data);
  const locked = isLocked(view);
  const busy = status === "loading";
  const colouring = view.field !== NO_FIELD && data !== null;
  const units = fieldUnits(data, view.field);
  const percentiles = hasPercentiles(data, view.field);
  const rangeValue: RangeChoice = locked ? rangeModeOf(view) : legendOf(view).fullRange && percentiles ? "full" : "auto";
  const hasSlice = data?.slice === true;
  const tree = useStore((s) => s.tree);
  const settings = useMemo(() => findSolverSettings(tree), [tree]);
  const plane = useMemo(() => slicePlaneText(settings), [settings]);

  // The run the plots and the table are about: the one the fields were read from, else the newest finished.
  const runs = useStore((s) => s.run.runs);
  const series = useStore((s) => s.run.residuals);
  const seriesRun = useStore((s) => s.run.residualRun);
  const pushToast = useStore((s) => s.pushToast);
  const shownRun = useMemo(() => runForMonitors(runs, results.run), [runs, results.run]);
  const newest = runs[0] ?? null;
  const plotRun = newest !== null && series !== null && seriesRun === newest.id ? newest : null;
  const monitors = useMemo(() => monitorsOf(shownRun), [shownRun]);
  const pUnits = fieldUnits(data, "p");
  const rows = useMemo(() => monitorRows(monitors), [monitors]);
  const drop = useMemo(() => pressureDrop(monitors, pUnits), [monitors, pUnits]);
  const copyTable = async () => {
    const ok = await copyTableText(tableText(monitors, pUnits));
    pushToast(ok ? "ok" : "error", ok ? "Copied the monitors table." : "The clipboard is not available in this window.");
  };
  const exportCsv = async () => {
    const name = await saveTextFile(csvFileName(shownRun), csvText(monitors, pUnits));
    if (name !== null) pushToast("ok", `Exported ${name}`);
  };

  // In the Results stage this is the tab in front, wherever the workspace put it (the slot's other tabs stay).
  useEffect(() => {
    if (stage !== "Results") return;
    const workspace = useWorkspace.getState();
    const place = placeOf(workspace.document.layout, "results");
    if (place === "left" || place === "right" || place === "bottom") workspace.setActiveTab(place, "results");
  }, [stage]);

  return (
    <div className="mesh-panel" data-ws="results-panel" data-status={status}>
      <section className="mesh-section" aria-label="Results">
        <div className="mesh-state" data-tone={status === "error" ? "bad" : status === "loading" ? "busy" : data !== null ? "ok" : "neutral"} aria-live="polite" data-ws="results-state">
          {status === "loading" ? <LoaderCircle size={14} aria-hidden="true" className="mesh-spin" /> : status === "error" ? <CircleAlert size={14} aria-hidden="true" /> : null}
          <span data-ws="results-state-text">{hasRun || data !== null || status === "error" ? loadedLine(results) : "Run a simulation to see fields"}</span>
        </div>
        <div className="mesh-actions">
          <Button
            variant={data === null ? "primary" : "default"}
            aria-label={data === null ? "Show results" : "Update the results"}
            aria-disabled={!connected || !hasRun || busy || undefined}
            title={!hasRun ? "There is no finished run yet" : data === null ? "Read the results of the newest finished run and show them" : "Ask the server for the run's results again"}
            onClick={() => connected && hasRun && !busy && showResults()}
            data-ws="results-show"
          >
            {data === null ? (
              "Show results"
            ) : (
              <>
                <RefreshCw size={13} aria-hidden="true" />
                Update
              </>
            )}
          </Button>
        </div>
      </section>

      {shown("field") && (
        <section className="mesh-section" aria-label="Field" data-ws="results-field">
          <div className="section-title">Field</div>
          <div className="select-wrap">
            <select
              className="field"
              aria-label="Field to colour the mesh with"
              value={view.field}
              disabled={data === null || options.length === 0}
              onChange={(e) => update((live) => chooseField(live, e.target.value))}
              data-ws="results-select"
            >
              <option value={NO_FIELD}>None</option>
              {options.map((o) => (
                <option key={o.name} value={o.name}>
                  {o.label}
                </option>
              ))}
            </select>
            <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
          </div>
          {data === null && (
            <Note className="mesh-hint" tip={hasRun ? "Press Show results to read the fields of the newest finished run." : "Run a simulation to see fields."}>{hasRun ? "Press Show results" : "No run yet"}</Note>
          )}
          {colouring && range !== null && (
            <p className="muted mesh-hint" data-ws="results-title">
              {fieldTitle(data, view.field)}: {formatTick(range.min)} to {formatTick(range.max)}
            </p>
          )}
        </section>
      )}

      {shown("map") && (
        <MapSection
          map={legendOf(view).map}
          reverse={legendOf(view).reverse}
          disabled={!colouring}
          onMap={(id) => update((live) => setView(live, setMap(live.view, id)))}
          onReverse={(next) => update((live) => setView(live, setReverse(live.view, next)))}
        />
      )}

      {shown("bands") && <BandSection disabled={!colouring} bands={view.bands} onChange={(n) => update((live) => setView(live, setBandCount(live.view, n)))} />}

      {shown("probe") && <ProbePanel />}

      {shown("streamlines") && <StreamlineSection />}

      {shown("arrows") && <ArrowsSection />}

      {shown("lod") && <LODSection />}

      {shown("report") && <ReportSection />}

      {shown("report") && (
        <section className="mesh-section" aria-label="Image" data-ws="results-image">
          <div className="section-title">Image</div>
          <Tooltip label={data === null ? "Show the results first" : "Save the view as a PNG at print resolution, with the legend and scale bar"} side="top">
            <span>
              <Button aria-label="Export image" onClick={openExportDialog} disabled={data === null} data-ws="export-image-open">
                <ImageDown size={13} aria-hidden="true" /> Export image…
              </Button>
            </span>
          </Tooltip>
        </section>
      )}

      {shown("fade") && <FadeSection hasResults={colouring} />}

      {shown("range") && (
        <section className="mesh-section" aria-label="Range" data-ws="results-range">
          <div className="section-title">Range</div>
          {/*
            **One menu replaces the old "Lock range" switch** (R1): auto follows the run (its 1-99 percentile
            range when the run reports one), locked freezes the range as it is, manual is typed ends. "Full
            range" is offered only when the run has percentiles, because without them it is the same as auto.
          */}
          <Tooltip label="Auto follows the results; Locked and Manual fix the range" side="top">
            <div className="select-wrap">
              <select
                className="field"
                aria-label="Colour range"
                value={rangeValue}
                disabled={!colouring}
                onChange={(e) => update((live) => setView(live, chooseRange(live.view, live.data, e.target.value as RangeChoice)))}
                data-ws="results-range-select"
              >
                <option value="auto">{RANGE_MODE_WORDS.auto}</option>
                {percentiles && <option value="full">Full range</option>}
                <option value="locked">{RANGE_MODE_WORDS.locked}</option>
                <option value="manual">{RANGE_MODE_WORDS.manual}</option>
              </select>
              <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
            </div>
          </Tooltip>
          <div className="results-range">
            <RangeInput label="Min" value={range === null ? null : range.min} disabled={!locked || !colouring} why={!colouring ? "Turn on a field to colour the mesh with first." : "Pick Locked or Manual to type the ends"}
                onCommit={(v) => update((live) => setView(live, setManualEnd(live.view, live.data, "min", v)))} />
            <RangeInput label="Max" value={range === null ? null : range.max} disabled={!locked || !colouring} why={!colouring ? "Turn on a field to colour the mesh with first." : "Pick Locked or Manual to type the ends"}
                onCommit={(v) => update((live) => setView(live, setManualEnd(live.view, live.data, "max", v)))} />
            {units !== "" && colouring && <span className="muted results-units">{units}</span>}
          </div>
        </section>
      )}

      {shown("slices") && <SlicesSection />}

      {shown("iso") && <IsoSection />}

      {shown("residuals") && (
        <section className="mesh-section" aria-label="Residuals" data-ws="results-residuals">
          <div className="section-title">Residuals</div>
          {/* The chart is drawn only while the Results stage is in front: every tab of the slot is mounted, and the Solve panel and the drawer draw the same series. */}
          {stage === "Results" && plotRun !== null && series !== null && series.iterations.length > 0 ? (
            <>
              <ResidualPlot series={series} target={residualTarget(settings)} />
              <dl className="mesh-counts" aria-label="Final residuals" data-ws="results-final">
                <div>
                  <dt className="muted">Iterations</dt>
                  <dd>{plotRun.iterations > 0 ? plotRun.iterations : series.iterations[series.iterations.length - 1]}</dd>
                </div>
                {plotRun.last.map((f) => (
                  <div key={f.name}>
                    <dt className="muted">{f.name}</dt>
                    <dd>{formatResidual(f.value)}</dd>
                  </div>
                ))}
              </dl>
            </>
          ) : (
            <Note className="mesh-hint" tip="The residuals of the newest run appear here, with the convergence target as a dashed line.">No run yet</Note>
          )}
        </section>
      )}

      {shown("monitors") && (
        <section className="mesh-section" aria-label="Monitors" data-ws="results-monitors">
          <div className="section-title">Monitors</div>
          {rows.length > 0 ? (
            <table className="mesh-quality" aria-label="Monitors of the run" data-ws="monitors-table">
              <thead>
                <tr>
                  <th scope="col" className="muted">
                    Patch
                  </th>
                  <th scope="col" className="muted">
                    Flow ({FLOW_UNITS})
                  </th>
                  <th scope="col" className="muted">
                    Mean {pressureLabel(pUnits)} ({pUnits})
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.patch}>
                    <th scope="row">{r.patch}</th>
                    <td className="mesh-q-value">{r.flowRate}</td>
                    <td className="mesh-q-value">{r.pMean}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Note className="mesh-hint" tip={shownRun === null ? "The flow rate and mean pressure of each patch appear here once a run has finished." : "This run reported no monitors."}>{shownRun === null ? "No run yet" : "No monitors"}</Note>
          )}
          {drop !== null && (
            <p className="monitors-drop" data-ws="monitors-drop">
              <span className="muted">
                Pressure drop, {drop.inlet} to {drop.outlet}
              </span>
              <span className="mesh-q-value">{`${dropSymbol(drop.units)} ${drop.text}`}</span>
            </p>
          )}
          <div className="mesh-actions">
            <Button disabled={rows.length === 0} aria-label="Copy the monitors table" title="Copy the table to the clipboard, tab separated" onClick={() => void copyTable()} data-ws="monitors-copy">
              <ClipboardCopy size={13} aria-hidden="true" />
              Copy table
            </Button>
            <Button disabled={rows.length === 0} aria-label="Export the monitors as CSV" title="Save the table as a CSV file" onClick={() => void exportCsv()} data-ws="monitors-csv">
              <Download size={13} aria-hidden="true" />
              Export CSV
            </Button>
          </div>
        </section>
      )}

      {shown("slice") && (
        <section className="mesh-section" aria-label="Slice" data-ws="results-slice">
          <div className="section-title">Slice</div>
          <Tooltip label={!hasSlice && data !== null ? "This run has no slice" : "Draw the plane through the middle of the case and colour it by the field"} side="top">
            <div>
              <Switch checked={view.slice && hasSlice} label="Mid-plane slice" onChange={(next) => hasSlice && update((live) => setSlice(live, next))} />
            </div>
          </Tooltip>
          <Tooltip label="Draw the mesh's cell edges on the slice" side="top">
            <div data-ws="results-slice-edges">
              <Switch checked={view.sliceEdges === true} label="Cell edges" onChange={(next) => update((live) => setSliceEdges(live, next))} />
            </div>
          </Tooltip>
          {plane !== null ? (
            <dl className="mesh-counts" aria-label="Slice plane" data-ws="results-plane">
              {plane.origin !== "" && (
                <div>
                  <dt className="muted">Origin</dt>
                  <dd>{plane.origin}</dd>
                </div>
              )}
              {plane.normal !== "" && (
                <div>
                  <dt className="muted">Normal</dt>
                  <dd>{plane.normal}</dd>
                </div>
              )}
            </dl>
          ) : (
            <Note className="mesh-hint" tip="The plane is set by the solver settings (slice origin and normal); it is cut when the run ends.">Cut when the run ends</Note>
          )}
        </section>
      )}
    </div>
  );
}
