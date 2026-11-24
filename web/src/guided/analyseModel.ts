/**
 * The Analyse block of the Results card, as pure functions: Fluent's Reports and Plots on a run's surfaces.
 *
 * * **Report** — `results.surface_integrals`: one column per surface and a Net column, one row per quantity
 *   (area, area- and mass-weighted average, flow rate, mass flow, min/max, standard deviation, uniformity),
 *   with units, as text a person reads and as CSV.
 * * **XY plot** — `results.surface_plot`: every point's value against x, y, z or the radius from an axis, one
 *   series per surface (a radial profile across an outlet is the pipe's velocity profile).
 * * **Histogram** — `results.histogram`: how much of the surface (by area) is at each value.
 * * **History** — `run.monitors` and `run.monitor`: the solver's own report files (flow rate and mean pressure
 *   on each inlet and outlet), plotted against the iteration.
 * * **Functions** — `results.function_save`/`_list`/`_remove`: named expressions that colour, report and plot
 *   like any field.
 *
 * Nothing here computes physics: every number is the server's, and this only arranges and draws them.
 *
 * Import rule: no React, no store, no DOM. This loads in Node.
 */
import { niceTicks } from "../state/residualChart";
import { tickText } from "../panels/lineModel";
import { addAxisSlice, updateSlice, visibleSlices, type SliceList } from "../panels/sliceModel";
import type { SectionAxis } from "../state/sectionState";
import { axisOfPlane } from "../state/slicePlane";

export type AnalyseTab = "report" | "xy" | "histogram" | "history" | "functions";

export const ANALYSE_TABS: readonly { id: AnalyseTab; label: string; tip: string }[] = [
  { id: "report", label: "Report", tip: "Surface integrals: area, averages, flow rate, min/max and uniformity on any boundary or the slice" },
  { id: "xy", label: "XY plot", tip: "The field at every point of a surface against its position: a profile across a pipe or along a wall" },
  { id: "histogram", label: "Histogram", tip: "How much of the surface, by area, is at each value" },
  { id: "history", label: "History", tip: "The solver's own reports (flow rate, mean pressure) against the iteration" },
  { id: "functions", label: "Functions", tip: "Your own fields, written as expressions over the run's fields" },
];

/** One surface a report can name (`results.surfaces`). */
export interface SurfaceInfo {
  id: string;
  name: string;
  kind: "patch" | "slice";
  points: number;
  triangles: number;
  area: number;
}

export interface SurfaceFieldInfo {
  name: string;
  units: string;
  components: number;
}

export interface FieldFunction {
  id?: string;
  name: string;
  definition: string;
}

export interface SurfacesReply {
  run: string;
  surfaces: SurfaceInfo[];
  fields: SurfaceFieldInfo[];
  functions: FieldFunction[];
  density: number | null;
}

export type Quantities = Record<string, number | null>;

export interface IntegralsReply {
  run: string;
  field: string;
  field_units: string;
  density: number | null;
  quantities: string[];
  units: Record<string, string>;
  rows: (Quantities & { surface: string; id: string })[];
  net: Quantities;
}

export interface HistogramReply {
  field: string;
  units: string;
  edges: number[];
  area: number[];
  fraction: number[];
  min: number;
  max: number;
  total: number;
}

export interface PlotSeries {
  surface: string;
  x: number[];
  y: number[];
  points: number;
}

export interface SurfacePlotReply {
  field: string;
  units: string;
  axis: string;
  x_units: string;
  series: PlotSeries[];
}

/** What each quantity is called in the report, Fluent's words where they are plain. */
export const QUANTITY_LABELS: Record<string, string> = {
  area: "Area",
  area_avg: "Area-weighted average",
  mass_avg: "Mass-weighted average",
  integral: "Integral",
  flow_rate: "Volume flow rate",
  mass_flow: "Mass flow rate",
  facet_min: "Facet minimum",
  facet_max: "Facet maximum",
  facet_avg: "Facet average",
  vertex_min: "Vertex minimum",
  vertex_max: "Vertex maximum",
  vertex_avg: "Vertex average",
  std_dev: "Standard deviation",
  uniformity_area: "Uniformity index (area)",
  uniformity_mass: "Uniformity index (mass)",
};

