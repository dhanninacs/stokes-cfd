/**
 * The plot over a line (R5 of the results overhaul), as pure functions: the two clicks that make a line, the
 * `run.probe` ask for it, the plot's geometry, and the CSV and file names. No React, no store, no DOM, so the
 * rules are tested in Node; `guided/lineControl.ts` is the impure half (it reads the stores, asks the server and
 * saves files) and `guided/LinePlot.tsx` only draws what this lays out.
 *
 * **The line is two points and a count.** The server samples `n` evenly spaced points between them
 * (`run.probe` with a two-point `polyline` and `n`) and answers the distance along the line and the value there,
 * so the plot's x axis is a true distance (metres), not an index. The answer is never extrapolated: a point off
 * the surface is "outside", the same fact as for a single probe point.
 *
 * **Where the line lies.** With the run's mid-plane slice on, the line is on that plane (`patch: "slice"`): the
 * clicks land on whatever the pick met, so each is carried to the plane along the camera ray (`rayOnPlane`).
 * Without a slice the line is read on the mesh patch under the FIRST click, as a single probe is.
 *
 * View state only: nothing here is a project command and nothing is journaled. A saved view keeps the line as
 * `extras.line` `{a, b, n, patch}` (`viewsModel.ts` `lineSpecOf`, R5b).
 */
import { colorAtIn, type ColormapId } from "../state/colormap";
import { niceTicks, type Tick } from "../state/residualChart";
import { phaseOfCode } from "./probeModel";
import { safeNamePart } from "./exportModel";
import { formatLength } from "../viewport/scale";

export type Vec3 = readonly [number, number, number];

/** The default number of samples along a line. */
export const DEFAULT_LINE_POINTS = 100;
/** The most the server returns (`MAX_LINE_SAMPLES` in `stokes/api/commands/run.py`). */
export const MAX_LINE_POINTS = 1000;

/**
 * Where the line is: `pickA` and `pickB` wait for a click (the scene's point pick is armed), `preparing` is the
 * server building the surface (retried by itself), `ready` has a plot, `outside` is a line that leaves the surface
 * (pick again), `failed` is anything else.
 */
export type LinePhase = "idle" | "pickA" | "pickB" | "preparing" | "ready" | "outside" | "failed";

export interface LineState {
  a: Vec3 | null;
  b: Vec3 | null;
  /** How many points the server samples along the line. */
  n: number;
  phase: LinePhase;
  /** The distance from A of each sample, metres. Empty unless `phase` is `ready`. */
  along: readonly number[];
  values: readonly number[];
  /** The field the values are of, so a stale answer is not drawn under a new field's name. */
  field: string | null;
  /** What went wrong, in words, or "". */
  message: string;
  /** The surface the ask is made on: `"slice"` or a `mesh_patch` node id; null before the first click. */
  patch: string | null;
  /** The results' sequence number when the values were asked (`results.seq`), so a new run or time is told from the same one. */
  seq: number;
}

export const NO_LINE: LineState = { a: null, b: null, n: DEFAULT_LINE_POINTS, phase: "idle", along: [], values: [], field: null, message: "", patch: null, seq: 0 };

/** A line pick is waiting for a click (the Line button is lit). */
export function isLinePicking(phase: LinePhase): boolean {
  return phase === "pickA" || phase === "pickB";
}

/** The line has both ends, so it is drawn in the viewport. */
export function lineDrawn(line: LineState): boolean {
  return line.a !== null && line.b !== null;
}

/** Pressing Line: a pick that is waiting is cancelled; otherwise (nothing, or a line already made) a new pick starts. */
export function pressLine(line: LineState): LineState {
  return isLinePicking(line.phase) ? { ...NO_LINE, n: line.n } : { ...NO_LINE, n: line.n, phase: "pickA" };
}

// ---------------------------------------------------------------------------------------------- the points
/** `n` evenly spaced points from `a` to `b`, both ends included; fewer than 2 gives the two ends. */
export function samplePoints(a: Vec3, b: Vec3, n: number): [number, number, number][] {
  const count = Number.isFinite(n) ? Math.min(MAX_LINE_POINTS, Math.max(2, Math.floor(n))) : 2;
  const out: [number, number, number][] = [];
  for (let i = 0; i < count; i++) {
    const t = i / (count - 1);
    out.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])]);
  }
  return out;
}

/** The straight-line distance between two points, metres. */
export function lineLength(a: Vec3, b: Vec3): number {
  return Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
}

/** A plane along a world axis: `x`, `y` or `z` equals `offset`. */
export interface AxisPlane {
  axis: "x" | "y" | "z";
  offset: number;
}

const AXIS_INDEX = { x: 0, y: 1, z: 2 } as const;

/**
 * Where a click lands on the slice's plane: the camera ray's crossing of the plane, or, when the ray runs
 * (almost) along it or there is none, the picked point carried straight onto the plane. The click lands on
 * whatever face the pick met (a wall in front of the slice, say), which is not in the plane the line is read on.
 */
