/**
 * Saved views as a model (R4 of the results overhaul): what a view holds, how it is made from the figure on
 * screen, and what applying one changes. Pure functions, so every rule is tested in Node;
 * `viewsControl.ts` reads and writes the stores and the camera, `viewsApi.ts` talks to the server, and the
 * card's menu (`guided/ViewsMenu.tsx`) draws the list.
 *
 * **A saved view IS the image export's sidecar plus a name** (`exportModel.buildSidecar`: camera, field, range,
 * map, reverse, bands, legend, and the settings of each look), so a `.json` an export wrote re-applies as a
 * view and a view's record could be handed to the export as it stands. The sidecar leaves out a few things a
 * figure needs to be made again (the run's own mid-plane slice, a slice's edges and origin, the section, the
 * streamline seeds, the walls' fade); those travel in `stokes.extras`, which is optional, so a plain sidecar
 * applies too, leaving what it does not say as it is. The camera's `up` vector is an optional addition to the
 * sidecar's camera for the same reason (a face-on view may turn it).
 *
 * **Reading is tolerant, as `legendOf` is**: a record from an older build, a hand-edited file or a field of
 * another type gives the default or leaves the setting alone, never an exception. **Applying never invents**:
 * a field the run does not have is skipped and named, the rest of the view is applied.
 *
 * Import rule: no React, no store, no DOM. Types only from other modules' state, so this loads in Node.
 */

import type { SectionPlane } from "../state/sectionState";
import { anyLoading, type LoadingCounts } from "../state/loadingState";
import { DEFAULT_COLORMAP, isColormapId, type ColormapId } from "../state/colormap";
import { samePlane, type Plane } from "../state/slicePlane";
import { DEFAULT_EXPORT, buildSidecar, safeNamePart, type ExportCamera, type ExportSettings, type ExportView, type Sidecar } from "./exportModel";
import { NO_FIELD, scalarFields, type ResultsLive, type ResultsView } from "./fieldsModel";
import { legendOf, type LegendPlacement, type RangeMode, type TickFormat } from "./legendModel";
import type { IsoView } from "./isoModel";
import type { LookAction, SeedKind } from "./lookModel";
import { FOLLOW_FIELD, MAX_SLICES, addSlice, setFadeWalls, updateSlice, type SliceList, type SliceOrigin, type SliceSpec } from "./sliceModel";
import type { StreamlineState } from "./streamlineModel";
import { DEFAULT_LINE_POINTS, MAX_LINE_POINTS, NO_LINE, type LineState, type Vec3 } from "./lineModel";

// ---------------------------------------------------------------------------------------------- the name
/** The longest name: the server's (`stokes/model/views.py` `MAX_NAME`). */
export const MAX_VIEW_NAME = 80;

/**
 * Why a name cannot be saved, in a few words, or null when it can. The server checks the same rules
 * (`view.name_invalid`); this is for the prompt, so the user sees it before the round trip.
 */
export function viewNameProblem(name: string): string | null {
  const text = name.trim();
  if (text === "") return "Name it";
  if (text.length > MAX_VIEW_NAME) return `At most ${MAX_VIEW_NAME} characters`;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(text)) return "No line breaks";
  return null;
}

/** The name as it is saved: trimmed. */
export function cleanViewName(name: string): string {
  return name.trim();
}

// ---------------------------------------------------------------------------------------------- the record
/** The seed settings of the streamlines on screen: the card's choice, which no store holds. */
export interface StreamExtras {
  seeds: SeedKind;
  count: number;
}

/** What the sidecar leaves out and a view needs. */
export interface ViewExtras {
  /** The run's own mid-plane slice is on. */
  midSlice: boolean;
  /** Its cell edges are drawn. */
  sliceEdges: boolean;
  /** The walls are drawn see-through while a slice shows. */
  fadeWalls: boolean;
  /** The visible slices of the list, in full (the sidecar's list leaves out `origin`, `edges` and `visible`). */
  slices: SliceSpec[];
  /** The user's section (Clip). Null when a slice's own cut is the only one, or none. */
  section: SectionPlane | null;
  /** The streamlines' seeds, when lines are drawn. */
  stream: StreamExtras | null;
  /** The plot line's ends and surface, when one is on screen (R5b); absent otherwise, as in an older record. */
  line?: LineSpec;
}

/** The plot line a view keeps: its two ends, the sample count and the surface it is read on (`"slice"` or a patch id). */
export interface LineSpec {
  a: [number, number, number];
  b: [number, number, number];
  n: number;
  patch: string;
}

