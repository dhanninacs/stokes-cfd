/**
 * The logic of the results on the mesh (T-218), as pure functions: which fields can be chosen, what the
 * range is, what the legend prints, which tint a patch takes, the order the patches' fields are fetched in,
 * and when the results are loaded, kept or dropped. The `results` slice of the store, the Results panel, the
 * legend and `FieldsController` (viewport/fields.ts) are thin readers of it.
 *
 * View state only: nothing here is a project command, nothing is journaled, nothing is saved in the project.
 *
 * Import rule: no React, no store, no DOM. Types only from other modules, so this loads in Node.
 */

import type { NodeSummary } from "../api/types";
import { fraction, tintFor, type ColormapId } from "../state/colormap";
import type { LegendSettings } from "./legendModel";
import type { FieldValues } from "../state/fieldCodec";
import type { Stage } from "../state/store";
import { isMeshRun, isRunActive, type RunSummary } from "./runApi";
import type { RunFields } from "./resultsApi";

// ---------------------------------------------------------------------------------------------- the slice of the store
/** A range the user fixed for one field. */
export interface RangeLock {
  field: string;
  min: number;
  max: number;
}

/** What is drawn: which field (`none` for no colouring), a fixed range or null for the run's own, and the slice. */
export interface ResultsView extends Partial<LegendSettings> {
  field: string;
  lock: RangeLock | null;
  slice: boolean;
  /**
   * Draw the mid-plane slice's cell edges. **Absent is off**: the edges drew as black dashes through a figure's
   * colours, and the Display menu's mesh mode no longer decides it (`scene.setSourceCellEdges` `own`). The Results
   * panel's Slice section turns it on; a view without the field (a test literal, a saved workspace) has none.
   */
  sliceEdges?: boolean;
  /**
   * How many flat bands the colour map is cut into, or `SMOOTH` (0) for the gradient that ships.
   *
   * **View state, like `field` and `lock`**: no journal record, no undo step, and the default is smooth
   * because the dark palette is approved and banding is an addition to it.
   */
  bands: number;
  /*
    **The legend's settings (`legendModel.ts`, R1) are OPTIONAL fields of this view, not new required ones**:
    a view built before they existed - a test literal, a saved workspace, `NO_RESULTS` - has none of them, and
    `legendOf` answers the default (viridis, smooth, auto range, auto format, right) for each. They are view
    state like `field` and `lock`: no journal record, no undo step.
  */
}

export type ResultsStatus = "idle" | "loading" | "ready" | "error";

/** The `results` slice of the store. `data` is what `run.fields` said about `run`; the arrays are fetched per patch by the controller. */
export interface ResultsLive {
  view: ResultsView;
  /** The run `data` belongs to. */
  run: string | null;
  status: ResultsStatus;
  /** The refusal or failure in words, or "". */
  message: string;
  data: RunFields | null;
  /** Grows with every load and every drop, so a reader can tell new results from the ones it has. */
  seq: number;
}

export const NO_FIELD = "none";

/** The scene source of the mid-plane slice: an extra mesh source beside the patches (`mesh:<node id>`). */
export const SLICE_SOURCE = "mesh:slice";
export const SLICE_NAME = "Mid-plane slice (mesh)";

/**
 * How many BANDS the smooth colour map is cut into (claude-win2's results item 3).
 *
 * **0 means smooth**, which is the shipped look and must stay the default: the owner approved the dark
 * palette and "band count" is an addition, not a restyle.
 */
export const SMOOTH = 0;

export const NO_RESULTS: ResultsLive = { view: { field: NO_FIELD, lock: null, slice: false, bands: SMOOTH }, run: null, status: "idle", message: "", data: null, seq: 0 };

// ---------------------------------------------------------------------------------------------- names and units
/**
 * The pressure's name for its unit: `p/rho` for an incompressible solver's kinematic pressure (`m2/s2`,
 * and what an older server that sends no unit means), `p` for a pressure in Pa (the energy engine's
 * `of14.fluid`, ADR-0027), which is a pressure and not a pressure over a density.
 */