/** The row names in the card's narrow table; the full name is the row's tooltip. */
export const QUANTITY_SHORT: Record<string, string> = {
  area: "Area",
  area_avg: "Area avg",
  mass_avg: "Mass avg",
  integral: "Integral",
  flow_rate: "Flow rate",
  mass_flow: "Mass flow",
  facet_min: "Facet min",
  facet_max: "Facet max",
  facet_avg: "Facet avg",
  vertex_min: "Min",
  vertex_max: "Max",
  vertex_avg: "Vertex avg",
  std_dev: "Std dev",
  uniformity_area: "Uniformity",
  uniformity_mass: "Uniformity (m)",
};

/** The rows a report shows by default: the ones a person asks first. The rest are a toggle away. */
export const MAIN_QUANTITIES: readonly string[] = ["area", "area_avg", "mass_avg", "flow_rate", "mass_flow", "vertex_min", "vertex_max", "std_dev", "uniformity_area"];

/** A report value as a person reads it: five significant digits, exponent form for the very small or large. */
export function formatValue(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  if (magnitude >= 1e6 || magnitude < 1e-3) {
    const [mantissa, exponent] = value.toExponential(4).split("e");
    return `${mantissa!.replace(/\.?0+$/, "")}e${Number(exponent)}`;
  }
  return String(Number(value.toPrecision(5)));
}

export interface ReportRow {
  key: string;
  label: string;
  short: string;
  units: string;
  cells: string[];
}

export interface ReportTable {
  columns: string[];
  rows: ReportRow[];
}

/**
 * The report as a table: one column per surface, then Net when there is more than one, one row per quantity in
 * `wanted` (all of them when null). A row whose every cell is empty (a mass flow without a density) is left out:
 * a row of dashes says nothing.
 */
export function reportTable(reply: IntegralsReply, wanted: readonly string[] | null = MAIN_QUANTITIES): ReportTable {
  const net = reply.rows.length > 1;
  const columns = [...reply.rows.map((r) => r.surface), ...(net ? ["Net"] : [])];
  const keys = (wanted ?? reply.quantities).filter((k) => reply.quantities.includes(k));
  const rows: ReportRow[] = [];
  for (const key of keys) {
    const values = [...reply.rows.map((r) => r[key] ?? null), ...(net ? [reply.net[key] ?? null] : [])];
    if (values.every((v) => v === null)) continue;
    rows.push({ key, label: QUANTITY_LABELS[key] ?? key, short: QUANTITY_SHORT[key] ?? key, units: reply.units[key] ?? "", cells: values.map(formatValue) });
  }
  return { columns, rows };
}

/** The report as CSV: a header, then one line per quantity with its units, at full precision. */
export function reportCsv(reply: IntegralsReply): string {
  const net = reply.rows.length > 1;
  const quote = (text: string) => (/[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text);
  const header = ["quantity", "units", ...reply.rows.map((r) => r.surface), ...(net ? ["net"] : [])].map(quote).join(",");
  const lines = reply.quantities.map((key) => {
    const values = [...reply.rows.map((r) => r[key] ?? null), ...(net ? [reply.net[key] ?? null] : [])];
    return [quote(key), quote(reply.units[key] ?? ""), ...values.map((v) => (v === null || !Number.isFinite(v) ? "" : String(v)))].join(",");
  });
  return [`# field: ${reply.field || "(none)"}`, header, ...lines].join("\n") + "\n";
}

/** The surfaces a report starts with: the inlets and outlets when the model has them, else the first. */
export function defaultSurfaces(surfaces: readonly SurfaceInfo[]): string[] {
  const ends = surfaces.filter((s) => s.kind === "patch" && /inlet|outlet/i.test(s.name));
  if (ends.length > 0) return ends.map((s) => s.id);
  return surfaces.length > 0 ? [surfaces[0]!.id] : [];
}

/** A press of a surface chip: on when off, off when on, but never the last one off. */
export function toggleSurface(chosen: readonly string[], id: string): string[] {
  if (chosen.includes(id)) return chosen.length > 1 ? chosen.filter((s) => s !== id) : [...chosen];
  return [...chosen, id];
}

/** The fields a report can name: the run's scalars, each vector's magnitude and components, then saved functions. */
export function fieldChoices(fields: readonly SurfaceFieldInfo[], functions: readonly FieldFunction[]): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = [];
  const names = new Set(fields.map((f) => f.name));
  for (const f of fields) {
    const units = f.units === "" ? "" : ` (${f.units})`;
    if (f.components === 1) out.push({ value: f.name, label: `${f.name}${units}` });
    else {
      for (const axis of ["x", "y", "z"]) {
        if (!names.has(`${f.name}_${axis}`)) out.push({ value: `${f.name}_${axis}`, label: `${f.name}_${axis}${units}` });
      }
    }
  }
  for (const fn of functions) out.push({ value: fn.name, label: `${fn.name} = ${fn.definition}` });
  return out;
}