/** The line of the store as the record keeps it: `ready`, or `preparing` with both ends; null for anything else (picking, outside, failed, none). */
export function lineSpecOf(line: LineState): LineSpec | null {
  if (line.phase !== "ready" && line.phase !== "preparing") return null;
  if (line.a === null || line.b === null || line.patch === null || line.patch === "") return null;
  const ends = [...line.a, ...line.b];
  if (!ends.every(Number.isFinite)) return null;
  return { a: [line.a[0], line.a[1], line.a[2]], b: [line.b[0], line.b[1], line.b[2]], n: line.n, patch: line.patch };
}

/** A line read out of a record: both ends finite, a surface named; the count is clamped to what the server takes. Null when the record has no usable line. */
export function lineSpecOfRaw(raw: unknown): LineSpec | null {
  if (!isRecord(raw) || typeof raw.patch !== "string" || raw.patch === "") return null;
  const a = vec3(raw.a);
  const b = vec3(raw.b);
  if (a === null || b === null) return null;
  const n = num(raw.n);
  return { a, b, n: n === null ? DEFAULT_LINE_POINTS : Math.min(MAX_LINE_POINTS, Math.max(2, Math.round(n))), patch: raw.patch };
}

/** The store's line for a view that has one: both ends and the surface set, asking for the values (`preparing`; `askLine` fills the rest). The count is the record's. */
export function lineStateOf(spec: LineSpec): LineState {
  const ends: [Vec3, Vec3] = [[...spec.a], [...spec.b]];
  return { ...NO_LINE, a: ends[0], b: ends[1], n: spec.n, patch: spec.patch, phase: "preparing" };
}

/** The record a view holds: a sidecar with the extras. */
export type ViewRecord = Sidecar & { stokes: { extras?: ViewExtras } };

/** What a figure on screen is made of, as the controller reads it. */
export interface ViewSource {
  view: ExportView;
  camera: ExportCamera;
  /** The window's size, for the sidecar's `image` entry (a view has no picture of its own). */
  pixels: { w: number; h: number };
  slices: SliceList;
  /** The section in the store and whether the user chose it (no slice is showing, so it is not a slice's own cut). */
  section: SectionPlane | null;
  stream: StreamExtras | null;
  /** The plot line in the store, when the caller has one to keep (`lineSpecOf` decides whether it is on screen). */
  line?: LineState;
}

function finite(n: number): number {
  return Number.isFinite(n) ? n : 0;
}

function sectionOf(section: SectionPlane | null): SectionPlane | null {
  return section === null ? null : { axis: section.axis, offset: finite(section.offset), invert: section.invert };
}

/**
 * The record of the figure on screen: `buildSidecar` with the default export settings (the picture is not part
 * of a view) and the extras. **The section is kept only when no slice is showing**: while one is, the section in
 * the store is that slice's own cut (`viewport/fields.ts` engages it), and recording it as the user's would give
 * it back as theirs the day the slice goes.
 */
export function recordOf(source: ViewSource): ViewRecord {
  const sidecar = buildSidecar(source.view, source.camera, { ...DEFAULT_EXPORT, legend: true }, source.pixels);
  const { view } = source.view;
  const shown = source.slices.list.filter((s) => s.visible);
  const sliceShown = view.slice || shown.length > 0;
  const extras: ViewExtras = {
    midSlice: view.slice,
    sliceEdges: view.sliceEdges === true,
    fadeWalls: source.slices.fadeWalls,
    slices: shown.map((s) => ({ ...s, plane: { origin: [...s.plane.origin] as Plane["origin"], normal: [...s.plane.normal] as Plane["normal"] } })),
    section: sliceShown ? null : sectionOf(source.section),
    stream: source.view.streamlines ? source.stream : null,
  };
  const line = source.line === undefined ? null : lineSpecOf(source.line);
  if (line !== null) extras.line = line;
  return { ...sidecar, stokes: { ...sidecar.stokes, extras } };
}

// ---------------------------------------------------------------------------------------------- reading
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function vec3(value: unknown): [number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 3) return null;
  const [x, y, z] = value.map(num);
  return x === null || y === null || z === null ? null : [x, y, z];
}

