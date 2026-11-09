/**
 * The Analyse block of the Results card: Fluent's Reports and Plots on a finished run, in five tabs (Report, XY
 * plot, Histogram, History, Functions). Every number comes from an API command (`results.*`, `run.monitor*`); the
 * arrangement and the chart geometry are `analyseModel.ts` (pure, tested); this file asks and draws.
 *
 * The surfaces are the run's boundary patches and the slice on screen: a report on "slice" is a report on the cut
 * the user is looking at (its plane is sent with the ask). A saved function can colour the model: its range over
 * every surface is asked with `results.evaluate`, added to the run's field list, and the field route serves its
 * values like any field's.
 *
 * The house text rule: a few words on the control, the sentence in the tooltip.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronsUpDown } from "lucide-react";
import { host } from "../app/host";
import { Button } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { chooseField, NO_FIELD } from "../panels/fieldsModel";
import { legendOf } from "../panels/legendModel";
import { curveColour } from "../panels/lineModel";
import { visibleSlices } from "../panels/sliceModel";
import { planeQuery } from "../state/slicePlane";
import { useStore } from "../state/store";
import { download } from "../viewport/exportImage";
import { Chips, Note } from "./controls";
import {
  ANALYSE_TABS,
  MAIN_QUANTITIES,
  XY_AXES,
  defaultSurfaces,
  fieldChoices,
  formatValue,
  functionNameProblem,
  histogramChart,
  monitorLabel,
  monitorSeries,
  reportCsv,
  reportTable,
  settled,
  toggleSurface,
  xyChart,
  type AnalyseTab,
  type FieldFunction,
  type HistogramReply,
  type IntegralsReply,
  type SurfacePlotReply,
  type SurfacesReply,
} from "./analyseModel";
import { historyAxis } from "./timeModel";
import "./linePlot.css";
import "./analyse.css";

const WIDTH = 300;
const HEIGHT = 150;

/** Series colours after the first (which follows the colour map's curve colour): theme tokens. */
const SERIES = ["var(--warn)", "var(--danger)", "var(--text-muted)", "var(--accent)"];

type Ask = <T>(method: string, params: Record<string, unknown>) => Promise<T>;

function useAsk(): Ask | null {
  const client = useStore((s) => s.client);
  return useMemo(() => (client === null ? null : <T,>(method: string, params: Record<string, unknown>) => client.call<T>(method, params)), [client]);
}

/** The plane of the first slice on screen, as the results commands take it; undefined for the run's own. */
function useSlicePlane(): string | undefined {
  const slices = useStore((s) => s.slices);
  return useMemo(() => {
    // A slice of the list is a plane of its own; with none, "slice" is the run's mid-plane cut.
    const shown = visibleSlices(slices)[0];
    return shown === undefined ? undefined : (planeQuery(shown.plane) ?? undefined);
  }, [slices]);
}

function message(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "object" && error !== null && "message" in error) return String((error as { message: unknown }).message);
  return String(error);
}

async function saveText(text: string, name: string, title: string): Promise<void> {
  if (host.canSaveFiles) {
    const path = await host.pickSaveFile(title, name, "CSV files (*.csv);;All files (*)");
    if (path) await host.writeTextFile(path, text);
    return;
  }
  download(new Blob([text], { type: "text/csv;charset=utf-8" }), name);
}

function Select({ label, value, options, onChange, ws }: { label: string; value: string; options: readonly { value: string; label: string }[]; onChange: (v: string) => void; ws: string }) {
  return (
    <div className="select-wrap analyse-select">
      <select className="field" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} data-ws={ws}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronsUpDown size={12} className="select-chevron" aria-hidden="true" />
    </div>
  );
}

function Axes({ chart, xLabel }: { chart: { x0: number; x1: number; y0: number; y1: number; xTicks: { value: number; at: number; label: string }[]; yTicks: { value: number; at: number; label: string }[] }; xLabel: string }) {
  return (
    <>
      {chart.yTicks.map((t) => (
        <g key={`y${t.value}`}>
          <line className="line-grid" x1={chart.x0} x2={chart.x1} y1={t.at} y2={t.at} />
          <text className="line-tick" x={chart.x0 - 4} y={t.at + 3.5} textAnchor="end">
            {t.label}
          </text>
        </g>
      ))}
      <line className="line-axis" x1={chart.x0} x2={chart.x1} y1={chart.y1} y2={chart.y1} />
      {chart.xTicks.map((t) => (
        <text key={`x${t.value}`} className="line-tick" x={t.at} y={chart.y1 + 12} textAnchor="middle">
          {t.label}
        </text>
      ))}
      <text className="line-tick" x={chart.x1} y={HEIGHT - 3} textAnchor="end">
        {xLabel}
      </text>
    </>
  );
}