export interface Tick {
  value: number;
  at: number;
  label: string;
}

export interface XYChart {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  xTicks: Tick[];
  yTicks: Tick[];
  /** One SVG path per series, in the series' order. */
  paths: string[];
  /** The x-axis scale's unit after scaling metres (mm for a pipe). */
  xUnit: string;
  /**
   * Where a line leaves the y range it is drawn in (a history's start-up spike): one mark per excursion, at the
   * edge it crosses and the x of its furthest point, with that point's value. Empty without a `yRange`.
   */
  clipped: { x: number; y: number; edge: "top" | "bottom"; value: number }[];
}

/** What a chart is asked to show of its data: a fixed y range (the robust one), and the x it starts from. */
export interface ChartView {
  yRange?: readonly [number, number];
  xFrom?: number;
}

export const CHART_PAD = { left: 46, right: 8, top: 8, bottom: 26 };

/** Metres shown in mm below 1 m, as a person reads a pipe; anything else is shown as it comes. */
export function xScaleFor(units: string, maxAbs: number): { unit: string; scale: number } {
  if (units === "m" && maxAbs < 1) return { unit: "mm", scale: 1e-3 };
  return { unit: units, scale: 1 };
}

function span(values: readonly number[]): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (!Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return lo === Infinity ? null : [lo, hi];
}

function padded([lo, hi]: [number, number], share = 0.05): [number, number] {
  if (hi - lo < 1e-12 * Math.max(1, Math.abs(hi))) {
    const w = Math.abs(hi) > 0 ? Math.abs(hi) * 0.05 : 0.5;
    return [lo - w, hi + w];
  }
  const w = (hi - lo) * share;
  return [lo - w, hi + w];
}

/**
 * Several series on one pair of axes. `mode` "dots" draws each point as a short tick (a scatter of a surface's
 * points, where joining them would zig-zag); "line" joins them in order (a history against the iteration).
 */