export function rayOnPlane(hit: { point: Vec3; ray?: { origin: Vec3; direction: Vec3 } | undefined }, plane: AxisPlane): [number, number, number] {
  const i = AXIS_INDEX[plane.axis];
  if (hit.ray !== undefined) {
    const d = hit.ray.direction[i];
    if (Math.abs(d) > 1e-6) {
      const t = (plane.offset - hit.ray.origin[i]) / d;
      if (t > 0) {
        const o = hit.ray.origin;
        const v = hit.ray.direction;
        const p: [number, number, number] = [o[0] + t * v[0], o[1] + t * v[1], o[2] + t * v[2]];
        p[i] = plane.offset;
        return p;
      }
    }
  }
  const p: [number, number, number] = [hit.point[0], hit.point[1], hit.point[2]];
  p[i] = plane.offset;
  return p;
}

/** A line carried onto a (moved) slice plane: both ends keep their other two coordinates. */
export function carryToPlane(line: LineState, plane: AxisPlane): LineState {
  if (line.a === null || line.b === null) return line;
  const i = AXIS_INDEX[plane.axis];
  const move = (p: Vec3): Vec3 => (p[i] === plane.offset ? p : (p.map((c, k) => (k === i ? plane.offset : c)) as unknown as Vec3));
  const a = move(line.a);
  const b = move(line.b);
  return a === line.a && b === line.b ? line : { ...line, a, b };
}

// ---------------------------------------------------------------------------------------------- the two clicks
/**
 * The line after a click: the first click is A, the second is B and makes the ask. A second click on A itself is
 * not a line, so it is refused with a word and waits for another. `patch` is the surface the first click
 * chose (`"slice"` or a patch id) and is kept for the ask.
 */
export function pickPoint(line: LineState, point: Vec3, patch: string): LineState {
  if (line.phase === "pickA") return { ...line, a: point, b: null, patch, message: "", phase: "pickB" };
  if (line.phase === "pickB" && line.a !== null) {
    if (lineLength(line.a, point) < 1e-12) return { ...line, message: "Pick a second point" };
    return { ...line, b: point, phase: "preparing", along: [], values: [], message: "" };
  }
  return line;
}

// ---------------------------------------------------------------------------------------------- the ask
/** The `run.probe` params for the line, or null before both ends are set. */
export function lineAsk(line: LineState, field: string): { patch: string; field: string; polyline: [Vec3, Vec3]; n: number } | null {
  if (line.a === null || line.b === null || line.patch === null) return null;
  return { patch: line.patch, field, polyline: [line.a, line.b], n: Math.min(MAX_LINE_POINTS, Math.max(2, Math.floor(line.n))) };
}

/**
 * True when a made line no longer matches what is on screen and must be asked again: it has both ends, is not
 * waiting on an answer already, and was read for another field or another set of results.
 */
export function lineStale(line: LineState, field: string, seq: number): boolean {
  if (line.a === null || line.b === null || line.patch === null) return false;
  if (line.phase !== "ready" && line.phase !== "outside" && line.phase !== "failed") return false;
  return line.field !== field || line.seq !== seq;
}

/** The numbers of an answer, or null when it is not about this line (a length mismatch, a non-number). */
export function readLineReply(reply: unknown, n: number): { along: number[]; values: number[] } | null {
  const r = reply as { along?: unknown; values?: unknown } | null;
  if (r === null || typeof r !== "object" || !Array.isArray(r.along) || !Array.isArray(r.values)) return null;
  const along = r.along as unknown[];
  const values = r.values as unknown[];
  if (along.length !== values.length || along.length < 2 || along.length > Math.max(n, 2)) return null;
  if (![...along, ...values].every((v) => typeof v === "number")) return null;
  return { along: along as number[], values: values as number[] };
}

/** The phase an error answer puts the line in; "preparing" retries, the rest end the ask. */
export function lineErrorPhase(code: string | null | undefined): LinePhase {
  const phase = phaseOfCode(code);
  return phase === "preparing" ? "preparing" : phase === "outside" ? "outside" : "failed";
}

// ---------------------------------------------------------------------------------------------- words
/** A few words for the card: the note under the buttons, and its tooltip. Empty for a line that needs none. */
export function lineNote(line: LineState): { text: string; tip: string } | null {
  switch (line.phase) {
    case "pickA":
      return { text: "Click point A", tip: "Click the model where the line starts" };
    case "pickB":
      return { text: line.message !== "" ? line.message : "Click point B", tip: "Click the model where the line ends" };
    case "preparing":
      return { text: "Preparing…", tip: "The surface the line reads is being prepared" };
    case "outside":
      return { text: "Outside the model", tip: line.message !== "" ? line.message : "The line leaves the surface it is read on" };
    case "failed":
      return { text: "No reading", tip: line.message };
    default:
      return null;
  }
}

/** `|U| (m/s) along 120 mm`: the plot's title. The field label and units are the caller's (`fieldLabel`, `fieldUnits`). */
export function lineTitle(label: string, units: string, metres: number): string {
  const what = units === "" ? label : `${label} (${units})`;
  const length = formatLength(metres);
  return length === "—" ? what : `${what} along ${length}`;
}

