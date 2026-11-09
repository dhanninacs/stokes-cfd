/**
 * The list of slices of the Results stage, as pure functions: the planes the user cut the results with, how
 * each is drawn (fill, contour lines, bands, field) and what the server said about each cut. The slice list
 * lives in the store's `slices` field; the Slices section of the Results panel is a thin reader and writer of
 * it, and `viewport/sliceLayer.ts` cuts, fetches and draws what it lists.
 *
 * View state only: nothing here is a project command, nothing is journaled, nothing is saved in the project.
 * A slice is a plane (`state/slicePlane.ts`); where the plane came from (a world axis and a slider, or the
 * section plane at the moment it was copied) is kept only to label it and to give an axis slice its slider.
 *
 * Import rule: no React, no store, no DOM. Types only from other modules, so this loads in Node.
 */

import type { Plane } from "../state/slicePlane";
import { axisOfPlane, axisPlane, samePlane } from "../state/slicePlane";
import type { SectionAxis } from "../state/sectionState";

/** At most this many slices at once. */
export const MAX_SLICES = 8;
/** The bands of a banded fill, and the levels of the contour lines (N bands have N - 1 lines between them). */
export const MIN_LEVELS = 5;
export const MAX_LEVELS = 20;
export const DEFAULT_LEVELS = 10;
/** A slice's field is "the one the surfaces show" until the user picks its own. */
export const FOLLOW_FIELD = "follow";
/** How long a slider rests before its plane is asked of the server. */
export const SLICE_DEBOUNCE_MS = 300;
/** The opacity the walls fade to while slices are shown and the walls are faded. */
export const FADED_WALL_OPACITY = 0.12;

/** The scene source of one slice. Like the mid-plane slice's `mesh:slice` it is a mesh source beside the patches. */
export const USER_SLICE_PREFIX = "mesh:slice:";
/** The scene source of the section plane's cap. */
export const CAP_SOURCE = "mesh:slice-cap";

export type SliceOrigin = "axis" | "section";
export type LineColour = "map" | "neutral";

/** One slice: a plane and how it is drawn. */
export interface SliceSpec {
  id: string;
  origin: SliceOrigin;
  plane: Plane;
  visible: boolean;
  /** `FOLLOW_FIELD`, or the name of a field of the run. */
  field: string;
  /** Paint the slice with the field's colours. Off: the slice is a plain grey sheet (the lines still show). */
  fill: boolean;
  /**
   * Draw the cut's cell edges. **Off unless asked**: a figure wants the colours, and the edges drew as black
   * dashes through them (`scene.setSourceCellEdges` `own`: the Display menu's mesh mode does not decide this).
   */
  edges: boolean;
  /** Draw contour lines. */
  lines: boolean;
  /** Fill in `levels` flat bands instead of the smooth colour map. */
  bands: boolean;
  levels: number;
  lineColour: LineColour;
}

/** What the server said about the cut of one slice. */
export type SliceStatus = "loading" | "ready" | "empty" | "host" | "error";

export interface SliceState {
  status: SliceStatus;
  /** The failure in words (for `error`), or "". */
  message: string;
}

/** The `slices` field of the store. */
export interface SliceList {
  list: SliceSpec[];
  /** Draw the walls see-through while a slice is shown, so the slices inside the body can be seen. */
  fadeWalls: boolean;
  /** Per slice id (and `cap` for the section's cut): what the cut of the plane is doing. Written by the layer. */
  status: Record<string, SliceState>;
  /** The number the next slice id is made from. */
  counter: number;
}

export const NO_SLICES: SliceList = { list: [], fadeWalls: true, status: {}, counter: 0 };

/** The key of the section cap in `SliceList.status`. */
export const CAP_STATUS_KEY = "cap";

export function sliceSourceOf(id: string): string {
  return USER_SLICE_PREFIX + id;
}

function clampLevels(levels: number): number {
  if (!Number.isFinite(levels)) return DEFAULT_LEVELS;
  return Math.min(MAX_LEVELS, Math.max(MIN_LEVELS, Math.round(levels)));
}

/** Millimetres for a length in metres, as the panel and the section popover write them. */
export function formatMm(metres: number): string {
  const mm = Math.round(metres * 10000) / 10;
  return `${mm === 0 ? 0 : mm} mm`;
}