export function xyChart(series: readonly { x: readonly number[]; y: readonly number[] }[], width: number, height: number, xUnits: string, mode: "dots" | "line", view: ChartView = {}): XYChart {
  const x0 = CHART_PAD.left;
  const y0 = CHART_PAD.top;
  const x1 = Math.max(x0 + 1, width - CHART_PAD.right);
  const y1 = Math.max(y0 + 1, height - CHART_PAD.bottom);
  const from = view.xFrom;
  // The points before `xFrom` are not on the chart at all: neither the x nor the y range counts them.
  const shown = from === undefined ? series : series.map((s) => keepFrom(s, from));
  const allX = shown.flatMap((s) => s.x as number[]);
  const allY = shown.flatMap((s) => s.y as number[]);
  const xs = span(allX);
  const ys = view.yRange !== undefined && view.yRange[1] >= view.yRange[0] ? ([view.yRange[0], view.yRange[1]] as [number, number]) : span(allY);
  const empty: XYChart = { x0, y0, x1, y1, xTicks: [], yTicks: [], paths: series.map(() => ""), xUnit: xUnits, clipped: [] };
  if (xs === null || ys === null) return empty;
  const { unit, scale } = xScaleFor(xUnits, Math.max(Math.abs(xs[0]), Math.abs(xs[1])));
  const [xlo, xhi] = padded([xs[0] / scale, xs[1] / scale], mode === "line" ? 0 : 0.02);
  const [ylo, yhi] = padded(ys);
  const sx = (v: number) => x0 + ((v / scale - xlo) / (xhi - xlo)) * (x1 - x0);
  const sy = (v: number) => y1 - ((v - ylo) / (yhi - ylo)) * (y1 - y0);
  const clipped: XYChart["clipped"] = [];
  const paths = shown.map((s) => {
    let d = "";
    let pen = false;
    // The furthest point of the excursion past an edge that is going on, if one is.
    let out: { x: number; value: number; edge: "top" | "bottom" } | null = null;
    const close = () => {
      if (out !== null) clipped.push({ x: sx(out.x), y: out.edge === "top" ? y0 : y1, edge: out.edge, value: out.value });
      out = null;
    };
    for (let i = 0; i < s.x.length; i++) {
      const x = s.x[i]!;
      const y = s.y[i]!;
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        pen = false;
        close();
        continue;
      }
      // A value past the range is drawn AT its edge (the line runs along it) and marked once per excursion, so the
      // ticks stay the range's and the excursion is never silently lost.
      const edge = y > yhi ? "top" : y < ylo ? "bottom" : null;
      if (edge === null) close();
      else if (out === null || out.edge !== edge) {
        close();
        out = { x, value: y, edge };
      } else if (Math.abs(y) > Math.abs(out.value)) out = { x, value: y, edge };
      const py = (edge === "top" ? y0 : edge === "bottom" ? y1 : sy(y)).toFixed(1);
      const px = sx(x).toFixed(1);
      if (mode === "dots") d += `M${px} ${py}h0.01`;
      else d += `${pen ? "L" : "M"}${px} ${py}`;
      pen = true;
    }
    close();
    return d;
  });
  const xTicks = niceTicks(xlo, xhi, Math.max(2, Math.floor((x1 - x0) / 60))).map((value) => ({ value, at: x0 + ((value - xlo) / (xhi - xlo)) * (x1 - x0), label: tickText(value) }));
  const yTicks = niceTicks(ylo, yhi, Math.max(2, Math.floor((y1 - y0) / 28))).map((value) => ({ value, at: sy(value), label: tickText(value) }));
  return { x0, y0, x1, y1, xTicks, yTicks, paths, xUnit: unit, clipped };
}

function keepFrom(s: { x: readonly number[]; y: readonly number[] }, from: number): { x: number[]; y: number[] } {
  const x: number[] = [];
  const y: number[] = [];
  for (let i = 0; i < s.x.length; i++) {
    if (s.x[i]! >= from) {
      x.push(s.x[i]!);
      y.push(s.y[i]!);
    }
  }
  return { x, y };
}

/** The share of a history's x span counted as its start-up: the impulsive start of a run, not its answer. */
export const START_SHARE = 0.05;
/** The fewest first samples counted as start-up whatever the span (the first iterations of a steady run). */
export const START_SAMPLES = 2;
/** The full range is drawn when it is at most this many times the range after the start-up. */
export const SPIKE_FACTOR = 3;

/**
 * The y range a history is drawn in by default (ADR-0022 addendum 2026-11-24): the range of its values after the
 * start-up (the first `START_SHARE` of the x span, and at least the first `START_SAMPLES` samples), unless the full
 * range is within `SPIKE_FACTOR` of it. A run that starts impulsively (a mean inlet pressure of 200 m2/s2 at the
 * second step against a swing of +-2 afterwards) would otherwise flatten everything after it into a line. Null
 * when the full range should be drawn (nothing to clip, or too few samples to tell).
 */
export function robustRange(x: readonly number[], y: readonly number[], from?: number): { range: [number, number]; full: [number, number]; after: number } | null {
  const pairs: [number, number][] = [];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    if (Number.isFinite(x[i]!) && Number.isFinite(y[i]!) && (from === undefined || x[i]! >= from)) pairs.push([x[i]!, y[i]!]);
  }
  if (pairs.length < 3 * START_SAMPLES) return null;
  const full = span(pairs.map((p) => p[1]));
  const first = pairs[0]![0];
  const last = pairs[pairs.length - 1]![0];
  const cut = first + (last - first) * START_SHARE;
  const kept = pairs.filter((p, i) => i >= START_SAMPLES && p[0] >= cut);
  const body = span(kept.map((p) => p[1]));
  if (full === null || body === null) return null;
  const width = (r: [number, number]) => r[1] - r[0];
  const scale = Math.max(width(body), 1e-12 * Math.max(1, Math.abs(body[1])));
  if (width(full) <= SPIKE_FACTOR * scale) return null;
  // `after`: where the start-up ends, the first x the range was measured from (for the caption's numbers).
  return { range: body, full, after: kept[0]![0] };
}