export function pressureLabel(units: string): string {
  return units === "" || units === "m2/s2" ? "p/rho" : "p";
}

/** The label a field has in the selector, the tree and the legend: `|U|` for the speed, the pressure by its unit (`pressureLabel`). */
export function fieldLabel(name: string, units = ""): string {
  if (name === "U_mag") return "|U|";
  if (name === "p") return pressureLabel(units);
  return name;
}

/** The unit text of a field as the run says it; the pressure of an incompressible solver is kinematic. */
export function fieldUnits(data: RunFields | null, name: string): string {
  const fromRun = data?.fields.find((f) => f.name === name)?.units;
  if (fromRun !== undefined && fromRun !== "") return fromRun;
  if (name === "U_mag") return "m/s";
  if (name === "p") return "m2/s2";
  return "";
}

/** `|U| (m/s)`, `p/rho (m2/s2)` and `p (Pa)`: what the legend prints above its bar. */
export function fieldTitle(data: RunFields | null, name: string): string {
  const units = fieldUnits(data, name);
  return units === "" ? fieldLabel(name, units) : `${fieldLabel(name, units)} (${units})`;
}

export interface FieldOption {
  name: string;
  label: string;
}

/** The vector `U` is not drawn: its magnitude `U_mag` is. */
const VECTOR_FIELDS: readonly string[] = ["U"];

/** The fields the run has that can colour a surface, `|U|` first, then `p`, then the rest by name. */
export function scalarFields(data: RunFields | null): FieldOption[] {
  if (data === null) return [];
  const rank = (name: string): number => (name === "U_mag" ? 0 : name === "p" ? 1 : 2);
  const stored = data.fields
    .filter((f) => !VECTOR_FIELDS.includes(f.name))
    .map((f) => f.name)
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
    .map((name) => ({ name, label: fieldLabel(name, fieldUnits(data, name)) }));
  /*
    **The velocity's components follow |U|** when the run HAS a velocity: they are the three numbers
    behind the magnitude the user is already looking at, derived in the browser
    (`derivedComponentOf`), so they cost no request and no schema change. Without a `U` in the run there
    is nothing to derive them from, and they are not offered - a menu entry that cannot be answered is
    worse than a shorter menu.
  */
  if (!data.fields.some((f) => f.name === "U")) return stored;
  const at = stored.findIndex((o) => o.name === "U_mag");
  const components = DERIVED_COMPONENTS.map((c) => ({ name: c.name, label: c.label }));
  return at === -1 ? [...stored, ...components] : [...stored.slice(0, at + 1), ...components, ...stored.slice(at + 1)];
}

// ---------------------------------------------------------------------------------------------- the range
export interface Range {
  min: number;
  max: number;
}

/** The run's own range of a field (`run.fields`' min and max). Null when the run lacks it. */
export function fieldRange(data: RunFields | null, name: string): Range | null {
  /*
    **A derived component has NO range here, and that is the corrected behaviour.**

    An earlier version borrowed `U`'s range for `U_x`/`U_y`/`U_z`. That is **wrong, not merely
    approximate**: `U`'s range is measured over the **magnitude**, which is non-negative, while a
    component is **signed**. On a duct with recirculation `Ux` runs to, say, -0.04, and a legend scaled
    to the magnitude's 0..0.158 would **clip every negative value at the bottom of the map** - a
    reversed-flow region painted as if it were still. `Uy` and `Uz` are worse: their own extrema are
    small and symmetric, so the magnitude's maximum compresses the whole component into a sliver.

    **So a component's range comes from its own sampled values** (`componentRange`, the finite signed
    extrema), which the caller supplies once it has them; until then the answer is `null` and the caller
    says so rather than drawing against a range that belongs to a different quantity.
  */
  const f = data?.fields.find((x) => x.name === name);
  // **A stored component uses its stored range** - signed, because the server measured it. Only a
  // component the run does NOT store is refused, and refusing it is the point.
  if (f === undefined && derivedComponentOf(name) !== null) return null;
  return f === undefined || !(f.min <= f.max) ? null : { min: f.min, max: f.max };
}