function sig6(v: number): string {
  return Number.isFinite(v) ? String(Number(v.toPrecision(6))) : "";
}

/** `distance_m,<field>[<units>]` then one row per sample, 6 significant digits; a value that is not a number is an empty cell. */
export function lineCsv(field: string, units: string, along: readonly number[], values: readonly number[]): string {
  const head = `distance_m,${field}${units === "" ? "" : `[${units}]`}`;
  const rows = along.map((d, i) => `${sig6(d)},${sig6(values[i] ?? Number.NaN)}`);
  return `${[head, ...rows].join("\n")}\n`;
}

/** `<project>-<run>-<field>-line.csv` (or `.png`) with safe characters; an empty part is left out. */
export function lineFileName(parts: { project: string; run: string; field: string }, extension: "csv" | "png"): string {
  const pieces = [safeNamePart(parts.project) || "stokes", safeNamePart(parts.run), safeNamePart(parts.field), "line"].filter((p) => p !== "");
  return `${pieces.join("-")}.${extension}`;
}

// ---------------------------------------------------------------------------------------------- the plot
/** The curve's colour: the middle of the results' colour map, so the plot wears the map the figure does. */
export function curveColour(map: ColormapId, reverse: boolean): string {
  const [r, g, b] = colorAtIn(map, 0.5, reverse);
  return `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`;
}

export interface LinePad {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export const LINE_PAD: LinePad = { left: 44, right: 8, top: 8, bottom: 26 };

/** The length unit of the x axis, picked once for the whole line (a ladder tick in km, m, mm or µm). */
export function lengthUnitFor(maxMetres: number): { suffix: string; scale: number } {
  const text = formatLength(maxMetres);
  const suffix = text === "—" ? "m" : (text.split(" ")[1] ?? "m");
  const scale = suffix === "km" ? 1000 : suffix === "mm" ? 1e-3 : suffix === "µm" ? 1e-6 : 1;
  return { suffix, scale };
}

/** A tick's text: three significant digits, exponent form at the extremes. */
export function tickText(v: number): string {
  if (v === 0 || !Number.isFinite(v)) return "0";
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(1).replace(/\.0e/, "e").replace("e+", "e");
  return String(Number(v.toPrecision(3)));
}

export interface LineChart {
  /** The plot area in px. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  /** The `<path>` d, "" when nothing is drawn. Gaps (a value that is not a number) break it. */
  d: string;
  xTicks: Tick[];
  yTicks: Tick[];
  /** The axis' unit, `mm`: the label of the x axis reads `distance (mm)`. */
  unit: string;
  yMin: number;
  yMax: number;
}

/**
 * The plot as geometry for a box of `width` x `height` px: x is the distance in the line's own display unit, y the
 * field, padded 5 % so the curve does not touch the frame. **A flat line still draws** (every value equal is a
 * result): the span is widened so the curve crosses the middle.
 */
export function lineChart(along: readonly number[], values: readonly number[], width: number, height: number, pad: LinePad = LINE_PAD): LineChart {
  const x0 = pad.left;
  const y0 = pad.top;
  const x1 = Math.max(x0 + 1, width - pad.right);
  const y1 = Math.max(y0 + 1, height - pad.bottom);
  const empty: LineChart = { x0, y0, x1, y1, d: "", xTicks: [], yTicks: [], unit: "m", yMin: 0, yMax: 1 };
  if (along.length < 2 || along.length !== values.length) return empty;
  const total = along[along.length - 1]!;
  if (!(total > 0)) return empty;
  const { suffix, scale } = lengthUnitFor(total);
  const xs = along.map((d) => d / scale);
  const xMax = total / scale;
  const finite = values.filter((v) => Number.isFinite(v));
  if (finite.length === 0) return { ...empty, unit: suffix };
  let lo = Math.min(...finite);
  let hi = Math.max(...finite);
  if (hi - lo < 1e-12 * Math.max(1, Math.abs(hi))) {
    const w = Math.abs(hi) > 0 ? Math.abs(hi) * 0.05 : 0.5;
    lo -= w;
    hi += w;
  } else {
    const w = (hi - lo) * 0.05;
    lo -= w;
    hi += w;
  }
  const sx = (v: number) => x0 + (v / xMax) * (x1 - x0);
  const sy = (v: number) => y1 - ((v - lo) / (hi - lo)) * (y1 - y0);
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (!Number.isFinite(v)) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${sx(xs[i]!).toFixed(1)} ${sy(v).toFixed(1)}`;
    pen = true;
  });
  const xTicks = niceTicks(0, xMax, Math.max(2, Math.floor((x1 - x0) / 60))).map((value) => ({ value, at: sx(value), label: tickText(value) }));
  const yTicks = niceTicks(lo, hi, Math.max(2, Math.floor((y1 - y0) / 28))).map((value) => ({ value, at: sy(value), label: tickText(value) }));
  return { x0, y0, x1, y1, d, xTicks, yTicks, unit: suffix, yMin: lo, yMax: hi };
}