/**
 * The words under a transient history (ADR-0022 addendum 2026-11-24): its smallest, mean and largest value, and
 * the mean over the last whole period when a profile repeats. "Settled" and "moved over the last tenth" are a
 * steady run's questions; a time-accurate history is read for its swing. The mean weighs each sample by the time
 * it stands for (trapezoids), so an uneven write interval does not bias it. Null with fewer than two samples.
 */
export function transientStats(x: readonly number[], y: readonly number[], from: number | undefined, period: number | null): { min: number; mean: number; max: number; periodMean: number | null } | null {
  const pts: [number, number][] = [];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    if (Number.isFinite(x[i]!) && Number.isFinite(y[i]!) && (from === undefined || x[i]! >= from)) pts.push([x[i]!, y[i]!]);
  }
  if (pts.length < 2) return null;
  const mean = (a: [number, number][]): number | null => {
    if (a.length < 2) return null;
    let area = 0;
    for (let i = 1; i < a.length; i++) area += ((a[i]![1] + a[i - 1]![1]) / 2) * (a[i]![0] - a[i - 1]![0]);
    const t = a[a.length - 1]![0] - a[0]![0];
    return t > 0 ? area / t : null;
  };
  const values = pts.map((p) => p[1]);
  const end = pts[pts.length - 1]![0];
  const lastPeriod = period !== null && period > 0 && end - pts[0]![0] >= period * (1 - 1e-9) ? pts.filter((p) => p[0] >= end - period * (1 + 1e-9)) : [];
  return { min: Math.min(...values), mean: mean(pts) ?? values[0]!, max: Math.max(...values), periodMean: lastPeriod.length >= 2 ? mean(lastPeriod) : null };
}

/** The caption of a transient history: "min 0.7 · mean 1 · max 1.3 · period mean 1". */
export function transientCaption(stats: NonNullable<ReturnType<typeof transientStats>>): string {
  const parts = [`min ${formatValue(stats.min)}`, `mean ${formatValue(stats.mean)}`, `max ${formatValue(stats.max)}`];
  if (stats.periodMean !== null) parts.push(`last period's mean ${formatValue(stats.periodMean)}`);
  return parts.join(" · ");
}

/**
 * The caption under a history: its name and units, then how a steady run ended (`settled`) or a transient
 * run's min, mean, max and last period's mean (`transientCaption`). The whole of it, so the plot can wrap it
 * rather than cut the max off.
 */
export function historyCaption(
  label: string,
  units: string,
  end: ReturnType<typeof settled>,
  stats: ReturnType<typeof transientStats>,
  skipStart: boolean,
): string {
  let text = `${label}${units !== "" ? ` (${units})` : ""}`;
  if (end !== null) text += `: ${formatValue(end.last)}${end.change === null ? "" : `, ${end.change < 0.1 ? "settled" : `moved ${end.change.toFixed(1)} % over the last tenth`}`}`;
  if (stats !== null) text += `: ${transientCaption(stats)}${skipStart ? " (after the start-up)" : ""}`;
  return text;
}

/** A typed "from" of a history: a number on its x axis, or null with the reason. Empty is "from the start". */
export function historyFrom(text: string, x: readonly number[]): { from: number | undefined } | { error: string } {
  const t = text.trim();
  if (t === "") return { from: undefined };
  const n = Number(t);
  if (!Number.isFinite(n)) return { error: "Type a number on the x axis" };
  const finite = x.filter((v) => Number.isFinite(v));
  const last = finite.length > 0 ? Math.max(...finite) : 0;
  if (finite.length > 0 && n >= last) return { error: `The history ends at ${formatValue(last)}` };
  return { from: n };
}