/** A name for a slice: its axis and position, or "oblique plane". */
export function sliceLabel(spec: Pick<SliceSpec, "plane" | "origin">): string {
  const found = axisOfPlane(spec.plane);
  const base = found === null ? "Oblique plane" : `${found.axis.toUpperCase()} = ${formatMm(found.position)}`;
  return spec.origin === "section" ? `Section ${base}` : base;
}

/** The scene patch name of a slice. */
export function sliceName(spec: Pick<SliceSpec, "plane" | "origin">): string {
  return `Slice ${sliceLabel(spec)} (mesh)`;
}

/** The field a slice is painted with, given the one the surfaces show. */
export function sliceFieldOf(spec: Pick<SliceSpec, "field">, surfaceField: string): string {
  return spec.field === FOLLOW_FIELD ? surfaceField : spec.field;
}

/**
 * The field a slice ASKS THE SERVER FOR, given the one it is painted with.
 *
 * **A speed is fetched as its VELOCITY** (claude-win2's decision c, 2026-10-22): `U_mag` and `U` are the
 * same motion, the header ranges `U` over its magnitude (`agent/stokes_agent/extract.py` takes `norm`
 * for `U`), and `vertexValues` reduces the three floats to the one number the colour needs. **One
 * request serves the colouring and the arrows**, instead of a second one for the directions.
 *
 * **Measured cost on the duct's slice**: 2761 points, so `U` is 33 132 bytes against `U_mag`'s
 * 11 044 - **22 088 more**, before gzip, on a payload the browser decodes in one pass.
 *
 * **Only the velocity is substituted.** Any other field is fetched exactly as named: the rule "do not
 * fetch what nobody asked for" still holds, and the vector is part of the field the user chose rather
 * than an extra request beside it.
 */
export function sliceRequestedFieldOf(painted: string): string {
  return painted === "U_mag" ? "U" : painted;
}

/** The slices that are drawn: the visible ones. */
export function visibleSlices(slices: SliceList): SliceSpec[] {
  return slices.list.filter((s) => s.visible);
}

/** True when the list is full. */
export function isFull(slices: SliceList): boolean {
  return slices.list.length >= MAX_SLICES;
}

/**
 * The list with a new slice on `plane`, shown, filled and smooth; the list itself when it is full. The id is
 * never reused in a session, so a status that arrives late for a removed slice finds nothing.
 */
export function addSlice(slices: SliceList, plane: Plane, origin: SliceOrigin = "axis"): SliceList {
  if (isFull(slices)) return slices;
  const counter = slices.counter + 1;
  const spec: SliceSpec = {
    id: `s${counter}`,
    origin,
    plane,
    visible: true,
    field: FOLLOW_FIELD,
    fill: true,
    edges: false,
    lines: false,
    bands: false,
    levels: DEFAULT_LEVELS,
    lineColour: "neutral",
  };
  return { ...slices, list: [...slices.list, spec], counter };
}

/** An axis slice at a position; the slider starts the plane there. */
export function addAxisSlice(slices: SliceList, axis: SectionAxis, position: number): SliceList {
  return addSlice(slices, axisPlane(axis, Number.isFinite(position) ? position : 0), "axis");
}

export function removeSlice(slices: SliceList, id: string): SliceList {
  if (!slices.list.some((s) => s.id === id)) return slices;
  const { [id]: _gone, ...status } = slices.status;
  return { ...slices, list: slices.list.filter((s) => s.id !== id), status };
}

export type SlicePatch = Partial<Pick<SliceSpec, "visible" | "field" | "fill" | "edges" | "lines" | "bands" | "levels" | "lineColour">>;

/** One slice's drawing settings changed; the list itself when nothing changes (so no reader wakes up for nothing). */
export function updateSlice(slices: SliceList, id: string, patch: SlicePatch): SliceList {
  let changed = false;
  const list = slices.list.map((s) => {
    if (s.id !== id) return s;
    const next: SliceSpec = { ...s, ...patch };
    if (patch.levels !== undefined) next.levels = clampLevels(patch.levels);
    const same = (Object.keys(next) as (keyof SliceSpec)[]).every((k) => k === "plane" || next[k] === s[k]);
    if (same) return s;
    changed = true;
    return next;
  });
  return changed ? { ...slices, list } : slices;
}