/** The camera of a record, or null when it has none that can be used (a direction of zero length, a span that is not positive). */
export function cameraOfRecord(raw: unknown): ExportCamera | null {
  if (!isRecord(raw)) return null;
  const target = vec3(raw.target);
  const dir = vec3(raw.dir);
  const span = num(raw.span);
  if (target === null || dir === null || span === null || span <= 0) return null;
  if (dir[0] * dir[0] + dir[1] * dir[1] + dir[2] * dir[2] < 1e-12) return null;
  const up = vec3(raw.up);
  return { target, dir, span, orthographic: raw.orthographic === true, ...(up === null ? {} : { up }) };
}

function planeOf(raw: unknown): Plane | null {
  if (!isRecord(raw)) return null;
  const origin = vec3(raw.origin);
  const normal = vec3(raw.normal);
  if (origin === null || normal === null) return null;
  return normal[0] === 0 && normal[1] === 0 && normal[2] === 0 ? null : { origin, normal };
}

function sliceOf(raw: unknown): SliceSpec | null {
  if (!isRecord(raw)) return null;
  const plane = planeOf(raw.plane);
  if (plane === null) return null;
  const origin: SliceOrigin = raw.origin === "section" ? "section" : "axis";
  return {
    id: typeof raw.id === "string" ? raw.id : "",
    origin,
    plane,
    visible: true,
    field: typeof raw.field === "string" && raw.field !== "" ? raw.field : FOLLOW_FIELD,
    fill: raw.fill !== false,
    edges: raw.edges === true,
    lines: raw.lines === true,
    bands: raw.bands === true,
    levels: num(raw.levels) ?? 10,
    lineColour: raw.lineColour === "map" ? "map" : "neutral",
  };
}

function sectionOfRaw(raw: unknown): SectionPlane | null {
  if (!isRecord(raw)) return null;
  const offset = num(raw.offset);
  if (offset === null || (raw.axis !== "x" && raw.axis !== "y" && raw.axis !== "z")) return null;
  return { axis: raw.axis, offset, invert: raw.invert === true };
}

/** What a record says, every part checked; `null` where it says nothing, so applying leaves that setting alone. */
export interface ParsedView {
  camera: ExportCamera | null;
  /** The field name; `none` for no colouring; null when the record names none. */
  field: string | null;
  /** The range fixed for the field, or null for the run's own (auto). */
  range: { min: number; max: number } | null;
  rangeMode: RangeMode;
  fullRange: boolean;
  map: ColormapId;
  reverse: boolean;
  bands: number;
  format: TickFormat;
  digits: number;
  placement: LegendPlacement;
  slices: SliceSpec[];
  /** Null: the record does not say (a plain sidecar), leave the run's own slice as it is. */
  midSlice: boolean | null;
  sliceEdges: boolean | null;
  fadeWalls: boolean | null;
  /** Null with `hasExtras` false: not said. */
  section: SectionPlane | null;
  hasExtras: boolean;
  /** The plot line the record keeps; null for a record without one (applying it clears the line). */
  line: LineSpec | null;
  iso: { field: string; value: number } | null;
  streamlines: boolean;
  stream: StreamExtras;
  arrows: { density: number; scale: number } | null;
}

const SEEDS: readonly SeedKind[] = ["line", "plane", "rake", "sphere", "inlet"];