export interface Bar {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
}

export interface BarChart {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  bars: Bar[];
  xTicks: Tick[];
  yTicks: Tick[];
}

/** The histogram as bars: x is the value, y the share of the area in percent. */
export function histogramChart(reply: HistogramReply, width: number, height: number): BarChart {
  const x0 = CHART_PAD.left;
  const y0 = CHART_PAD.top;
  const x1 = Math.max(x0 + 1, width - CHART_PAD.right);
  const y1 = Math.max(y0 + 1, height - CHART_PAD.bottom);
  const edges = reply.edges;
  const empty: BarChart = { x0, y0, x1, y1, bars: [], xTicks: [], yTicks: [] };
  if (edges.length < 2 || reply.fraction.length !== edges.length - 1) return empty;
  const lo = edges[0]!;
  const hi = edges[edges.length - 1]!;
  if (!(hi > lo)) return empty;
  const top = Math.max(...reply.fraction.map((f) => f * 100), 1e-9);
  const ymax = niceTicks(0, top, 4).at(-1) ?? top;
  const sx = (v: number) => x0 + ((v - lo) / (hi - lo)) * (x1 - x0);
  const sy = (v: number) => y1 - (v / Math.max(ymax, top)) * (y1 - y0);
  const bars = reply.fraction.map((f, i) => {
    const left = sx(edges[i]!);
    const right = sx(edges[i + 1]!);
    const yTop = sy(f * 100);
    return { x: left + 0.5, y: yTop, w: Math.max(0.5, right - left - 1), h: Math.max(0, y1 - yTop), label: `${tickText(edges[i]!)} to ${tickText(edges[i + 1]!)}: ${(f * 100).toFixed(1)} %` };
  });
  const xTicks = niceTicks(lo, hi, Math.max(2, Math.floor((x1 - x0) / 60))).map((value) => ({ value, at: sx(value), label: tickText(value) }));
  const yTicks = niceTicks(0, Math.max(ymax, top), 4).map((value) => ({ value, at: sy(value), label: `${tickText(value)}` }));
  return { x0, y0, x1, y1, bars, xTicks, yTicks };
}

/**
 * A monitor file's name as a person reads it: `patchFlowRate(patch=inlet)/0/…` is "Flow in, inlet" at an inlet and
 * "Flow out, outlet" at an outlet (ADR-0022 addendum 2026-11-24: each opening's flow in its own direction, the
 * server's `sign`), else "Flow rate, wall". A region's tables sit under its folder (`fluid/patchFlowRate(...)`).
 */
export function monitorLabel(name: string, opening = ""): { label: string; units: string } {
  const head = name.split("/").find((part) => part.startsWith("patch")) ?? name.split("/")[0] ?? name;
  const patch = /patch=([^,)]+)/.exec(head)?.[1] ?? "";
  if (head.startsWith("patchFlowRate")) return { label: `${opening === "inlet" ? "Flow in" : opening === "outlet" ? "Flow out" : "Flow rate"}, ${patch}`, units: "m3/s" };
  if (head.startsWith("patchAverage")) {
    const field = /fields=\(([^)]*)\)/.exec(head)?.[1] ?? "p";
    return { label: `Mean ${field}, ${patch}`, units: field === "p" ? "m2/s2" : "" };
  }
  return { label: head, units: "" };
}

/**
 * A monitor table's history: the first column against the first value column, as `{x, y}`, times `sign` (the
 * server's -1 for an inlet's flow rate, so the flow into the domain is positive).
 */
export function monitorSeries(table: { columns: string[]; values: Record<string, number[]> }, sign = 1): { x: number[]; y: number[]; column: string } | null {
  if (table.columns.length < 2) return null;
  const x = table.values[table.columns[0]!] ?? [];
  const column = table.columns[1]!;
  const raw = table.values[column] ?? [];
  if (x.length === 0 || x.length !== raw.length) return null;
  const y = sign === -1 ? raw.map((v) => -v) : raw;
  return { x, y, column };
}