/** One slice moved to another plane (the slider). A plane the server would refuse, or the same plane, changes nothing. */
export function moveSlice(slices: SliceList, id: string, plane: Plane): SliceList {
  let changed = false;
  const list = slices.list.map((s) => {
    if (s.id !== id || samePlane(s.plane, plane)) return s;
    changed = true;
    return { ...s, plane };
  });
  return changed ? { ...slices, list } : slices;
}

export function setFadeWalls(slices: SliceList, on: boolean): SliceList {
  return slices.fadeWalls === on ? slices : { ...slices, fadeWalls: on };
}

/**
 * Whether the walls are drawn see-through: the "Fade the walls" setting, and something to see inside the body.
 * That is a slice of the list, the iso-surface, or **the run's mid-plane slice once its own cut is gone**
 * (`midBuried`). The mid-plane slice is normally exposed by the section its cut sets (`viewport/fields.ts`);
 * measured on pipe3 (2026-11-03, owner's report of a "camera jump"): with that section removed, the camera did
 * not move, but the near half of the pipe came back between the camera and the slice and its end face filled
 * the view. Fading the walls then keeps the slice the Looks card says is showing on screen.
 *
 * **`inside`: streamlines or volume arrows** (2026-11-04). They run through the whole fluid, so no section
 * can expose them all; the walls fade instead, as for an iso-surface, and the lines keep their depth.
 */
export function wallsFaded(input: { setting: boolean; slices: number; iso: boolean; midBuried: boolean; inside?: boolean }): boolean {
  return input.setting && (input.slices > 0 || input.iso || input.midBuried || input.inside === true);
}

/** What the cut of a slice is doing; the list itself when it is already so. */
export function setSliceStatus(slices: SliceList, key: string, state: SliceState): SliceList {
  const was = slices.status[key];
  if (was !== undefined && was.status === state.status && was.message === state.message) return slices;
  // A status for a slice that is gone is dropped, but the cap's is not a slice.
  if (key !== CAP_STATUS_KEY && !slices.list.some((s) => s.id === key)) return slices;
  return { ...slices, status: { ...slices.status, [key]: state } };
}

export function clearSliceStatus(slices: SliceList, key: string): SliceList {
  if (slices.status[key] === undefined) return slices;
  const { [key]: _gone, ...status } = slices.status;
  return { ...slices, status };
}

/** The words for what a cut is doing, or "" when there is nothing to say. */
export function statusText(state: SliceState | undefined): string {
  if (state === undefined) return "";
  switch (state.status) {
    case "loading":
      return "Cutting...";
    case "empty":
      return "The plane misses the model";
    case "host":
      return "This plane has not been cut yet and the run's host is not available";
    case "error":
      return state.message !== "" ? state.message : "The plane could not be cut";
    default:
      return "";
  }
}

/** The slider's range for an axis slice, in millimetres, widened to whole steps. `null` when the model has no extent. */
export function sliderRange(range: readonly [number, number] | null, stepMm = 0.5): { min: number; max: number } {
  if (range === null || !(range[1] >= range[0])) return { min: -500, max: 500 };
  return { min: Math.floor((range[0] * 1000) / stepMm) * stepMm, max: Math.ceil((range[1] * 1000) / stepMm) * stepMm };
}

/**
 * The line levels of a contour over a range: `levels` bands have `levels - 1` boundaries between them, evenly
 * spaced strictly inside the range. A range with no width has none.
 */
export function contourLevels(min: number, max: number, levels: number): number[] {
  const n = clampLevels(levels);
  if (!(max > min)) return [];
  const out: number[] = [];
  for (let k = 1; k < n; k++) out.push(min + ((max - min) * k) / n);
  return out;
}

// ---------------------------------------------------------------------------------------------- debouncing
/** A function that runs once its calls have rested for `ms`, with the last value it was called with. */
export interface Debounced<T> {
  call(value: T): void;
  /** Runs now, with the last value, if a call is waiting. */
  flush(): void;
  /** Drops the call that is waiting. */
  cancel(): void;
  readonly pending: boolean;
}

export function createDebounce<T>(fn: (value: T) => void, ms: number): Debounced<T> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let last: { value: T } | null = null;
  const run = (): void => {
    timer = null;
    const call = last;
    last = null;
    if (call !== null) fn(call.value);
  };
  return {
    call(value: T): void {
      last = { value };
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(run, ms);
    },
    flush(): void {
      if (timer !== null) clearTimeout(timer);
      run();
    },
    cancel(): void {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      last = null;
    },
    get pending(): boolean {
      return last !== null;
    },
  };
}