function Legend({ names, colours }: { names: readonly string[]; colours: readonly string[] }) {
  if (names.length < 2) return null;
  return (
    <div className="analyse-legend">
      {names.map((n, i) => (
        <span key={n}>
          <i style={{ background: colours[i] }} />
          {n}
        </span>
      ))}
    </div>
  );
}

export default function AnalyseBlock() {
  const ask = useAsk();
  const run = useStore((s) => s.results.data?.run ?? null);
  const shownField = useStore((s) => s.results.view.field);
  // Two primitives, not `legendOf(...)`: a selector that builds an object re-renders forever.
  const map = useStore((s) => legendOf(s.results.view).map);
  const reverse = useStore((s) => legendOf(s.results.view).reverse);
  const plane = useSlicePlane();
  const [tab, setTab] = useState<AnalyseTab | null>(null);
  const [info, setInfo] = useState<SurfacesReply | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [field, setField] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<IntegralsReply | null>(null);
  const [allRows, setAllRows] = useState(false);
  const [plot, setPlot] = useState<SurfacePlotReply | null>(null);
  const [axis, setAxis] = useState("radius");
  const [hist, setHist] = useState<HistogramReply | null>(null);
  const [bins, setBins] = useState("20");
  const [history, setHistory] = useState<{ name: string; label: string; units: string; x: number[]; y: number[] }[] | null>(null);
  const [historyPick, setHistoryPick] = useState(0);
  const [fnName, setFnName] = useState("");
  const [fnText, setFnText] = useState("");
  const [fnNote, setFnNote] = useState("");

  const colours = useMemo(() => [curveColour(map, reverse), ...SERIES], [map, reverse]);

  const loadInfo = useCallback(async () => {
    if (ask === null || run === null) return;
    try {
      const reply = await ask<SurfacesReply>("results.surfaces", { run });
      setInfo(reply);
      setChosen((before) => (before.length > 0 && before.every((id) => reply.surfaces.some((s) => s.id === id)) ? before : defaultSurfaces(reply.surfaces)));
      setError("");
    } catch (e) {
      setError(message(e));
    }
  }, [ask, run]);

  // The surfaces and fields are asked when the block opens, and again for a new run.
  useEffect(() => {
    if (tab !== null) void loadInfo();
  }, [tab, loadInfo]);

  // The field follows the one colouring the model until the user picks another here.
  useEffect(() => {
    if (field === "" && shownField !== NO_FIELD) setField(shownField);
  }, [field, shownField]);

  const fields = useMemo(() => (info === null ? [] : fieldChoices(info.fields, info.functions)), [info]);
  const fieldValue = fields.some((f) => f.value === field) ? field : (fields[0]?.value ?? "");

  const common = useCallback(
    (surfaces: string[]) => ({ run, surfaces, field: fieldValue, ...(plane !== undefined && surfaces.includes("slice") ? { plane } : {}) }),
    [run, fieldValue, plane],
  );

  const go = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const compute = () => {
    if (ask === null || tab === null) return;
    if (tab === "report") void go(async () => setReport(await ask<IntegralsReply>("results.surface_integrals", common(chosen))));
    if (tab === "xy") void go(async () => setPlot(await ask<SurfacePlotReply>("results.surface_plot", { ...common(chosen), axis })));
    if (tab === "histogram") void go(async () => setHist(await ask<HistogramReply>("results.histogram", { ...common(chosen), bins: Number(bins) })));
    if (tab === "history")
      void go(async () => {
        const listed = await ask<{ monitors: { name: string }[] }>("run.monitors", { run });
        const tables = await Promise.all(
          listed.monitors.map(async (m) => {
            const table = await ask<{ columns: string[]; values: Record<string, number[]> }>("run.monitor", { run, name: m.name });
            const series = monitorSeries(table);
            return series === null ? null : { name: m.name, ...monitorLabel(m.name), x: series.x, y: series.y };
          }),
        );
        setHistory(tables.filter((t): t is NonNullable<typeof t> => t !== null));
      });
  };

  // A tab opens on its answer: the first press of a tab computes it with the defaults.
  useEffect(() => {
    if (info === null || tab === null || tab === "functions") return;
    const empty = (tab === "report" && report === null) || (tab === "xy" && plot === null) || (tab === "histogram" && hist === null) || (tab === "history" && history === null);
    if (empty && chosen.length > 0) compute();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info, tab]);

  const saveFunction = () => {
    if (ask === null) return;
    const problem = functionNameProblem(fnName);
    if (problem !== null) {
      setFnNote(problem);
      return;
    }
    void go(async () => {
      const surface = info?.surfaces[0]?.id ?? "slice";
      const checked = await ask<{ units: string; min: number | null; max: number | null }>("results.evaluate", { run, surface, field: fnText });
      await ask("results.function_save", { name: fnName.trim(), definition: fnText.trim() });
      setFnNote(`${fnName.trim()} saved: ${formatValue(checked.min)} to ${formatValue(checked.max)} ${checked.units} on ${info?.surfaces[0]?.name ?? "the slice"}`);
      setFnName("");
      setFnText("");
      await loadInfo();
    });
  };

  const removeFunction = (fn: FieldFunction) => {
    if (ask === null) return;
    void go(async () => {
      await ask("results.function_remove", { function: fn.id ?? fn.name });
      await loadInfo();
    });
  };

  /** Colour the model by a function: its range over every surface, added to the run's fields, then chosen. */
  const colourBy = (fn: FieldFunction) => {
    if (ask === null || info === null) return;
    void go(async () => {
      let min = Infinity;
      let max = -Infinity;
      let units = "";
      for (const s of info.surfaces) {
        const r = await ask<{ units: string; min: number | null; max: number | null }>("results.evaluate", { run, surface: s.id, field: fn.name, ...(s.kind === "slice" && plane !== undefined ? { plane } : {}) });
        units = r.units;
        if (r.min !== null) min = Math.min(min, r.min);
        if (r.max !== null) max = Math.max(max, r.max);
      }
      if (!(max >= min)) throw new Error(`${fn.name} has no finite value on the model`);
      useStore.setState((st) => {
        const data = st.results.data;
        if (data === null) return st;
        const fieldsNext = [...data.fields.filter((f) => f.name !== fn.name), { name: fn.name, units, min, max }];
        const live = { ...st.results, data: { ...data, fields: fieldsNext } };
        return { results: chooseField(live, fn.name) };
      });
    });
  };

  if (run === null) return null;

  const surfaceChips = (multi: boolean) =>
    info === null ? null : (
      <div className="guided-chips wide analyse-surfaces" role="group" aria-label="Surfaces">
        {info.surfaces.map((s) => (
          <button
            key={s.id}
            type="button"
            className="guided-chip no-drag"
            aria-pressed={chosen.includes(s.id)}
            title={`${s.name}: ${formatValue(s.area)} m2, ${s.triangles} triangles`}
            data-choice={s.id}
            onClick={() => setChosen(multi ? toggleSurface(chosen, s.id) : [s.id])}
          >
            <span>{s.kind === "slice" ? "Slice" : s.name}</span>
          </button>
        ))}
      </div>
    );

  const fieldRow = (
    <div className="looks-row">
      <span className="looks-label">Field</span>
      <Select label="Field" value={fieldValue} options={fields} onChange={setField} ws="analyse-field" />
    </div>
  );

  const computeButton = (
    <div className="guided-actions">
      <Tooltip label="Compute again with these surfaces and this field" side="top">
        <Button onClick={compute} aria-disabled={busy || chosen.length === 0 || undefined} data-ws="analyse-compute">
          {busy ? "Computing…" : "Compute"}
        </Button>
      </Tooltip>
    </div>
  );

  return (
    <div className="analyse" data-ws="guided-analyse">
      <div className="analyse-head muted">Analyse</div>
      <div className="looks-row">
        <div className="guided-chips wide analyse-tabs" role="group" aria-label="Analyse">
          {ANALYSE_TABS.map((t) => (
            <Tooltip key={t.id} label={t.tip} side="top">
              <button type="button" className="guided-chip no-drag" aria-pressed={tab === t.id} data-choice={t.id} data-ws={`analyse-tab-${t.id}`} onClick={() => setTab(tab === t.id ? null : t.id)}>
                <span>{t.label}</span>
              </button>
            </Tooltip>
          ))}
        </div>
      </div>

      {tab !== null && info === null && error === "" && <Note>Reading the surfaces…</Note>}

      {tab === "report" && info !== null && (
        <>
          {surfaceChips(true)}
          {fieldRow}
          {computeButton}
          {report !== null && (() => {
            const table = reportTable(report, allRows ? null : MAIN_QUANTITIES);
            return (
              <div className="analyse-table-wrap" data-ws="analyse-report">
                <table className="analyse-table">
                  <thead>
                    <tr>
                      <th>{report.field === "" ? "" : `${report.field}${report.field_units ? ` (${report.field_units})` : ""}`}</th>
                      {table.columns.map((c) => (
                        <th key={c}>{c === "slice" ? "Slice" : c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {table.rows.map((r) => (
                      <tr key={r.key}>
                        <th title={r.units === "" ? r.label : `${r.label} (${r.units})`}>
                          {r.short}
                          {r.units !== "" && <span className="analyse-unit muted">{r.units}</span>}
                        </th>
                        {r.cells.map((c, i) => (
                          <td key={i}>{c}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })()}
          {report !== null && (
            <div className="guided-actions">
              <Tooltip label={allRows ? "Only the main rows" : "Every quantity: facet and vertex values, the integral, the mass-weighted uniformity"} side="top">
                <Button onClick={() => setAllRows(!allRows)} data-ws="analyse-all-rows">
                  {allRows ? "Fewer" : "All rows"}
                </Button>
              </Tooltip>
              <Tooltip label="Save the whole report, at full precision, as a CSV file" side="top">
                <Button onClick={() => void saveText(reportCsv(report), `${run}-report-${report.field || "flow"}.csv`, "Save the report")} data-ws="analyse-report-csv">
                  CSV
                </Button>
              </Tooltip>
              {report.density !== null && <Note tip="The fluid material's density at its reference temperature, used for mass flow and mass weighting">{`ρ ${formatValue(report.density)} kg/m3`}</Note>}
            </div>
          )}
        </>
      )}

      {tab === "xy" && info !== null && (
        <>
          {surfaceChips(true)}
          {fieldRow}
          <Tooltip label="Radius is the distance from each surface's own axis: its centre, along its normal" side="top" describe={false}>
            <div className="looks-row">
              <span className="looks-label">Against</span>
              <Chips<string> label="Against" value={axis} choices={XY_AXES} onChange={setAxis} wide />
            </div>
          </Tooltip>
          {computeButton}
          {plot !== null && (() => {
            const chart = xyChart(plot.series, WIDTH, HEIGHT, plot.x_units, "dots");
            const title = `${plot.field}${plot.units ? ` (${plot.units})` : ""} against ${plot.axis === "radius" ? "radius" : plot.axis}`;
            return (
              <div className="line-plot" data-ws="analyse-xy">
                <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={title}>
                  <Axes chart={chart} xLabel={`${plot.axis === "radius" ? "r" : plot.axis} (${chart.xUnit})`} />
                  {chart.paths.map((d, i) => (
                    <path key={i} className="analyse-dots" d={d} style={{ stroke: colours[i % colours.length] }} />
                  ))}
                </svg>
                <Legend names={plot.series.map((s) => (s.surface === "slice" ? "Slice" : s.surface))} colours={colours} />
                <div className="line-title muted">{title}</div>
              </div>
            );
          })()}
        </>
      )}

      {tab === "histogram" && info !== null && (
        <>
          {surfaceChips(true)}
          {fieldRow}
          <div className="looks-row">
            <span className="looks-label">Bins</span>
            <Chips<string> label="Bins" value={bins} choices={["10", "20", "40"].map((b) => ({ value: b, label: b }))} onChange={setBins} wide />
          </div>
          {computeButton}
          {hist !== null && (() => {
            const chart = histogramChart(hist, WIDTH, HEIGHT);
            const title = `${hist.field}${hist.units ? ` (${hist.units})` : ""}: share of ${formatValue(hist.total)} m2`;
            return (
              <div className="line-plot" data-ws="analyse-histogram">
                <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={title}>
                  <Axes chart={chart} xLabel={hist.units || hist.field} />
                  {chart.bars.map((b, i) => (
                    <rect key={i} className="analyse-bar" x={b.x} y={b.y} width={b.w} height={b.h} style={{ fill: colours[0] }}>
                      <title>{b.label}</title>
                    </rect>
                  ))}
                  <text className="line-tick" x={chart.x0 + 2} y={chart.y0 + 8}>
                    % area
                  </text>
                </svg>
                <div className="line-title muted">{title}</div>
              </div>
            );
          })()}
        </>
      )}

      {tab === "history" && (
        <>
          {history === null && busy && <Note>Reading the solver's reports…</Note>}
          {history !== null && history.length === 0 && <Note tip="The run wrote no report files">No reports</Note>}
          {history !== null && history.length > 0 && (() => {
            const pick = Math.min(historyPick, history.length - 1);
            const h = history[pick]!;
            const chart = xyChart([{ x: h.x, y: h.y }], WIDTH, HEIGHT, "", "line");
            const end = settled(h.y);
            return (
              <>
                <div className="looks-row">
                  <span className="looks-label">Report</span>
                  <Select label="Report" value={String(pick)} options={history.map((m, i) => ({ value: String(i), label: m.label }))} onChange={(v) => setHistoryPick(Number(v))} ws="analyse-history-pick" />
                </div>
                <div className="line-plot" data-ws="analyse-history">
                  <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={h.label}>
                    <Axes chart={chart} xLabel={historyAxis(h.x)} />
                    <path className="line-curve" d={chart.paths[0]} style={{ stroke: colours[0] }} />
                  </svg>
                  <div className="line-title muted">
                    {h.label}
                    {h.units !== "" ? ` (${h.units})` : ""}
                    {end !== null && `: ${formatValue(end.last)}${end.change === null ? "" : `, ${end.change < 0.1 ? "settled" : `moved ${end.change.toFixed(1)} % over the last tenth`}`}`}
                  </div>
                </div>
              </>
            );
          })()}
        </>
      )}

      {tab === "functions" && info !== null && (
        <>
          {info.functions.length === 0 && <Note tip="A function is a name for an expression, e.g. p_total = p + 0.5 * U_mag^2. It reports, plots and colours like a field.">No functions yet</Note>}
          {info.functions.map((fn) => (
            <div key={fn.name} className="analyse-function">
              <span className="mono" title={fn.definition}>
                {fn.name} = {fn.definition}
              </span>
              <Tooltip label="Colour the model by this function" side="top">
                <Button onClick={() => colourBy(fn)} data-ws={`analyse-colour-${fn.name}`}>
                  Colour
                </Button>
              </Tooltip>
              <Tooltip label="Remove this function (Undo brings it back)" side="top">
                <Button onClick={() => removeFunction(fn)} data-ws={`analyse-remove-${fn.name}`}>
                  Remove
                </Button>
              </Tooltip>
            </div>
          ))}
          <div className="analyse-new-function">
            <input className="field mono" aria-label="Function name" placeholder="name" value={fnName} spellCheck={false} onChange={(e) => setFnName(e.target.value)} data-ws="analyse-fn-name" />
            <span>=</span>
            <input className="field mono" aria-label="Function definition" placeholder="p + 0.5 * U_mag^2" value={fnText} spellCheck={false} onChange={(e) => setFnText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && saveFunction()} data-ws="analyse-fn-text" />
          </div>
          <div className="guided-actions">
            <Tooltip label="Check the expression on the model, then save it with the project" side="top">
              <Button onClick={saveFunction} aria-disabled={busy || fnText.trim() === "" || undefined} data-ws="analyse-fn-save">
                Save
              </Button>
            </Tooltip>
          </div>
          <Note tip="Names: the run's fields (U_mag, p...), components U_x U_y U_z, coordinates x y z, rho, pi. Functions: sqrt abs min max clamp sin cos tan exp log log10. Units in brackets: 2 [m/s].">
            {fnNote !== "" ? fnNote : "Fields, U_x, x, rho; units in [ ]"}
          </Note>
        </>
      )}

      {error !== "" && (
        <p role="alert" className="guided-error" data-ws="analyse-error">
          {error}
        </p>
      )}
    </div>
  );
}