/** A record read into what it says. `null` for something that is not a view (not an object, no `stokes` part). */
export function readRecord(raw: unknown): ParsedView | null {
  if (!isRecord(raw) || !isRecord(raw.stokes)) return null;
  const s = raw.stokes;
  const field = isRecord(s.field) && typeof s.field.name === "string" && s.field.name !== "" ? s.field.name : null;
  const rangeRaw = isRecord(s.range) ? s.range : null;
  const lo = rangeRaw === null ? null : num(rangeRaw.min);
  const hi = rangeRaw === null ? null : num(rangeRaw.max);
  const fixed = rangeRaw !== null && rangeRaw.locked === true && lo !== null && hi !== null && lo < hi;
  const legend = isRecord(s.legend) ? s.legend : {};
  const looks = isRecord(s.looks) ? s.looks : {};
  const settings = legendOf({
    map: isColormapId(s.map) ? s.map : DEFAULT_COLORMAP,
    reverse: s.reverse === true,
    bands: num(s.bands) ?? 0,
    rangeMode: legend.rangeMode as RangeMode,
    fullRange: legend.fullRange === true,
    format: legend.format as TickFormat,
    digits: num(legend.digits) ?? undefined,
    placement: legend.placement as LegendPlacement,
  });
  const extras = isRecord(s.extras) ? s.extras : null;
  const listed = Array.isArray(extras?.slices) ? extras.slices : Array.isArray(looks.slices) ? looks.slices : [];
  const slices = listed.map(sliceOf).filter((x): x is SliceSpec => x !== null);
  const isoRaw = isRecord(looks.iso) ? looks.iso : null;
  const isoValue = isoRaw === null ? null : num(isoRaw.value);
  const arrowsRaw = isRecord(looks.arrows) ? looks.arrows : null;
  const density = arrowsRaw === null ? null : num(arrowsRaw.density);
  const scale = arrowsRaw === null ? null : num(arrowsRaw.scale);
  const streamRaw = extras !== null && isRecord(extras.stream) ? extras.stream : null;
  const seeds = streamRaw !== null && SEEDS.includes(streamRaw.seeds as SeedKind) ? (streamRaw.seeds as SeedKind) : "line";
  const count = streamRaw === null ? null : num(streamRaw.count);
  return {
    camera: cameraOfRecord(s.camera),
    field,
    range: fixed ? { min: lo, max: hi } : null,
    rangeMode: settings.rangeMode,
    fullRange: settings.fullRange,
    map: settings.map,
    reverse: settings.reverse,
    bands: settings.bands,
    format: settings.format,
    digits: settings.digits,
    placement: settings.placement,
    slices,
    midSlice: extras !== null && typeof extras.midSlice === "boolean" ? extras.midSlice : null,
    sliceEdges: extras !== null && typeof extras.sliceEdges === "boolean" ? extras.sliceEdges : null,
    fadeWalls: extras !== null && typeof extras.fadeWalls === "boolean" ? extras.fadeWalls : null,
    section: extras === null ? null : sectionOfRaw(extras.section),
    hasExtras: extras !== null,
    line: extras === null ? null : lineSpecOfRaw(extras.line),
    iso: isoRaw !== null && typeof isoRaw.field === "string" && isoRaw.field !== "" && isoValue !== null ? { field: isoRaw.field, value: isoValue } : null,
    streamlines: looks.streamlines === true,
    stream: { seeds, count: count === null ? 50 : Math.max(1, Math.round(count)) },
    arrows: density !== null && scale !== null ? { density, scale } : null,
  };
}

/** The streamline seeds a record names, when it has streamlines; null otherwise (the card keeps its own). */
export function streamOfRecord(raw: unknown): StreamExtras | null {
  const parsed = readRecord(raw);
  return parsed !== null && parsed.streamlines ? parsed.stream : null;
}

// ---------------------------------------------------------------------------------------------- applying
/** What applying reads of the stores. */
export interface ApplyInput {
  results: ResultsLive;
  slices: SliceList;
  section: SectionPlane | null;
}

/** What applying changes: the store writes, the actions of the Looks controller, the camera, and what was left out. */
export interface ViewApplication {
  /** The three store fields applying writes in ONE `setState`, so the slice's cut engages on a whole state. */
  patch: { results: ResultsLive; slices: SliceList; section: SectionPlane | null };
  /** Forget the section the slice's cut remembered, BEFORE the write (`fields.ts` `forgetSavedSection`). */
  forgetSection: boolean;
  /** The Looks controller's actions: iso and streamlines (asks), arrows, the projection. Run after the write. */
  actions: LookAction[];
  /** The camera to restore once the picture is drawn, or null. */
  camera: ExportCamera | null;
  /** What the view says that this run cannot give, in a few words each (`No U_x in this run`). */
  skipped: string[];
  /** The field the view colours by that this run does not have, or null: the picture would show the field that was on screen. */
  missingField: string | null;
  /** The plot line to put on screen (asked again by the caller), or null: the line is cleared, so a view never shows another view's line. */
  line: LineState | null;
  /** The iso value and streamline ask the settle wait looks for. */
  expect: { iso: { field: string; value: number } | null; streamlines: boolean };
}