/**
 * The range a DERIVED COMPONENT is coloured against: its own **signed** extrema, or null.
 *
 * `sampled` is the component's values once the caller has them. **This is the only correct source for
 * a component**: `run.fields` has no entry for `U_x`, and the vector's range is over the magnitude,
 * which cannot express a negative.
 */
export function componentRangeOrNull(
  field: string,
  sampled: { component: 0 | 1 | 2; values: Float32Array } | null,
): Range | null {
  const axis = derivedComponentOf(field);
  if (axis === null || sampled === null || sampled.component !== axis) return null;
  if (sampled.values.length === 0) return null;
  let min = Infinity;
  let max = -Infinity;
  for (const v of sampled.values) {
    if (!Number.isFinite(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  // **A constant component still needs a span**: every value equal means the field is uniform, not that
  // it has no range, and a zero-width range would divide by zero in the colour map.
  if (!Number.isFinite(min)) return null;
  return min === max ? { min, max: min + (min === 0 ? 1 : Math.abs(min) * 1e-9) } : { min, max };
}

/** The range the colours span: the user's fixed range for this field (swapped when min is above max), else the run's. Null with no field. */
export function effectiveRange(view: ResultsView, data: RunFields | null): Range | null {
  if (view.field === NO_FIELD) return null;
  const lock = view.lock;
  if (lock !== null && lock.field === view.field) return lock.min <= lock.max ? { min: lock.min, max: lock.max } : { min: lock.max, max: lock.min };
  return autoRange(view, data, view.field);
}

/**
 * The AUTO range of a field: its 1-99 percentile range when the run reported one (`p01`, `p99`), so a few
 * stagnation-point spikes do not flatten the whole picture into one colour, else the run's own min..max.
 * `view.fullRange` asks for min..max even when percentiles are there.
 *
 * **The percentile is clamped into the run's range** and used only when it has a width: a server that sent
 * `p01 >= p99` (a constant field) or numbers outside min..max would otherwise draw against a range that is
 * not the data's.
 */
export function autoRange(view: Pick<ResultsView, "fullRange">, data: RunFields | null, field: string): Range | null {
  const full = fieldRange(data, field);
  if (full === null || view.fullRange === true) return full;
  const f = data?.fields.find((x) => x.name === field);
  const low = f?.p01;
  const high = f?.p99;
  if (low === undefined || high === undefined || !(low < high)) return full;
  const min = Math.max(low, full.min);
  const max = Math.min(high, full.max);
  return min < max ? { min, max } : full;
}

/** Whether the run gives percentiles for a field, i.e. whether "full range" is a different answer from "auto". */
export function hasPercentiles(data: RunFields | null, field: string): boolean {
  const f = data?.fields.find((x) => x.name === field);
  return f?.p01 !== undefined && f.p99 !== undefined && f.p01 < f.p99;
}

/**
 * The range the colours of one field span: the user's fixed range when it is for this field (swapped when min
 * is above max), else the run's own. The slices use it for fields other than the one on the walls, so every
 * surface painted with a field shares that field's scale. Null with no field or a run that lacks it.
 */
export function rangeForField(view: ResultsView, data: RunFields | null, field: string): Range | null {
  if (field === NO_FIELD) return null;
  const lock = view.lock;
  if (lock !== null && lock.field === field) return lock.min <= lock.max ? { min: lock.min, max: lock.max } : { min: lock.max, max: lock.min };
  return autoRange(view, data, field);
}

/** True when a fixed range is in force for the chosen field. */
export function isLocked(view: ResultsView): boolean {
  return view.lock !== null && view.lock.field === view.field && view.field !== NO_FIELD;
}

/** The view with the range fixed at what it is now (or at `range`); with no field there is nothing to fix. */
export function lockRange(view: ResultsView, data: RunFields | null, range?: Range): ResultsView {
  const current = range ?? effectiveRange(view, data);
  if (current === null || view.field === NO_FIELD) return view;
  return { ...view, lock: { field: view.field, min: current.min, max: current.max } };
}

export function unlockRange(view: ResultsView): ResultsView {
  return view.lock === null ? view : { ...view, lock: null };
}

/** One end of the fixed range after typing; the other end is kept. Returns the view unchanged for a value that is not a number. */
export function setLockEnd(view: ResultsView, data: RunFields | null, end: "min" | "max", value: number | null): ResultsView {
  if (value === null || !Number.isFinite(value)) return view;
  const base = view.lock !== null && view.lock.field === view.field ? view.lock : lockRange(view, data).lock;
  if (base === null) return view;
  return { ...view, lock: { ...base, [end]: value } };
}

/** A typed range end as a number, or null when it is empty or not a number. */
export function parseRangeInput(text: string): number | null {
  const trimmed = text.trim().replace(",", ".");
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

// ---------------------------------------------------------------------------------------------- the legend
/** `n` values from `min` to `max`, evenly spaced, both ends included. One value reads as the min alone. */
export function legendTicks(min: number, max: number, n = 5): number[] {
  if (n < 2) return [min];
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(i === n - 1 ? max : min + ((max - min) * i) / (n - 1));
  return out;
}

/** A number for a tick: three significant digits, `e` notation beyond 1e4 and below 1e-3, and "0" for zero. */
export function formatTick(value: number): string {
  if (!Number.isFinite(value)) return "-";
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  if (magnitude >= 1e4 || magnitude < 1e-3) {
    const [mantissa, exponent] = value.toExponential(2).split("e");
    const trimmed = mantissa.includes(".") ? mantissa.replace(/0+$/, "").replace(/\.$/, "") : mantissa;
    return `${trimmed}e${Number(exponent)}`;
  }
  return String(Number(value.toPrecision(3)));
}

// ---------------------------------------------------------------------------------------------- what is on screen
/** Whether the mesh is coloured: the Results stage, a field chosen and the run's results in hand. */
export function resultsShown(stage: Stage, view: ResultsView, hasData: boolean): boolean {
  return stage === "Results" && view.field !== NO_FIELD && hasData;
}

/**
 * The tint of one surface for a field, or null when the surface has no values for it (404 or 400 from the
 * server: it keeps its own colour; a surface without a result is never drawn as zero). Null too when the
 * field is not one value per vertex of the surface in the scene (`vertices`), which the scene would drop
 * with a warning.
 */
/**
 * The fields already reported as multi-component, so a redraw does not log the same thing every frame.
 *
 * **Keyed by field AND by the object reported**, and both are needed: a module-level `Set<string>`
 * persisted for the life of the page, so a field reported once stayed silent for every later project
 * - and, in the tests, across every later test. Measured: three `fields.test.ts` cases failed only
 * when run together, and each passed alone.
 */
const reportedMultiComponent = new WeakMap<object, Set<string>>();

function logMultiComponent(source: object, field: string, components: number): void {
  let seen = reportedMultiComponent.get(source);
  if (seen === undefined) {
    seen = new Set<string>();
    reportedMultiComponent.set(source, seen);
  }
  if (seen.has(field)) return;
  seen.add(field);
  console.warn(
    `patchTint: ${field} has ${components} components; reduce it with vertexValues before tinting. ` +
      "A tint is one byte per vertex, so a vector is drawn blank rather than coloured by a guess.",
  );
}

export function patchTint(
  values: FieldValues | null | undefined,
  range: Range,
  vertices: number,
  bands = SMOOTH,
  map?: ColormapId,
  reverse = false,
): Uint8Array | null {
  /*
    **The refusals are named, and the VECTOR one is logged.** A multi-component field reaching here is
    a caller that forgot to reduce it (`stokes`' `vertexValues` does), and this function used to answer
    `null` for it in silence - which is what cost three rounds of guessing on the slice work. The
    other two are ordinary "nothing to paint yet" and stay quiet.
  */
  if (values === null || values === undefined) return null;
  if (values.components !== 1) {
    // **Logged once per field**, because a redraw happens every frame and a per-frame log is noise.
    logMultiComponent(values, values.field, values.components);
    return null;
  }
  if (values.count !== vertices || values.values.length !== vertices) return null;
  return tintFor(values.values, range.min, range.max, bandCount(bands), map, reverse);
}

/** The key one field of one surface is cached under: a run's results are replaced as a whole (`seq`). */
export function fieldKey(seq: number, target: string, field: string): string {
  return `${seq}|${target}|${field}`;
}

/** The patches in the order their fields are fetched: the largest first (the surface the eye lands on), ties in tree order. */
export function fetchOrder<T extends { faces: number }>(entries: readonly T[]): T[] {
  return entries.map((entry, at) => ({ entry, at })).sort((a, b) => b.entry.faces - a.entry.faces || a.at - b.at).map((x) => x.entry);
}

/** The slice plane as the solver settings hold it, as text for the panel, or null when the settings carry none. */
export function slicePlaneText(settings: NodeSummary | null): { origin: string; normal: string } | null {
  const params = settings?.params as Record<string, unknown> | undefined;
  const show = (value: unknown): string => (Array.isArray(value) ? `(${value.map((v) => String(v)).join(", ")})` : typeof value === "string" ? value : "");
  const origin = show(params?.slice_origin);
  const normal = show(params?.slice_normal);
  return origin === "" && normal === "" ? null : { origin, normal };
}

// ---------------------------------------------------------------------------------------------- loading and dropping
/** A run whose result can be read: the solver finished, converged or not. */
export function isFinalRun(run: RunSummary): boolean {
  return run.state === "done" || run.state === "done_unconverged";
}

/** The newest run that has a result (`run.fields` defaults to the same one). */
export function runToShow(runs: readonly RunSummary[]): RunSummary | null {
  return runs.find(isFinalRun) ?? null;
}

/**
 * Whether the results of the newest finished run are to be read now, without being asked: the Results
 * stage is in front, that run is not the one held, no newer run is in flight and nothing is loading. A failed
 * load is not retried by this rule: a run's end or the Show results press asks again.
 */
export function shouldAutoLoad(stage: Stage, runs: readonly RunSummary[], live: ResultsLive, anyStage = false): boolean {
  if (live.status === "loading" || live.status === "error") return false;
  // A run in flight over an older result: that result is not read (it is about to be replaced).
  if (runs[0] !== undefined && isRunActive(runs[0].state)) return false;
  const run = runToShow(runs);
  if (run === null) return false;
  // **An imported case's mesh is read in any stage**: it is all a tutorial has to look at before it
  // is solved, so a project reopened on the Mesh or Run step showed an empty viewport until Results.
  // `anyStage`: the project has no mesh patches (an imported case), so the results slice is its mesh view.
  if (stage !== "Results" && !isMeshRun(run) && !anyStage) return false;
  return live.run !== run.id || live.data === null;
}

/**
 * Whether the held results are to be dropped: a new run has started over them (the newest run is in
 * flight and is not the held one), or the project has no runs at all. A finished run that failed or was
 * stopped leaves the older result in place: it is still that run's.
 */
export function shouldDrop(runs: readonly RunSummary[], live: ResultsLive): boolean {
  if (live.run === null && live.data === null) return false;
  const newest = runs[0];
  if (newest === undefined) return true;
  return isRunActive(newest.state) && newest.id !== live.run;
}

// ---------------------------------------------------------------------------------------------- reducers of the slice
export function loading(live: ResultsLive): ResultsLive {
  return { ...live, status: "loading", message: "" };
}

export function failed(live: ResultsLive, message: string): ResultsLive {
  return { ...live, status: "error", message };
}

/** The field a fresh result is shown with: the one chosen if the run has it, else `|U|`, else the first scalar. */
function chosenField(view: ResultsView, data: RunFields, first: boolean): string {
  const options = scalarFields(data);
  if (view.field !== NO_FIELD && options.some((o) => o.name === view.field)) return view.field;
  if (view.field === NO_FIELD && !first) return NO_FIELD;
  return options.find((o) => o.name === "U_mag")?.name ?? options[0]?.name ?? NO_FIELD;
}

/** `run.fields` has answered for a run. The first answer of a session (`seq` 0) turns `|U|` on; later ones keep the user's choice (including none). */
export function loaded(live: ResultsLive, data: RunFields): ResultsLive {
  const first = live.seq === 0;
  const field = chosenField(live.view, data, first);
  const lock = live.view.lock !== null && live.view.lock.field === field ? live.view.lock : null;
  const slice = live.view.slice && data.slice;
  // **The band count, the map and the legend's settings are the user's, not the data's**, so they survive a new run's arrival.
  return { view: { ...live.view, field, lock, slice }, run: data.run, status: "ready", message: "", data, seq: live.seq + 1 };
}

/** The held results are gone (the run was replaced, the project closed); the view settings stay. */
export function dropped(live: ResultsLive): ResultsLive {
  return live.data === null && live.run === null && live.status === "idle" ? live : { ...NO_RESULTS, view: live.view, seq: live.seq + 1 };
}

export function chooseField(live: ResultsLive, field: string): ResultsLive {
  if (field === live.view.field) return live;
  return { ...live, view: { ...live.view, field, lock: live.view.lock !== null && live.view.lock.field === field ? live.view.lock : null } };
}

export function setSlice(live: ResultsLive, on: boolean): ResultsLive {
  return live.view.slice === on ? live : { ...live, view: { ...live.view, slice: on } };
}

/** The mid-plane slice's cell edges on or off; the same object when nothing changes. */
export function setSliceEdges(live: ResultsLive, on: boolean): ResultsLive {
  return (live.view.sliceEdges === true) === on ? live : { ...live, view: { ...live.view, sliceEdges: on } };
}

export function setView(live: ResultsLive, view: ResultsView): ResultsLive {
  return view === live.view ? live : { ...live, view };
}

/** One line for the panel: what is loaded. */
export function loadedLine(live: ResultsLive): string {
  if (live.status === "loading") return "Reading the results";
  if (live.status === "error") return live.message !== "" ? live.message : "The results could not be read.";
  if (live.data === null || live.run === null) return "No results are loaded.";
  if (live.data.surfaces === "missing") return `Run ${live.run}: the mesh surfaces are missing on the server; generate the mesh again`;
  const names = scalarFields(live.data).map((o) => o.label);
  return `Run ${live.run} at ${live.data.time} · ${names.length > 0 ? names.join(", ") : "no fields"}${live.data.slice ? " and a mid-plane slice" : ""}`;
}

/*
  **Above 0 draws that many flat steps**, so a user can read a value off the model without reading the
  legend's gradient.
*/

/** The band counts the control offers, and the limit a caller may ask for. */
export const BAND_CHOICES = [SMOOTH, 4, 6, 8, 10, 12, 16] as const;
export const MAX_BANDS = 32;

/**
 * The band count actually used, from whatever the store holds.
 *
 * **Clamped rather than refused**: the value is view state with no journal record, and a bad one would
 * only be reachable through a crafted workspace document - where clamping to something drawable beats an
 * error the user cannot act on. `MAX_BANDS` is what keeps a nonsense number from being drawn.
 */
export function bandCount(bands: number | null | undefined): number {
  if (bands === null || bands === undefined || !Number.isFinite(bands)) return SMOOTH;
  const n = Math.trunc(bands);
  if (n <= SMOOTH) return SMOOTH;
  return Math.min(n, MAX_BANDS);
}

/**
 * The flat value a banded fill shows for a reading: the MIDDLE of the band the value falls in.
 *
 * **The middle, not the lower edge** (or "the value" would read as the band's floor and every value in a
 * band would look one step low), which is also what `bandColor` assumes when it colours a band.
 */
export function bandedValue(value: number, min: number, max: number, bands: number): number {
  const n = bandCount(bands);
  if (n === SMOOTH) return value;
  if (!(max > min)) return min;
  const band = Math.min(n - 1, Math.max(0, Math.floor(fraction(value, min, max) * n)));
  return min + ((band + 0.5) * (max - min)) / n;
}

/** The boundaries between `bands` bands, for a stepped legend: `bands + 1` values from min to max. */
export function bandEdges(min: number, max: number, bands: number): number[] {
  const n = bandCount(bands);
  if (n === SMOOTH) return [];
  const out: number[] = [];
  for (let i = 0; i <= n; i++) out.push(min + ((max - min) * i) / n);
  return out;
}

/**
 * The view with a different BAND count (results item 3).
 *
 * **Clamped, not refused**: `setBandCount` is view state, so a nonsense value would only reach it through
 * a crafted document - and the honest response is the nearest drawable count rather than a refusal the
 * user cannot act on.
 */
export function setBandCount(view: ResultsView, bands: number): ResultsView {
  return { ...view, bands: bandCount(bands) };
}

// ------------------------------------------------------------------------------ derived velocity components

/**
 * The velocity components the CLIENT can show from the `U` it already has.
 *
 * **Why derive rather than publish** (claude-win2's Ux question): the run stores `U` as a vector and the
 * slice already fetches it under decision (c), so `Ux`, `Uy` and `Uz` are **three views of bytes the
 * browser holds** - no new request, no agent change, no schema. Measured cost of the alternative: three
 * more arrays per cut, about 11 kB each.
 *
 * **They are named `U_x`/`U_y`/`U_z`** (which component, not a solver convention) and the label says
 * *"Ux"*, so a reader recognises them and a parser can tell them from a stored field.
 */
export const DERIVED_COMPONENTS: readonly { name: string; axis: 0 | 1 | 2; label: string }[] = [
  { name: "U_x", axis: 0, label: "Ux" },
  { name: "U_y", axis: 1, label: "Uy" },
  { name: "U_z", axis: 2, label: "Uz" },
];

/** The component a field name names, or null when it is not one of the derived ones. */
export function derivedComponentOf(name: string): 0 | 1 | 2 | null {
  return DERIVED_COMPONENTS.find((c) => c.name === name)?.axis ?? null;
}

/**
 * One component of a vector, per vertex - the array a component field would have had.
 *
 * **Signed, not a magnitude**: `Ux` is negative where the flow runs backwards, and a `Math.abs` here
 * would draw a recirculation as if it went forwards. That signs are kept is the whole reason to look at
 * a component instead of `|U|`.
 */
export function componentOf(values: FieldValues, axis: 0 | 1 | 2): Float32Array {
  const out = new Float32Array(values.count);
  for (let i = 0; i < values.count; i++) out[i] = values.values[i * 3 + axis]!;
  return out;
}

/** The range of a component, for a legend that has to say what it is showing. */
export function componentRange(values: FieldValues, axis: 0 | 1 | 2): Range {
  const array = componentOf(values, axis);
  let min = Infinity;
  let max = -Infinity;
  for (const v of array) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  return Number.isFinite(min) ? { min, max } : { min: 0, max: 0 };
}

/**
 * What a colour range was measured over, for the legend's second line - "" meaning "the run's own".
 *
 * **A component's range is LOCAL**: it comes from the samples of the cut being viewed, so two slices of
 * one run can legitimately show different legends. Without the label the two read identically and a user
 * comparing them would take a difference in what was sampled for a change in the physics.
 */
export function rangeScopeOf(field: string, locked: boolean): string {
  if (locked) return "range set by you";
  if (derivedComponentOf(field) !== null) return "over the values on this cut";
  return "";
}
