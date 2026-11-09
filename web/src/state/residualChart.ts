/**
 * The residual chart's geometry (T-217): a residual history in, the SVG paths and tick marks out, so
 * the component draws and does not think. Log y axis in whole decades, linear x in iterations, one line
 * per field, a dashed line at the convergence target.
 *
 * `NaN` in a field (the solver did not report it in that block) breaks the line there; a lone number
 * between two breaks is drawn as a dot, so it never vanishes. A field with a value of zero or less has
 * no place on a log axis and is a break too.
 *
 * Import rule: no React, no store, no DOM.
 */
import type { ResidualSeries } from "./residualSeries";

export interface ChartPad {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export const CHART_PAD: ChartPad = { left: 40, right: 10, top: 8, bottom: 32 };

export interface Tick {
  value: number;
  /** The position in px along the axis. */
  at: number;
  label: string;
}

export interface ChartLine {
  name: string;
  /** The field's index in the series, which picks its colour token (`--series-N`, cyclic). */
  index: number;
  d: string;
  /** Numbers with a break on each side: drawn as dots. */
  dots: { x: number; y: number }[];
}

export interface ChartModel {
  /** The plot area in px. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  /** The decades the y axis spans, as exponents of 10. */
  eMin: number;
  eMax: number;
  xTicks: Tick[];
  yTicks: Tick[];
  lines: ChartLine[];
  /** The convergence target's y in px, when it is set; the label reads "target 1e-4". */
  target: { y: number; label: string } | null;
}

/** 1, 1e-1, 1e-2 ... the one label format of the log axis. */
export function decadeLabel(exponent: number): string {
  return exponent === 0 ? "1" : `1e${exponent}`;
}

/** A residual as the axis and the target label print it: "1e-4", "2.5e-5". */
export function targetLabel(value: number): string {
  const [m, e] = value.toExponential(1).split("e");
  return `${m.replace(/\.0$/, "")}e${Number(e)}`;
}

/** Round tick values for a linear axis: 1, 2, 5 times a power of ten, about `count` of them; `whole` keeps the step a whole number (iterations). */
export function niceTicks(min: number, max: number, count: number, whole = false): number[] {
  if (!(max > min) || count < 1) return [min];
  const raw = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const f = raw / power;
  const step = Math.max(whole ? 1 : 0, (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * power);
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Math.round(v / step) * step);
  return out;
}

/** The decades a set of values spans, at least one: the floor of the smallest and the ceiling of the largest. */
export function decadeRange(values: readonly number[]): { eMin: number; eMax: number } {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (!(v > 0) || !Number.isFinite(v)) continue;
    lo = Math.min(lo, v);
    hi = Math.max(hi, v);
  }
  if (lo === Infinity) return { eMin: -6, eMax: 0 };
  let eMin = Math.floor(Math.log10(lo) + 1e-9);
  const eMax = Math.max(Math.ceil(Math.log10(hi) - 1e-9), eMin + 1);
  if (eMin >= eMax) eMin = eMax - 1;
  return { eMin, eMax };
}

interface Point {
  x: number;
  y: number;
}

/** At most about two points per pixel column survive: each column keeps its first, lowest, highest and last, in order. */
function decimate(xs: readonly number[], ys: readonly number[], buckets: number): { i: number }[] {
  const n = xs.length;
  const picks: { i: number }[] = [];
  const size = n / buckets;
  for (let b = 0; b < buckets; b++) {
    const a = Math.floor(b * size);
    const z = Math.min(n, Math.floor((b + 1) * size));
    if (z <= a) continue;
    let lo = -1;
    let hi = -1;
    for (let i = a; i < z; i++) {
      if (!Number.isFinite(ys[i])) continue;
      if (lo < 0 || ys[i] < ys[lo]) lo = i;
      if (hi < 0 || ys[i] > ys[hi]) hi = i;
    }
    if (lo < 0) {
      picks.push({ i: a }); // the whole column is a gap
      continue;
    }
    const set = new Set<number>([a, lo, hi, z - 1]);
    for (const i of [...set].sort((p, q) => p - q)) picks.push({ i });
  }
  return picks;
}

function path(points: readonly (Point | null)[]): { d: string; dots: Point[] } {
  const parts: string[] = [];
  const dots: Point[] = [];
  let run: Point[] = [];
  const flush = () => {
    if (run.length === 1) dots.push(run[0]);
    else if (run.length > 1) parts.push(`M${run.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join("L")}`);
    run = [];
  };
  for (const p of points) {
    if (p === null) flush();
    else run.push(p);
  }
  flush();
  return { d: parts.join(""), dots };
}

/**
 * The chart for a size. `hidden` names the fields the reader switched off in the legend: they are not
 * drawn and do not stretch the axis. `target` is `residual_target`; it is on the axis whatever the data.
 */
export function buildChart(series: ResidualSeries, hidden: ReadonlySet<string>, target: number | null, width: number, height: number, pad: ChartPad = CHART_PAD): ChartModel {
  const x0 = pad.left;
  const y0 = pad.top;
  const x1 = Math.max(x0 + 1, width - pad.right);
  const y1 = Math.max(y0 + 1, height - pad.bottom);

  const shown = series.fields.map((f, index) => ({ f, index })).filter(({ f }) => !hidden.has(f.name));
  const values: number[] = [];
  for (const { f } of shown) for (const v of f.values) if (v > 0) values.push(v);
  if (target !== null) values.push(target);
  const { eMin, eMax } = decadeRange(values);

  const iterations = series.iterations;
  const first = iterations.length > 0 ? iterations[0] : 0;
  const last = iterations.length > 0 ? iterations[iterations.length - 1] : 1;
  const xMin = first;
  const xMax = last > first ? last : first + 1;
  const sx = (v: number) => x0 + ((v - xMin) / (xMax - xMin)) * (x1 - x0);
  const sy = (v: number) => y1 - ((Math.log10(v) - eMin) / (eMax - eMin)) * (y1 - y0);

  const lines: ChartLine[] = shown.map(({ f, index }) => {
    const ys = f.values.map((v) => (v > 0 && Number.isFinite(v) ? v : Number.NaN));
    const buckets = Math.max(1, Math.floor((x1 - x0) * 2));
    const picks = iterations.length > buckets * 2 ? decimate(iterations, ys, buckets) : iterations.map((_, i) => ({ i }));
    const points = picks.map(({ i }) => (Number.isFinite(ys[i]) ? { x: sx(iterations[i]), y: sy(ys[i]) } : null));
    const { d, dots } = path(points);
    return { name: f.name, index, d, dots };
  });

  const perDecade = (y1 - y0) / (eMax - eMin);
  const step = perDecade >= 18 ? 1 : Math.ceil(18 / perDecade);
  const yTicks: Tick[] = [];
  for (let e = eMax; e >= eMin; e--) if ((eMax - e) % step === 0) yTicks.push({ value: 10 ** e, at: y1 - ((e - eMin) / (eMax - eMin)) * (y1 - y0), label: decadeLabel(e) });

  const xTicks = niceTicks(xMin, xMax, Math.max(2, Math.floor((x1 - x0) / 70)), true).map((value) => ({ value, at: sx(value), label: String(value) }));

  return { x0, y0, x1, y1, eMin, eMax, xTicks, yTicks, lines, target: target !== null ? { y: sy(target), label: `target ${targetLabel(target)}` } : null };
}