/** The slices of the list after a view: those the record names shown (reusing a slice already on that plane), the others hidden, not removed (Full mode keeps them). */
export function applySlices(list: SliceList, wanted: readonly SliceSpec[]): { slices: SliceList; dropped: number } {
  let slices = list;
  for (const s of list.list) if (s.visible) slices = updateSlice(slices, s.id, { visible: false });
  let dropped = 0;
  const used = new Set<string>();
  for (const spec of wanted) {
    const found = slices.list.find((s) => !used.has(s.id) && s.origin === spec.origin && samePlane(s.plane, spec.plane));
    let id = found?.id ?? null;
    if (id === null) {
      if (slices.list.length >= MAX_SLICES) {
        dropped++;
        continue;
      }
      const added = addSlice(slices, spec.plane, spec.origin);
      id = added.list[added.list.length - 1]?.id ?? null;
      slices = added;
    }
    if (id === null) continue;
    used.add(id);
    slices = updateSlice(slices, id, { visible: true, field: spec.field, fill: spec.fill, edges: spec.edges, lines: spec.lines, bands: spec.bands, levels: spec.levels, lineColour: spec.lineColour });
  }
  return { slices, dropped };
}

function viewOf(input: ApplyInput, parsed: ParsedView, skipped: string[]): ResultsView {
  const old = input.results.view;
  const names = scalarFields(input.results.data).map((o) => o.name);
  let field = old.field;
  if (parsed.field !== null) {
    if (parsed.field === NO_FIELD || names.includes(parsed.field)) field = parsed.field;
    else skipped.push(`No ${parsed.field} in this run`);
  }
  const colouring = field !== NO_FIELD;
  // The range belongs to the record's field: with another field in force (the record's was skipped) it is the run's own.
  const fixed = parsed.range !== null && colouring && field === parsed.field;
  const rangeMode: RangeMode = fixed ? (parsed.rangeMode === "manual" ? "manual" : "locked") : "auto";
  const next: ResultsView = {
    ...old,
    field,
    lock: fixed && parsed.range !== null ? { field, min: parsed.range.min, max: parsed.range.max } : null,
    rangeMode,
    fullRange: parsed.fullRange,
    map: parsed.map,
    reverse: parsed.reverse,
    bands: parsed.bands,
    format: parsed.format,
    digits: parsed.digits,
    placement: parsed.placement,
    slice: parsed.midSlice ?? old.slice,
    ...(parsed.sliceEdges === null ? {} : { sliceEdges: parsed.sliceEdges }),
  };
  return next;
}

/**
 * What applying a view does, as data. **Everything the record says is set; what it does not say is left**: a plain
 * sidecar (no extras) leaves the run's own slice, the section and the fade alone, and sets the slices, field,
 * range, map, bands, legend, iso, streamlines, arrows and the camera it names.
 *
 * The section: with extras, the record's own when it has one; none when it shows no slice (and the saved section is
 * forgotten); a slice's engaged cut is left to the slice (`fields.ts`).
 */
export function applyView(parsed: ParsedView, input: ApplyInput): ViewApplication {
  const skipped: string[] = [];
  const view = viewOf(input, parsed, skipped);
  const results: ResultsLive = { ...input.results, view };
  const { slices: moved, dropped } = applySlices(input.slices, parsed.slices);
  if (dropped > 0) skipped.push(`${dropped} slice${dropped === 1 ? "" : "s"} over the limit`);
  const slices = parsed.fadeWalls === null ? moved : setFadeWalls(moved, parsed.fadeWalls);
  const anySlice = view.slice || parsed.slices.length > 0;
  let section = input.section;
  let forgetSection = false;
  if (parsed.hasExtras) {
    if (parsed.section !== null) {
      section = parsed.section;
      forgetSection = true;
    } else if (!anySlice) {
      section = null;
      forgetSection = true;
    }
  }
  const actions: LookAction[] = [];
  const names = scalarFields(input.results.data).map((o) => o.name);
  const isoRun = parsed.iso !== null && names.includes(parsed.iso.field);
  if (parsed.iso !== null && isoRun) actions.push({ kind: "askIso", field: parsed.iso.field, value: parsed.iso.value });
  else {
    actions.push({ kind: "hideIso" });
    if (parsed.iso !== null) skipped.push(`No ${parsed.iso.field} for the iso`);
  }
  if (parsed.streamlines) actions.push({ kind: "askStreamlines", seeds: parsed.stream.seeds, count: parsed.stream.count, tubes: false });
  else actions.push({ kind: "clearStreamlines" });
  actions.push(parsed.arrows === null ? { kind: "arrows", shown: false } : { kind: "arrows", shown: true, density: parsed.arrows.density, scale: parsed.arrows.scale });
  if (parsed.camera !== null) actions.push({ kind: "orthographic", on: parsed.camera.orthographic });
  return {
    patch: { results, slices, section },
    forgetSection,
    actions,
    camera: parsed.camera,
    line: parsed.line === null ? null : lineStateOf(parsed.line),
    skipped,
    missingField: parsed.field !== null && parsed.field !== NO_FIELD && view.field !== parsed.field ? parsed.field : null,
    expect: { iso: parsed.iso !== null && isoRun ? parsed.iso : null, streamlines: parsed.streamlines },
  };
}