/** The last value of a history and how much it moved over its last tenth, in percent: is the report settled? */
export function settled(y: readonly number[]): { last: number; change: number | null } | null {
  if (y.length === 0) return null;
  const last = y[y.length - 1]!;
  const back = y[Math.max(0, Math.floor(y.length * 0.9) - 1)]!;
  if (y.length < 10 || !Number.isFinite(last) || !Number.isFinite(back)) return { last, change: null };
  const scale = Math.max(Math.abs(last), Math.abs(back));
  return { last, change: scale === 0 ? 0 : (Math.abs(last - back) / scale) * 100 };
}

/** A function's name as the server will accept it (an identifier, not a given name), or why not. */
export function functionNameProblem(name: string): string | null {
  const text = name.trim();
  if (text === "") return "Give the function a name";
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,39}$/.test(text)) return "A name is a letter or _ then letters, digits or _";
  if (["x", "y", "z", "rho", "pi", "e"].includes(text)) return `${text} is already given`;
  return null;
}

/** The axis choices of an XY plot. `radius` needs an axis: the flow's direction through the surface's centre. */
export const XY_AXES: readonly { value: string; label: string }[] = [
  { value: "x", label: "x" },
  { value: "y", label: "y" },
  { value: "z", label: "z" },
  { value: "radius", label: "Radius" },
];

/** How many contour lines a smooth legend gets: a banded one draws its band edges, as Fluent's levels do. */
export const SMOOTH_LINE_BANDS = 10;

/** Whether every slice on screen draws its contour lines; null when none is on screen. */
export function contourLinesOn(slices: SliceList): boolean | null {
  const shown = visibleSlices(slices);
  return shown.length === 0 ? null : shown.every((s) => s.lines);
}

/**
 * Contour lines on or off on every slice on screen, at the legend's band edges (`bands` bands, so `bands - 1`
 * lines; a smooth legend gets `SMOOTH_LINE_BANDS`). The lines are the neutral colour, as Fluent draws them over
 * a filled contour: a line in the map's colour vanishes into the fill of its own band. The slice takes the
 * legend's banding too, so the picture is the one the legend describes.
 */
export function withContourLines(slices: SliceList, on: boolean, bands: number): SliceList {
  const levels = bands >= 2 ? bands : SMOOTH_LINE_BANDS;
  let next = slices;
  for (const spec of visibleSlices(slices)) next = updateSlice(next, spec.id, on ? { lines: true, levels, lineColour: "neutral", bands: bands >= 2 } : { lines: false });
  return next;
}

/**
 * Lines on or off for the slice the user sees. The run's own mid-plane cut (`results.view.slice`) is not in the
 * slice list and draws no lines, so turning lines on over it puts an axis slice at the same place in the list
 * (`addAxisSlice`) and the mid-plane is turned off: the picture is the same cut, now with lines.
 */
export function linesFor(
  slices: SliceList,
  midShown: boolean,
  midPlane: { axis: SectionAxis; offset: number } | null,
  on: boolean,
  bands: number,
): { slices: SliceList; midOff: boolean } {
  if (on && visibleSlices(slices).length === 0 && midShown && midPlane !== null) {
    // A hidden slice already at the mid-plane is shown again rather than a second one added beside it.
    const same = slices.list.find((s) => {
      const at = axisOfPlane(s.plane);
      return at !== null && at.axis === midPlane.axis && Math.abs(at.position - midPlane.offset) <= 1e-9 * Math.max(1, Math.abs(midPlane.offset));
    });
    const added = same !== undefined ? updateSlice(slices, same.id, { visible: true }) : addAxisSlice(slices, midPlane.axis, midPlane.offset);
    return { slices: withContourLines(added, true, bands), midOff: added !== slices };
  }
  return { slices: withContourLines(slices, on, bands), midOff: false };
}

export interface FitAsk {
  surfaces: string[];
  plane?: string;
}

/** Which surfaces a Fit spreads the colours over: the slice on screen, else every boundary. */
export function fitSurfaces(sliceShown: boolean, plane: string | null, patches: readonly string[]): FitAsk | null {
  if (sliceShown) return plane === null ? { surfaces: ["slice"] } : { surfaces: ["slice"], plane };
  return patches.length === 0 ? null : { surfaces: [...patches] };
}