// ---------------------------------------------------------------------------------------------- settling
/** The stores a settle test reads. */
export interface SettleInput {
  results: ResultsLive;
  slices: SliceList;
  iso: IsoView;
  streamlines: StreamlineState;
  /** The field and cut requests on the way (`state/loadingState.ts`). */
  loading: LoadingCounts;
  /** The plot line: waiting for its values (`preparing`) is not settled; ready, failed, outside and none are. */
  line: LineState;
}

/** Two iso values are the same surface when they agree to a part in a million of the larger. */
function sameValue(a: number, b: number): boolean {
  return Math.abs(a - b) <= 1e-6 * Math.max(Math.abs(a), Math.abs(b), 1e-12);
}

/**
 * Whether the picture has what the view asked for: the results are read, no slice cut is loading, **no field value
 * is on the way** (the walls' and the slices' colours: a cut already cached says `ready` while the values that paint
 * it are still coming, and the picture would have grey walls), the iso the view
 * asked for is drawn (a refused one never will be, and the wait's own limit ends that), streamlines are not
 * computing and, when the view has them, there are lines or the ask failed. **The caller waits for the layers'
 * debounce first** (`SETTLE_DELAY_MS`): a slice moved a moment ago still says `ready` for the plane it left.
 */
export function viewSettled(state: SettleInput, expect: ViewApplication["expect"]): boolean {
  if (state.results.status === "loading") return false;
  if (anyLoading(state.loading)) return false;
  if (state.line.phase === "preparing") return false;
  for (const s of state.slices.list) if (s.visible && state.slices.status[s.id]?.status === "loading") return false;
  if (state.slices.status.cap?.status === "loading") return false;
  if (expect.iso !== null) {
    const iso = state.iso.surface;
    if (!state.iso.visible || iso === null || iso.state !== "ready" || iso.field !== expect.iso.field || !sameValue(iso.value, expect.iso.value)) return false;
  }
  if (state.streamlines.phase === "computing") return false;
  if (expect.streamlines && state.streamlines.phase !== "ready" && state.streamlines.phase !== "failed") return false;
  return true;
}

/** How long a view waits before it asks whether the layers have settled (slice debounce 300 ms, plus the first frame), and for how long at most. */
export const SETTLE_DELAY_MS = 450;
export const SETTLE_POLL_MS = 100;
export const SETTLE_MAX_MS = 20_000;

// ---------------------------------------------------------------------------------------------- the camera
/** World Y up: the camera's up vector when a record does not say (`CameraRig.fitState`'s default for a new direction). */
export const DEFAULT_UP: [number, number, number] = [0, 1, 0];

/** The up vector a camera is restored with: the record's, else the default, so an older record applied after a face-on view does not keep the turned up. */
export function cameraUp(camera: ExportCamera): [number, number, number] {
  return camera.up === undefined ? [...DEFAULT_UP] : camera.up;
}

// ---------------------------------------------------------------------------------------------- export all
/**
 * The files "Export all" writes, one per view, `<project>-<run>-<view name>.png` with safe characters. **Two names are
 * the same file when their stems agree in lower case** (Windows does not tell `Front` from `front`), and a suffix is
 * added (`-2`, `-3`...) until the stem is unused, so a generated `X-2` never lands on a real view called `X-2` either.
 */
export function exportAllNames(parts: { project: string; run: string }, names: readonly string[]): string[] {
  const used = new Set<string>();
  return names.map((name) => {
    const stem = [safeNamePart(parts.project) || "stokes", safeNamePart(parts.run), safeNamePart(name, 60) || "view"].filter((p) => p !== "").join("-");
    let unique = stem;
    for (let n = 2; used.has(unique.toLowerCase()); n++) unique = `${stem}-${n}`;
    used.add(unique.toLowerCase());
    return `${unique}.png`;
  });
}

/** The export settings "Export all" uses: the dialog's remembered ones, else the defaults; never a title (it would name every figure alike). */
export function exportAllSettings(remembered: ExportSettings): ExportSettings {
  return { ...remembered, title: "" };
}
