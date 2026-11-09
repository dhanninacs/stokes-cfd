/**
 * The guided Results card's LOOKS (R2 of the results overhaul), as pure functions: the six ways the card shows a
 * field (Surface, Slice, Iso, Stream, Vectors, Clip), which one is showing, what each one's two or three
 * controls are, and what pressing a tile or a control does to the stores that already exist. No new state: a
 * look is a pattern in `results.view.slice`, `slices`, `iso`, `streamlines`, `arrows` and `section`, so Full
 * mode's sections keep working and show the same thing the card does.
 *
 * **Three ideas carry the file**
 *
 * * `activeLook` READS the stores. The card keeps no "current look" of its own, so a slice added from the
 *   Results panel lights the Slice tile and a Clear in the panel puts Surface back.
 * * A tile or a control gives ACTIONS AS DATA (`LookAction`), and `applyLookActions` is a pure reducer over the
 *   subset of the store the looks touch. The controller (`guided/looksControl.ts`) writes the result and runs the
 *   requests an answer cannot be computed for (an iso-surface, streamlines: both ask the server). That split is
 *   why every rule below is tested in Node and the card has none of its own.
 * * **Stream and Vectors fill the VOLUME** (2026-11-04, the owner's "stream lines and vectors are not
 *   volumetric"): the lines are traced through the solved 3-D mesh from every inlet, and the arrows are sampled on
 *   an even lattice through the fluid (`run.vectors`). Neither stands on a slice any more - a slice's cut exposed
 *   one plane and clipped every line on the near side of it away - so both take the slices off and the walls fade
 *   instead (`wallsFaded`'s `inside`). Vectors can still be put on a slice (its Where control), and then it
 *   outranks the slice in the derivation as before: a slice with arrows on it is Vectors, a slice alone is Slice.
 *
 * View state only: nothing here is a project command, nothing is journaled. Import rule: no React, no store, no
 * DOM; types only from other modules' state, so this loads in Node.
 */

import type { SectionAxis, SectionPlane } from "../state/sectionState";
import { sectionAt } from "../state/sectionState";
import { axisOfPlane, axisPlane } from "../state/slicePlane";
import { ARROW_DENSITIES, VELOCITY_MAGNITUDE, isVectorFieldName, volumeArrowsShown, volumeCountOf, type ArrowPlace, type ArrowView } from "./arrowModel";
import { chooseField, setSlice, type ResultsLive } from "./fieldsModel";
import { ISO_CODES, hideIso, isoFields, type IsoField, type IsoView } from "./isoModel";
import type { RunFields } from "./resultsApi";
import { MAX_SLICES, SLICE_DEBOUNCE_MS, addAxisSlice, formatMm, isFull, moveSlice, updateSlice, type SliceList } from "./sliceModel";
import type { StreamlineState } from "./streamlineModel";
import { NO_STREAMLINES } from "./streamlineModel";
import { directionVector } from "../viewport/viewDirections";

// ---------------------------------------------------------------------------------------------- the looks
export type LookId = "surface" | "slice" | "iso" | "stream" | "vectors" | "clip";

export interface LookTile {
  id: LookId;
  /** One word: the sentence is the tooltip. */
  label: string;
  tip: string;
}

/** The tiles, in the order the card draws them. */
export const LOOKS: readonly LookTile[] = [
  { id: "surface", label: "Surface", tip: "Colour the walls with the field" },
  { id: "slice", label: "Slice", tip: "Cut through the flow and colour the cut" },
  { id: "iso", label: "Iso", tip: "A surface where the field has one value" },
  { id: "stream", label: "Stream", tip: "Lines that follow the flow" },
  { id: "vectors", label: "Vectors", tip: "Arrows for the direction of the flow" },
  { id: "clip", label: "Clip", tip: "Cut half the model away and see inside" },
];

export type Vec3 = readonly [number, number, number];
export interface Box {
  min: Vec3;
  max: Vec3;
}

/** The parts of the store the looks read and write. The controller builds it from the store and writes back what changed. */
export interface LookState {
  results: ResultsLive;
  slices: SliceList;
  iso: IsoView;
  streamlines: StreamlineState;
  arrows: ArrowView;
  section: SectionPlane | null;
  orthographic: boolean;
  hiddenNodes: readonly string[];
}

/** What the looks need to know that is not in the stores: the run, the model's box, the run's own slice plane, the inlet. */
export interface LookContext {
  data: RunFields | null;
  /** The model's box in metres, or null while the scene draws nothing. */
  bounds: Box | null;
  /**
   * The plane of the run's own mid-plane slice, or null when it has none that lies along an axis. The controller
   * reads it from the solver settings and, for the default plane (which the settings leave empty), from the flat
   * axis of the slice in the scene, so the Axis row lights the chip of the plane on screen.
   */
  midPlane: { axis: SectionAxis; offset: number } | null;
  /** The case has an inlet whose patch is in the scene, so Stream can seed from it. */
  hasInlet: boolean;
  /** Whether two points are picked with the Line tool, which a rake seeds between. */
  hasRake: boolean;
}

/** What the card remembers for the controls that no store holds (the iso value before a surface exists, the seeds). */
export interface LookPrefs {
  /** Where in the field's range the iso value sits, 0 to 1. */
  isoFraction: number;
  streamSeeds: SeedKind;
  streamCount: number;
  /** Draw the lines as tubes (a width in metres) rather than hairlines (a width in pixels). */
  streamTubes: boolean;
}

/*
  **Inlet seeds by default** (2026-11-04): the lines start where the flow enters, on every inlet, spread over the
  inlet faces by the host. The old default, a row across the model's BOX on the mid-plane, missed the fluid
  wherever the model does not fill its box: on a mixing elbow most of the row lay outside the pipes and the
  second inlet was never seeded at all.
*/
export const DEFAULT_PREFS: LookPrefs = { isoFraction: 0.5, streamSeeds: "inlet", streamCount: 50, streamTubes: false };

/** The seed kind an ask uses: Inlet falls back to Line when the case has no inlet in the scene. */
export function seedKindFor(kind: SeedKind, hasInlet: boolean): SeedKind {
  return kind === "inlet" && !hasInlet ? "line" : kind;
}

// ---------------------------------------------------------------------------------------------- geometry
const AXES: readonly SectionAxis[] = ["x", "y", "z"];

function indexOf(axis: SectionAxis): 0 | 1 | 2 {
  return AXES.indexOf(axis) as 0 | 1 | 2;
}

/** The box's extent along an axis, or null for no box. */
export function axisSpan(bounds: Box | null, axis: SectionAxis): [number, number] | null {
  if (bounds === null) return null;
  const i = indexOf(axis);
  return [bounds.min[i], bounds.max[i]];
}

/** The axis the box is thinnest along; a tie goes to z, then y (a 2-D mesh is almost always thin in z). Z when there is no box. */
export function shortestAxis(bounds: Box | null): SectionAxis {
  if (bounds === null) return "z";
  let best: SectionAxis = "z";
  let size = bounds.max[2] - bounds.min[2];
  for (const axis of ["y", "x"] as const) {
    const s = bounds.max[indexOf(axis)] - bounds.min[indexOf(axis)];
    if (s < size) {
      best = axis;
      size = s;
    }
  }
  return best;
}

/** Shortest extent over longest, 0 to 1; 1 for no box or a box with no size (nothing is called flat for want of data). */
export function flatness(bounds: Box | null): number {
  if (bounds === null) return 1;
  const sizes = [0, 1, 2].map((i) => bounds.max[i] - bounds.min[i]);
  const longest = Math.max(...sizes);
  return longest > 0 ? Math.min(...sizes) / longest : 1;
}

/** Below this the mesh is a sheet: the camera goes orthographic (plan §3.7). */
export const THIN_RATIO = 0.05;

/** Where along an axis an offset sits, 0 to 1, clamped; 0.5 when the box has no length there. */
export function fractionAlong(bounds: Box | null, axis: SectionAxis, offset: number): number {
  const span = axisSpan(bounds, axis);
  if (span === null || !(span[1] > span[0])) return 0.5;
  return Math.min(1, Math.max(0, (offset - span[0]) / (span[1] - span[0])));
}

/** The offset a fraction of the way along an axis, or null for no box. */
export function offsetAt(bounds: Box | null, axis: SectionAxis, fraction: number): number | null {
  const span = axisSpan(bounds, axis);
  if (span === null) return null;
  const t = Number.isFinite(fraction) ? Math.min(1, Math.max(0, fraction)) : 0.5;
  return span[0] + t * (span[1] - span[0]);
}

// ---------------------------------------------------------------------------------------------- what can be shown
/** The vector fields a streamline can follow, in order of preference (`StreamlineSection` has the same list and the reason for it). */
export const STREAM_FIELDS: readonly string[] = ["U", "U_0", "velocity", "V"];

export function streamField(data: RunFields | null): string | null {
  return STREAM_FIELDS.find((name) => data?.fields.some((f) => f.name === name) === true) ?? null;
}

function hasVelocity(data: RunFields | null): boolean {
  return data !== null && data.fields.some((f) => isVectorFieldName(f.name) || VELOCITY_MAGNITUDE.has(f.name));
}

/**
 * Why a look cannot be chosen now, or null when it can: the tile's tooltip. Short, because it sits on a disabled
 * tile; the cases are the ones the Full-mode sections already refuse for the same reason.
 */
export function lookAvailability(data: RunFields | null): Record<LookId, string | null> {
  const none = (why: string): Record<LookId, string | null> => ({ surface: why, slice: why, iso: why, stream: why, vectors: why, clip: why });
  if (data === null) return none("Reading the results");
  const noMesh = data.surfaces === "missing" ? "The mesh surfaces are missing" : null;
  return {
    surface: noMesh,
    slice: noMesh,
    iso: noMesh ?? (isoFields(data).length === 0 ? "Needs speed, T or p in the run" : null),
    // Traced through the volume: the run's slice is only the fallback for a host that cannot trace.
    stream: noMesh ?? (streamField(data) === null ? "Needs the velocity" : null),
    vectors: noMesh ?? (hasVelocity(data) ? null : "Needs the velocity"),
    clip: noMesh,
  };
}

// ---------------------------------------------------------------------------------------------- which look is showing
/** The slice shows: the run's mid-plane is on, or any slice of the list is visible. */
export function sliceShown(state: LookState, data: RunFields | null): boolean {
  return (state.results.view.slice && data?.slice === true) || state.slices.list.some((s) => s.visible);
}

/**
 * The look the stores show. Stream and Vectors come before Slice because they stand on one (see the file's
 * head); a section with no slice is Clip (a slice's own cut sets the section, but then Slice already answered).
 */
export function activeLook(state: LookState, data: RunFields | null): LookId {
  if (state.iso.surface !== null && state.iso.visible) return "iso";
  if (state.streamlines.phase === "ready" && state.streamlines.lines.length > 0) return "stream";
  if (volumeArrowsShown(state.arrows)) return "vectors";
  const slice = sliceShown(state, data);
  if (state.arrows.shown && slice) return "vectors";
  if (slice) return "slice";
  if (state.section !== null) return "clip";
  return "surface";
}

/** The slice the Slice and Clip controls move: the first visible slice of the list that lies along an axis, else the run's mid-plane. */
export function lookSliceOf(state: LookState, ctx: LookContext): { axis: SectionAxis; offset: number; source: "user" | "mid"; id: string | null } | null {
  for (const s of state.slices.list) {
    if (!s.visible || s.origin !== "axis") continue;
    const found = axisOfPlane(s.plane);
    if (found !== null) return { axis: found.axis, offset: found.position, source: "user", id: s.id };
  }
  if (state.results.view.slice && ctx.data?.slice === true && ctx.midPlane !== null) return { ...ctx.midPlane, source: "mid", id: null };
  return null;
}

// ---------------------------------------------------------------------------------------------- actions
/** The server-side seeds a stream can start from. */
export type SeedKind = "line" | "plane" | "rake" | "sphere" | "inlet";

/**
 * What the card says about an ask, as a word or two (`text`) and the sentence for its tooltip (`tip`): the house
 * rule is a few words on the card and the explanation under the pointer.
 */
export interface LookNote {
  text: string;
  tip: string;
}

/** The card's note when a streamline ask comes back with nothing, and why the seeds gave none. */
export function noLinesNote(kind: SeedKind, seeds: number): LookNote {
  if (seeds === 0 && kind === "rake") return { text: "No rake", tip: "Pick two points with the Line tool first" };
  if (seeds === 0) {
    return kind === "inlet"
      ? { text: "No inlet here", tip: "The slice does not cross the inlet" }
      : { text: "No seeds", tip: "No seeds fit this model" };
  }
  return { text: "No lines", tip: "The flow crosses this plane, so no line follows it" };
}

/** The word for an iso-surface refusal (its sentence stays in the tooltip, from `isoRefusalText`). */
export function isoRefusalWord(code: string): string {
  switch (code) {
    case ISO_CODES.outOfRange:
      return "Out of range";
    case ISO_CODES.tooLarge:
      return "Too large";
    case ISO_CODES.host:
      return "Host unavailable";
    case ISO_CODES.busy:
      return "Server busy";
    default:
      return "Not cut";
  }
}

/** What a tile or a control asks for. Data, so the rules are tested without a store. */
export type LookAction =
  /** The run's own mid-plane slice on or off. */
  | { kind: "midSlice"; on: boolean }
  /** Every slice of the list hidden (not removed: Full mode keeps them). */
  | { kind: "hideUserSlices" }
  | { kind: "showUserSlice"; id: string }
  /** The slice the controls move goes to a plane: the visible one is moved, a hidden one shown, else one is added; the mid-plane goes off. */
  | { kind: "lookSlice"; axis: SectionAxis; offset: number }
  /** One more slice, parallel to the first. */
  | { kind: "parallelSlice"; axis: SectionAxis; offset: number }
  | { kind: "arrows"; shown: boolean; density?: number; scale?: number; where?: ArrowPlace }
  | { kind: "field"; name: string }
  | { kind: "clearStreamlines" }
  | { kind: "hideIso" }
  | { kind: "sectionOff" }
  | { kind: "sectionAt"; axis: SectionAxis; offset: number }
  | { kind: "sectionInvert"; invert: boolean }
  | { kind: "orthographic"; on: boolean }
  /** The bodies that are not the fluid, hidden from the view (the tree's eye does the same, by node id). */
  | { kind: "hideSolids"; ids: string[] }
  /** Asks of the server, or of the camera: not a change of the stores. */
  | { kind: "askIso"; field: string; value: number }
  | { kind: "askStreamlines"; seeds: SeedKind; count: number; tubes: boolean }
  /** The velocity sampled through the volume (`run.vectors`), about `count` arrows. */
  | { kind: "askVectors"; count: number }
  /**
   * `settle`: wait until the scene has stopped changing before framing it (`stepFitWatch`); the first picture asks for it.
   * `faceOn`: look along the thinnest axis of the model's box at the time of the fit (`faceOnDirection`), as a
   * sheet is read; absent keeps the view direction.
   * `slice`: the fit looks at the slice face-on (`viewFromFor`), from its normal's + side; the controller reads the
   * slice's axis at the time of the fit, since the slice that is on then is the one to be seen (R4d).
   */
  | { kind: "fit"; settle?: boolean; faceOn?: boolean; slice?: boolean }
  /**
   * Forget the section the slice's cut remembered for the user (`viewport/fields.ts` `forgetSavedSection`): the
   * cut gives it back when the slice goes, which put a section nobody asked for on the walls of Surface. A request,
   * made BEFORE the stores are written, so the release that the same press causes finds nothing to give back.
   */
  | { kind: "dropSavedSection" };

export type LookRequest = Extract<LookAction, { kind: "askIso" | "askStreamlines" | "askVectors" | "fit" | "dropSavedSection" }>;

/** The arrow densities "every 1 / 2 / 4" mean: every sample the slice has is far more than a figure holds, so the densest is the 192 the section offers. */
export const EVERY_DENSITY: Record<string, number> = { "1": ARROW_DENSITIES[4], "2": ARROW_DENSITIES[3], "4": ARROW_DENSITIES[2] };

/** "Every N" for a density, the nearest of the three. */
export function everyOf(density: number): "1" | "2" | "4" {
  let best: "1" | "2" | "4" = "4";
  let gap = Infinity;
  for (const key of ["1", "2", "4"] as const) {
    const d = Math.abs(EVERY_DENSITY[key] - density);
    if (d < gap) {
      best = key;
      gap = d;
    }
  }
  return best;
}

export const ARROW_SCALE = { min: 0.2, max: 3, step: 0.1 } as const;

/** The actions that put the slice on: nothing when one shows; the run's mid-plane; a slice already in the list; else a new one along the shortest axis. */
function sliceOnActions(state: LookState, ctx: LookContext): LookAction[] {
  if (sliceShown(state, ctx.data)) return [];
  if (ctx.data?.slice === true) return [{ kind: "midSlice", on: true }];
  const held = state.slices.list.find((s) => s.origin === "axis");
  if (held !== undefined) return [{ kind: "showUserSlice", id: held.id }];
  const axis = shortestAxis(ctx.bounds);
  const offset = offsetAt(ctx.bounds, axis, 0.5);
  return offset === null ? [] : [{ kind: "lookSlice", axis, offset }];
}

/**
 * The arrows at `where`. **Volume** (the default): the slices go, the arrows are asked of the host on an even
 * lattice through the fluid. **Slice**: the slice goes on and its velocity carries the arrows, as before.
 */
function vectorsAt(where: ArrowPlace, state: LookState, ctx: LookContext): LookAction[] {
  if (where === "volume") {
    return [
      { kind: "midSlice", on: false },
      { kind: "hideUserSlices" },
      { kind: "arrows", shown: true, where: "volume" },
      { kind: "askVectors", count: volumeCountOf(state.arrows.density) },
    ];
  }
  return [...sliceOnActions(state, ctx), ...velocityFieldActions(state, ctx.data), { kind: "arrows", shown: true, where: "slice" }];
}

/** The velocity colours the cut and the arrows: a pressure on the slice would carry arrows of nothing. */
function velocityFieldActions(state: LookState, data: RunFields | null): LookAction[] {
  const field = state.results.view.field;
  if (isVectorFieldName(field) || VELOCITY_MAGNITUDE.has(field)) return [];
  return data?.fields.some((f) => f.name === "U_mag") === true ? [{ kind: "field", name: "U_mag" }] : [];
}

/**
 * The field an iso-surface is cut through: the field of a surface already on screen (`shown`, so a
 * slider reads back the surface it sees), else the field on the walls when the run can contour it
 * (speed, `T`, `p`), else the first it can.
 */
export function isoFieldFor(view: { field: string }, data: RunFields | null, shown: string | null = null): IsoField | null {
  const all = isoFields(data);
  return (shown === null ? undefined : all.find((f) => f.name === shown)) ?? all.find((f) => f.name === view.field) ?? all[0] ?? null;
}

/** The field of the iso-surface on screen, or null. */
function shownIsoField(state: LookState): string | null {
  return state.iso.visible && state.iso.surface !== null ? state.iso.surface.field : null;
}

/** The value a fraction of the field's range stands for, to four significant digits (a slider's value is not worth more). */
export function isoValueAt(field: IsoField, fraction: number): number {
  const t = Number.isFinite(fraction) ? Math.min(1, Math.max(0, fraction)) : 0.5;
  return Number((field.min + t * (field.max - field.min)).toPrecision(4));
}

/** The iso value as a fraction of the range: the surface that is shown, if it is of this field, else what the card remembers. */
export function isoFractionOf(state: LookState, field: IsoField, prefs: LookPrefs): number {
  const shown = state.iso.surface;
  if (shown !== null && shown.field === field.name && field.max > field.min) return Math.min(1, Math.max(0, (shown.value - field.min) / (field.max - field.min)));
  return prefs.isoFraction;
}

/**
 * What takes a section the Looks did not ask for off the walls. **Clip is the only look that keeps a section.**
 * A section is the Looks' own while a slice that stays on is cutting the walls (Slice, Stream and Vectors stand on
 * the slice, whose cut `viewport/fields.ts` sets after the press); under Surface and Iso, which put the slice
 * away, or with no slice showing, whatever section there is was left by the Clip look or by an earlier cut
 * (R4b: a z = 0 section was on when Results opened, "Remove section" in the Section panel).
 * `dropSavedSection` is always made: the slice's cut remembers the section the user had and returns it when the
 * slice goes, which brought the stale one back on Surface.
 */
export function staleSectionActions(look: LookId, state: LookState, data: RunFields | null): LookAction[] {
  if (look === "clip") return [];
  const out: LookAction[] = [{ kind: "dropSavedSection" }];
  // Stream and volume Vectors take the slice away (they fill the volume), so only Slice and Vectors on a slice keep its cut.
  const sliceStays = (look === "slice" || (look === "vectors" && state.arrows.where === "slice")) && sliceShown(state, data);
  if (state.section !== null && !sliceStays) out.push({ kind: "sectionOff" });
  return out;
}

/**
 * What pressing a look's tile does. Every look leaves the others: what a look does not own is switched off, so
 * the tile that lights is the only thing on screen beyond the surfaces. A section is kept only by the Clip look
 * (`staleSectionActions`).
 */
export function activationActions(look: LookId, state: LookState, ctx: LookContext, prefs: LookPrefs = DEFAULT_PREFS): LookAction[] {
  const current = activeLook(state, ctx.data);
  // Pressing the Slice tile while it shows asks for the face-on fit again: the way back to the figure after orbiting (R4e).
  if (look === current) return look === "slice" ? [{ kind: "fit", settle: true, slice: true }] : [];
  const out: LookAction[] = [...staleSectionActions(look, state, ctx.data)];
  switch (look) {
    case "surface":
      out.push({ kind: "midSlice", on: false }, { kind: "hideUserSlices" }, { kind: "arrows", shown: false }, { kind: "clearStreamlines" }, { kind: "hideIso" });
      break;
    case "slice":
      out.push({ kind: "arrows", shown: false }, { kind: "clearStreamlines" }, { kind: "hideIso" }, ...sliceOnActions(state, ctx));
      // A slice is read face-on: the press frames it from its normal (R4d). Settled, as its geometry may still be on its way.
      out.push({ kind: "fit", settle: true, slice: true });
      break;
    case "iso": {
      out.push({ kind: "midSlice", on: false }, { kind: "hideUserSlices" }, { kind: "arrows", shown: false }, { kind: "clearStreamlines" });
      const field = isoFieldFor(state.results.view, ctx.data);
      if (field !== null) out.push({ kind: "askIso", field: field.name, value: isoValueAt(field, isoFractionOf(state, field, prefs)) });
      break;
    }
    case "stream":
      /*
        **Through the volume, with no slice** (2026-11-04). The lines were traced on the mid-plane, so that plane was
        put on, and its cut sectioned the walls at it: every line on the near side was clipped away with the walls
        and the rest were painted over the slice, which made a 3-D trace look like a drawing on one plane. The lines
        now run through the whole fluid, so the slices go and the walls fade (`wallsFaded`'s `inside`).
      */
      out.push({ kind: "arrows", shown: false }, { kind: "hideIso" }, { kind: "hideUserSlices" }, { kind: "midSlice", on: false });
      out.push({ kind: "askStreamlines", seeds: seedKindFor(prefs.streamSeeds, ctx.hasInlet), count: prefs.streamCount, tubes: prefs.streamTubes });
      break;
    case "vectors":
      out.push({ kind: "clearStreamlines" }, { kind: "hideIso" }, ...vectorsAt(state.arrows.where ?? "volume", state, ctx));
      break;
    case "clip": {
      out.push({ kind: "midSlice", on: false }, { kind: "hideUserSlices" }, { kind: "arrows", shown: false }, { kind: "clearStreamlines" }, { kind: "hideIso" });
      const axis = shortestAxis(ctx.bounds);
      const offset = offsetAt(ctx.bounds, axis, 0.5);
      if (offset !== null) out.push({ kind: "sectionAt", axis, offset });
      break;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- first show
export interface FirstShowInput {
  state: LookState;
  ctx: LookContext;
  /**
   * The project's bodies and the role each has. Not used for now: see `firstShowDefaults` (the fluid body alone
   * is not applied until it can target the mesh regions the Results stage shows).
   */
  bodies?: readonly { id: string; role: string }[];
  /**
   * The mesh is one cell thick: the server's `run.fields` `two_d` (`RunFields.twoD`), which the card passes. Only
   * `true` is acted on: absent or false leaves the box to decide, as before the server could say.
   */
  oneCellThick?: boolean;
}

/**
 * What to do the first time a project's results are shown (plan §3.7), as actions: a slice through the flow for
 * a 3-D mesh and the surfaces for a sheet, the camera orthographic for a sheet, and the view fitted.
 *
 * **The fluid body alone is NOT applied** (R2 review): `hideSolids` writes the global `hiddenNodes`, which the tree
 * eye and the Geometry stage read for the whole session, so hiding the CAD solids from the Results card hid them
 * everywhere and the user never asked. The action and its reducer stay (tested) for the day it can target the
 * mesh regions the Results stage shows instead. **A sheet is a mesh one cell thick, or whose shortest side is under 5 % of its longest**: the
 * client cannot count cells, so unless something says `oneCellThick` the box decides, and a mesh whose box is
 * not known yet is treated as 3-D (nothing is claimed flat for want of data). Orthographic is only ever turned
 * ON here: a user who set it themselves is not overruled the other way.
 */
export function firstShowDefaults(input: FirstShowInput): LookAction[] {
  const { state, ctx } = input;
  const out: LookAction[] = [];
  const sheet = input.oneCellThick === true || (ctx.bounds !== null && flatness(ctx.bounds) < THIN_RATIO);
  const look: LookId = sheet ? "surface" : "slice";
  const activation = activationActions(look, state, ctx);
  // The look may already be showing (nothing to activate), but a section left from before is still not asked for.
  // The Slice tile's own fit is dropped: the first show ends with its settled fit below (one fit, not two).
  out.push(...staleSectionActions(look, state, ctx.data).filter((a) => !activation.some((b) => b.kind === a.kind)), ...activation.filter((a) => a.kind !== "fit"));
  if (sheet) out.push({ kind: "orthographic", on: true });
  // **Last, and settled**: the slice's cut and the faded walls are written by the same press, but the slice's
  // geometry arrives from the server afterwards and the mesh patches of the Results stage may still be coming
  // in. A fit asked for now frames the parts the scene holds NOW (pipe3 came up zoomed into the outlet end,
  // the one patch that had arrived), so the controller waits for the scene to hold still first.
  // A sheet is read face on: along its thin axis, which is the fit's own direction rather than the one the camera was left in.
  // A slice through a 3-D mesh is read face-on too: from its plane's normal (`viewFromFor`, R4d).
  out.push(sheet ? { kind: "fit", settle: true, faceOn: true } : { kind: "fit", settle: true, slice: true });
  return out;
}

/**
 * The direction a fit looks from for a look, or null to keep the camera's: **a slice is viewed face-on**, from
 * the + side of its plane's normal (`directionVector`: front +Z, top +Y, right +X), so a y-normal cut of a pipe
 * along z is seen from +y with the pipe lying across the screen. The other looks (Surface, Iso, Stream, Vectors,
 * Clip) keep the direction they have, and a 2-D case is left to the sheet's own rule (`faceOnDirection`: along its
 * thin axis, orthographic). The camera stays perspective for 3-D. Pure.
 */
export function viewFromFor(look: LookId, axis: SectionAxis | null, twoD: boolean): [number, number, number] | null {
  if (look !== "slice" || axis === null || twoD) return null;
  return directionVector(axis === "z" ? "front" : axis === "y" ? "top" : "right");
}

/** The share of the model's longest side a streamline must travel to count as going somewhere. */
export const STREAM_STALL_FRACTION = 0.01;

/**
 * True when every line stayed put: none travelled `STREAM_STALL_FRACTION` of the model's longest side along its
 * path. The lines are traced IN the slice (`run.streamlines` on `slice`), so a slice ACROSS the flow gives lines
 * that go nowhere. **Measured on pipe3 (2026-11-03):** its solver settings set the slice normal along the pipe, and
 * 22 lines of 2,000 steps each moved about 0.04 mm in a 0.5 m pipe: drawn, and too small to see. The card says why
 * instead of showing an empty picture. Pure; false without lines or a box.
 */
export function streamlinesStalled(lines: readonly { points: readonly (readonly [number, number, number])[] }[], box: Box | null): boolean {
  if (lines.length === 0 || box === null) return false;
  const longest = Math.max(box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]);
  if (!(longest > 0)) return false;
  const needed = STREAM_STALL_FRACTION * longest;
  for (const line of lines) {
    let travelled = 0;
    for (let i = 1; i < line.points.length; i++) {
      const a = line.points[i - 1]!;
      const b = line.points[i]!;
      travelled += Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
      if (travelled >= needed) return false;
    }
  }
  return true;
}

/**
 * The direction to look from for a sheet: along the thinnest axis of the box, from its + side (`viewDirections`
 * `directionVector`: front is +Z, top +Y, right +X). z when there is no box, as `shortestAxis` answers. Pure.
 */
export function faceOnDirection(bounds: Box | null): [number, number, number] {
  const axis = shortestAxis(bounds);
  return directionVector(axis === "z" ? "front" : axis === "y" ? "top" : "right");
}

// ---------------------------------------------------------------------------------------------- the settled fit
/** How often the scene is looked at while the first fit waits, how many looks in a row must agree, and the most looks before it fits anyway. */
export const FIT_POLL_MS = 250;
export const FIT_STABLE_POLLS = 2;
export const FIT_MAX_POLLS = 40;

/**
 * What the scene holds when the fit looks: the box of what the fit will frame (null while nothing is there) and
 * whether every source the first show asked for has arrived: the slice, the slices of the list and the LAST of the
 * mesh patches the tree lists (R4c).
 */
export interface FitProbe {
  box: Box | null;
  sourcesReady: boolean;
}

/** What the watch remembers between looks: the box last seen, how many looks agreed with it, how many looks were made. */
export interface FitWatch {
  box: Box | null;
  stable: number;
  polls: number;
}

/** The box that holds every box given (nulls are skipped), or null when there is none. */
export function unionBox(boxes: readonly (Box | null)[]): Box | null {
  let out: Box | null = null;
  for (const box of boxes) {
    if (box === null) continue;
    out = out === null
      ? { min: [box.min[0], box.min[1], box.min[2]], max: [box.max[0], box.max[1], box.max[2]] }
      : {
          min: [Math.min(out.min[0], box.min[0]), Math.min(out.min[1], box.min[1]), Math.min(out.min[2], box.min[2])],
          max: [Math.max(out.max[0], box.max[0]), Math.max(out.max[1], box.max[1]), Math.max(out.max[2], box.max[2])],
        };
  }
  return out;
}

export const NEW_FIT_WATCH: FitWatch = { box: null, stable: 0, polls: 0 };

/** The same box to a part in a million of its longest side (a patch that arrives moves it by far more; float noise by far less). */
export function sameBox(a: Box | null, b: Box | null): boolean {
  if (a === null || b === null) return a === b;
  const size = Math.max(...[0, 1, 2].map((i) => Math.max(a.max[i] - a.min[i], b.max[i] - b.min[i])));
  const tolerance = 1e-6 * (size > 0 ? size : 1);
  return [0, 1, 2].every((i) => Math.abs(a.min[i] - b.min[i]) <= tolerance && Math.abs(a.max[i] - b.max[i]) <= tolerance);
}

/**
 * One look of the settled fit: fit when the box has been the same for `FIT_STABLE_POLLS` looks in a row with
 * every wanted source in the scene, or when `FIT_MAX_POLLS` looks have passed (a source that never arrives, a
 * server that is slow, must not leave the camera where it was). Pure, so the timing rule is tested without a
 * timer; the controller owns the timer and the probe.
 */
export function stepFitWatch(watch: FitWatch, probe: FitProbe): { watch: FitWatch; fit: boolean } {
  const polls = watch.polls + 1;
  const agrees = probe.box !== null && probe.sourcesReady && sameBox(watch.box, probe.box);
  const stable = agrees ? watch.stable + 1 : 0;
  const next: FitWatch = { box: probe.box, stable, polls };
  return { watch: next, fit: stable >= FIT_STABLE_POLLS || polls >= FIT_MAX_POLLS };
}

// ---------------------------------------------------------------------------------------------- controls
export interface ControlOption {
  value: string;
  label: string;
  /** Why this option cannot be chosen now. */
  disabled?: string;
}

/** One control of a look, as data: the card draws a row per control and knows nothing of what it means. */
export type LookControl =
  | { kind: "choice"; id: string; label: string; tip: string; value: string | null; options: ControlOption[] }
  | { kind: "slider"; id: string; label: string; tip: string; value: number; min: number; max: number; step: number; text: string; settleMs: number; disabled?: string }
  | { kind: "button"; id: string; label: string; tip: string; disabled?: string };

/**
 * How long a slider rests before the card acts on it (`settleMs` of the control): the ones that ask the server
 * for a new cut rest longest (a slice's plane is a cut, `SLICE_DEBOUNCE_MS`; an iso-surface is a bigger one), a
 * section only redraws, an arrow's length is free.
 */
const SETTLE = { slice: SLICE_DEBOUNCE_MS, iso: 400, clip: 150, arrows: 0 } as const;

const AXIS_OPTIONS: ControlOption[] = AXES.map((a) => ({ value: a, label: a.toUpperCase() }));

/** How many streamlines the seeds can be: the server draws at most 200 (`stokes.post.streamlines.MAX_LINES`). */
export const MAX_STREAM_SEEDS = 200;
export const STREAM_COUNTS: readonly number[] = [50, 100, 200];
export const ISO_COUNTS: readonly number[] = [1, 3, 5];

/**
 * The position slider. Its value reads **with the axis letter** (`y = 0 mm`): a bare "0 mm" read as "not set" on
 * pipe3, whose lengthwise plane sits at y = 0. How far along the axis that is goes in the tooltip, as a percentage.
 */
function positionControl(bounds: Box | null, axis: SectionAxis | null, offset: number | null, what: string, settleMs: number): LookControl {
  const usable = bounds !== null && axis !== null && offset !== null;
  const along = usable ? fractionAlong(bounds, axis, offset) : 0.5;
  return {
    kind: "slider",
    id: "position",
    label: "Position",
    tip: usable ? `Move the ${what} along its axis; now ${Math.round(along * 100)} % of the way along` : `Move the ${what} along its axis`,
    value: along,
    min: 0,
    max: 1,
    step: 0.01,
    text: usable ? `${axis} = ${formatMm(offset)}` : "",
    settleMs,
    ...(usable ? {} : { disabled: "Nothing is drawn yet" }),
  };
}

/**
 * The look's controls, two or three each. The slice's own come from where it lies now; a control that cannot act
 * (no box to measure positions in, a slice list that is full) is disabled with the reason, never hidden.
 */
export function controlsOf(look: LookId, state: LookState, ctx: LookContext, prefs: LookPrefs = DEFAULT_PREFS): LookControl[] {
  switch (look) {
    case "surface":
      return [];
    case "slice": {
      const found = lookSliceOf(state, ctx);
      const full = isFull(state.slices);
      return [
        { kind: "choice", id: "axis", label: "Axis", tip: "The direction the cut is normal to", value: found?.axis ?? null, options: AXIS_OPTIONS },
        positionControl(ctx.bounds, found?.axis ?? null, found?.offset ?? null, "slice", SETTLE.slice),
        {
          kind: "button",
          id: "parallel",
          label: "Add parallel",
          tip: full ? `At most ${MAX_SLICES} slices` : "Another slice parallel to this one",
          ...(found === null || ctx.bounds === null ? { disabled: "Pick an axis first" } : full ? { disabled: `At most ${MAX_SLICES} slices` } : {}),
        },
      ];
    }
    case "iso": {
      const field = isoFieldFor(state.results.view, ctx.data, shownIsoField(state));
      const t = field === null ? prefs.isoFraction : isoFractionOf(state, field, prefs);
      return [
        {
          kind: "slider",
          id: "value",
          label: "Value",
          tip: field === null ? "The run has no speed, T or p to contour" : `The value of ${field.name} the surface sits at, within its range`,
          value: t,
          min: 0,
          max: 1,
          step: 0.01,
          text: field === null ? "" : `${isoValueAt(field, t)}${field.units !== "" ? ` ${field.units}` : ""}`,
          settleMs: SETTLE.iso,
          ...(field === null ? { disabled: "Needs speed, T or p in the run" } : {}),
        },
        {
          kind: "choice",
          id: "count",
          label: "Count",
          tip: "How many surfaces, evenly spaced",
          value: "1",
          options: ISO_COUNTS.map((n) => ({ value: String(n), label: String(n), ...(n === 1 ? {} : { disabled: "One surface at a time for now" }) })),
        },
      ];
    }
    case "stream":
      return [
        {
          kind: "choice",
          id: "seed",
          label: "Seeds",
          tip: "Where the lines start: a line across the flow, a grid across it, a ring in the middle, or on every inlet",
          value: seedKindFor(prefs.streamSeeds, ctx.hasInlet),
          options: [
            { value: "line", label: "Line" },
            { value: "plane", label: "Plane" },
            { value: "rake", label: "Rake", ...(ctx.hasRake ? {} : { disabled: "Pick two points with Line first" }) },
            { value: "sphere", label: "Sphere" },
            { value: "inlet", label: "Inlet", ...(ctx.hasInlet ? {} : { disabled: "No inlet found in the mesh" }) },
          ],
        },
        {
          kind: "choice",
          id: "count",
          label: "Count",
          tip: "How many lines (the server draws at most 200)",
          value: String(prefs.streamCount),
          options: STREAM_COUNTS.map((n) => ({ value: String(n), label: String(n) })),
        },
        {
          kind: "choice",
          id: "style",
          label: "Style",
          tip: "Hairlines keep one width on screen; tubes have a width in the model and shrink with distance",
          value: prefs.streamTubes ? "tubes" : "lines",
          options: [
            { value: "lines", label: "Lines" },
            { value: "tubes", label: "Tubes" },
          ],
        },
      ];
    case "vectors":
      return [
        {
          kind: "choice",
          id: "where",
          label: "Where",
          tip: "Arrows through the whole fluid, or on a slice",
          value: state.arrows.where ?? "volume",
          options: [
            { value: "volume", label: "Volume" },
            { value: "slice", label: "Slice" },
          ],
        },
        {
          kind: "choice",
          id: "every",
          label: "Every",
          tip: "1 is the most arrows, 4 the fewest",
          value: everyOf(state.arrows.density),
          options: [{ value: "1", label: "1" }, { value: "2", label: "2" }, { value: "4", label: "4" }],
        },
        { kind: "slider", id: "scale", label: "Scale", tip: "The length of the arrows", value: state.arrows.scale, min: ARROW_SCALE.min, max: ARROW_SCALE.max, step: ARROW_SCALE.step, text: `${state.arrows.scale.toFixed(1)}x`, settleMs: SETTLE.arrows },
      ];
    case "clip": {
      const axis = state.section?.axis ?? null;
      return [
        { kind: "choice", id: "axis", label: "Axis", tip: "The direction the cut is normal to", value: axis, options: AXIS_OPTIONS },
        positionControl(ctx.bounds, axis, state.section?.offset ?? null, "cut", SETTLE.clip),
        { kind: "button", id: "flip", label: "Flip", tip: "Keep the other half", ...(state.section === null ? { disabled: "No cut yet" } : {}) },
      ];
    }
  }
}

/**
 * What a control does. `value` is the option's value, or the slider's number as text. Returns the actions and the
 * preferences after (a control no store holds, like the seed kind, is the card's to remember).
 */
export function controlActions(look: LookId, id: string, value: string, state: LookState, ctx: LookContext, prefs: LookPrefs = DEFAULT_PREFS): { actions: LookAction[]; prefs: LookPrefs } {
  const number = Number(value);
  const unchanged = { actions: [] as LookAction[], prefs };
  switch (look) {
    case "slice": {
      const found = lookSliceOf(state, ctx);
      if (id === "axis" && AXES.includes(value as SectionAxis)) {
        const axis = value as SectionAxis;
        const t = found === null ? 0.5 : fractionAlong(ctx.bounds, found.axis, found.offset);
        const offset = offsetAt(ctx.bounds, axis, t);
        if (offset === null) return unchanged;
        // A new axis is a new face to read: the camera follows it (position changes do not move the camera).
        const refit: LookAction[] = found?.axis === axis ? [] : [{ kind: "fit", settle: true, slice: true }];
        return { actions: [{ kind: "lookSlice", axis, offset }, ...refit], prefs };
      }
      if (id === "position" && found !== null && Number.isFinite(number)) {
        const offset = offsetAt(ctx.bounds, found.axis, number);
        return offset === null ? unchanged : { actions: [{ kind: "lookSlice", axis: found.axis, offset }], prefs };
      }
      if (id === "parallel" && found !== null && !isFull(state.slices)) {
        const offset = parallelOffset(state, ctx, found.axis);
        return offset === null ? unchanged : { actions: [{ kind: "parallelSlice", axis: found.axis, offset }], prefs };
      }
      return unchanged;
    }
    case "iso": {
      const field = isoFieldFor(state.results.view, ctx.data, shownIsoField(state));
      if (id !== "value" || field === null || !Number.isFinite(number)) return unchanged;
      const next = { ...prefs, isoFraction: Math.min(1, Math.max(0, number)) };
      return { actions: [{ kind: "askIso", field: field.name, value: isoValueAt(field, next.isoFraction) }], prefs: next };
    }
    case "stream": {
      let next = prefs;
      if (id === "seed" && (value === "line" || value === "plane" || value === "rake" || value === "sphere" || value === "inlet")) next = { ...prefs, streamSeeds: value };
      else if (id === "count" && STREAM_COUNTS.includes(number)) next = { ...prefs, streamCount: number };
      else if (id === "style" && (value === "lines" || value === "tubes")) next = { ...prefs, streamTubes: value === "tubes" };
      return next === prefs ? unchanged : { actions: [{ kind: "askStreamlines", seeds: seedKindFor(next.streamSeeds, ctx.hasInlet), count: next.streamCount, tubes: next.streamTubes }], prefs: next };
    }
    case "vectors": {
      if (id === "where" && (value === "volume" || value === "slice")) {
        if (value === (state.arrows.where ?? "volume") && state.arrows.shown) return unchanged;
        const after: LookState = { ...state, arrows: { ...state.arrows, where: value } };
        return { actions: [...staleSectionActions("vectors", after, ctx.data), ...vectorsAt(value, after, ctx)], prefs };
      }
      if (id === "every" && EVERY_DENSITY[value] !== undefined) {
        const density = EVERY_DENSITY[value];
        // A volume's arrows are a new sample of the host's: a denser lattice is a new ask.
        const ask: LookAction[] = state.arrows.where === "volume" ? [{ kind: "askVectors", count: volumeCountOf(density) }] : [];
        return { actions: [{ kind: "arrows", shown: true, density }, ...ask], prefs };
      }
      if (id === "scale" && Number.isFinite(number)) return { actions: [{ kind: "arrows", shown: true, scale: Math.min(ARROW_SCALE.max, Math.max(ARROW_SCALE.min, number)) }], prefs };
      return unchanged;
    }
    case "clip": {
      const section = state.section;
      if (id === "axis" && AXES.includes(value as SectionAxis)) {
        const axis = value as SectionAxis;
        const t = section === null ? 0.5 : fractionAlong(ctx.bounds, section.axis, section.offset);
        const offset = offsetAt(ctx.bounds, axis, t);
        return offset === null ? unchanged : { actions: [{ kind: "sectionAt", axis, offset }], prefs };
      }
      if (id === "position" && section !== null && Number.isFinite(number)) {
        const offset = offsetAt(ctx.bounds, section.axis, number);
        return offset === null ? unchanged : { actions: [{ kind: "sectionAt", axis: section.axis, offset }], prefs };
      }
      if (id === "flip" && section !== null) return { actions: [{ kind: "sectionInvert", invert: !section.invert }], prefs };
      return unchanged;
    }
    default:
      return unchanged;
  }
}

/** Where "Add parallel" puts the next slice: the first of a few round fractions of the axis that no slice of that axis is near. */
export function parallelOffset(state: LookState, ctx: LookContext, axis: SectionAxis): number | null {
  const used: number[] = [];
  for (const s of state.slices.list) {
    const found = axisOfPlane(s.plane);
    if (found !== null && found.axis === axis) used.push(fractionAlong(ctx.bounds, axis, found.position));
  }
  const mid = ctx.data?.slice === true && state.results.view.slice && ctx.midPlane?.axis === axis ? fractionAlong(ctx.bounds, axis, ctx.midPlane.offset) : null;
  if (mid !== null) used.push(mid);
  for (const t of [0.25, 0.75, 0.5, 0.1, 0.9, 0.4, 0.6]) {
    if (used.every((u) => Math.abs(u - t) > 0.03)) return offsetAt(ctx.bounds, axis, t);
  }
  return null;
}

// ---------------------------------------------------------------------------------------------- the reducer
/** The state after the actions, and the asks that are left for the controller. Pure; an action that changes nothing keeps the object it was given. */
export function applyLookActions(state: LookState, actions: readonly LookAction[]): { state: LookState; requests: LookRequest[] } {
  let next = state;
  const requests: LookRequest[] = [];
  for (const action of actions) {
    switch (action.kind) {
      case "midSlice": {
        const results = setSlice(next.results, action.on);
        if (results !== next.results) next = { ...next, results };
        break;
      }
      case "hideUserSlices": {
        let slices = next.slices;
        for (const s of next.slices.list) if (s.visible) slices = updateSlice(slices, s.id, { visible: false });
        if (slices !== next.slices) next = { ...next, slices };
        break;
      }
      case "showUserSlice": {
        const slices = updateSlice(next.slices, action.id, { visible: true });
        if (slices !== next.slices) next = { ...next, slices };
        break;
      }
      case "lookSlice": {
        const plane = axisPlane(action.axis, action.offset);
        const list = next.slices.list;
        const target = list.find((s) => s.origin === "axis" && s.visible) ?? list.find((s) => s.origin === "axis");
        const slices = target === undefined ? addAxisSlice(next.slices, action.axis, action.offset) : updateSlice(moveSlice(next.slices, target.id, plane), target.id, { visible: true });
        // The run's own mid-plane gives way to the slice that moves: two cuts at once is a figure nobody asked for.
        const results = setSlice(next.results, false);
        if (slices !== next.slices || results !== next.results) next = { ...next, slices, results };
        break;
      }
      case "parallelSlice": {
        const slices = addAxisSlice(next.slices, action.axis, action.offset);
        if (slices !== next.slices) next = { ...next, slices };
        break;
      }
      case "arrows": {
        const a = next.arrows;
        const where = action.where ?? a.where;
        const arrows: ArrowView = { shown: action.shown, density: action.density ?? a.density, scale: action.scale ?? a.scale, ...(where === undefined ? {} : { where }) };
        if (arrows.shown !== a.shown || arrows.density !== a.density || arrows.scale !== a.scale || arrows.where !== a.where) next = { ...next, arrows };
        break;
      }
      case "field": {
        const results = chooseField(next.results, action.name);
        if (results !== next.results) next = { ...next, results };
        break;
      }
      case "clearStreamlines":
        if (next.streamlines.phase !== "idle" || next.streamlines.lines.length > 0) next = { ...next, streamlines: NO_STREAMLINES };
        break;
      case "hideIso": {
        const iso = hideIso(next.iso);
        if (iso !== next.iso) next = { ...next, iso };
        break;
      }
      case "sectionOff":
        if (next.section !== null) next = { ...next, section: null };
        break;
      case "sectionAt": {
        const plane = { ...sectionAt(action.axis, action.offset), invert: next.section?.invert ?? false };
        if (next.section === null || next.section.axis !== plane.axis || next.section.offset !== plane.offset) next = { ...next, section: plane };
        break;
      }
      case "sectionInvert":
        if (next.section !== null && next.section.invert !== action.invert) next = { ...next, section: { ...next.section, invert: action.invert } };
        break;
      case "orthographic":
        if (next.orthographic !== action.on) next = { ...next, orthographic: action.on };
        break;
      case "hideSolids": {
        const missing = action.ids.filter((id) => !next.hiddenNodes.includes(id));
        if (missing.length > 0) next = { ...next, hiddenNodes: [...next.hiddenNodes, ...missing] };
        break;
      }
      case "askIso":
      case "askStreamlines":
      case "askVectors":
      case "fit":
      case "dropSavedSection":
        requests.push(action);
        break;
    }
  }
  return { state: next, requests };
}

// ---------------------------------------------------------------------------------------------- stream seeds
/** The in-plane axes of a plane normal to `normal`, the longer of the box's two first: the flow runs lengthwise until a velocity says otherwise. */
function planeAxes(box: Box, normal: 0 | 1 | 2): { flow: 0 | 1 | 2; across: 0 | 1 | 2 } {
  const rest = ([0, 1, 2] as const).filter((a) => a !== normal) as [0 | 1 | 2, 0 | 1 | 2];
  const size = (a: 0 | 1 | 2): number => box.max[a] - box.min[a];
  return size(rest[0]) >= size(rest[1]) ? { flow: rest[0], across: rest[1] } : { flow: rest[1], across: rest[0] };
}

/** What the seeds are placed against. `inlet` is the inlet patch's box, for the `inlet` kind. */
export interface SeedGeometry {
  box: Box;
  /** The axis the slice is normal to, and where it lies along it. */
  normal: 0 | 1 | 2;
  offset: number;
  inlet?: Box | null;
  /** The two points picked with the Line tool, in metres: a `rake` seeds between them. */
  rake?: readonly [readonly number[], readonly number[]] | null;
}

/**
 * Seed points ON the slice's plane (the streamline command traces on the slice's surface, so a seed off it is
 * dropped): `line` is a row across the flow just inside the upstream end, `sphere` is a ring in the middle (the
 * cut of a sphere by the plane), `inlet` is a row where the inlet patch meets the plane, nudged into the body.
 * A count is clamped to 0..`MAX_STREAM_SEEDS`; `inlet` gives nothing when the plane misses the inlet patch or
 * runs parallel to it, and the caller says so.
 */
export function streamSeeds(kind: SeedKind, geometry: SeedGeometry, count: number): [number, number, number][] {
  const n = Math.min(MAX_STREAM_SEEDS, Math.max(0, Math.trunc(Number.isFinite(count) ? count : 0)));
  if (n === 0) return [];
  const { box, normal, offset } = geometry;
  if (kind === "rake") {
    // **A rake between the two picked points** (ADR-0030): evenly spaced, both ends included. In 3-D, so a
    // volume trace starts exactly where the user pointed.
    const [a, b] = geometry.rake ?? [null, null];
    if (a == null || b == null || a.length < 3 || b.length < 3) return [];
    const rake: [number, number, number][] = [];
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 0.5 : i / (n - 1);
      rake.push([a[0]! + t * (b[0]! - a[0]!), a[1]! + t * (b[1]! - a[1]!), a[2]! + t * (b[2]! - a[2]!)]);
    }
    return rake;
  }
  const { flow, across } = planeAxes(box, normal);
  const point = (f: number, a: number): [number, number, number] => {
    const p: [number, number, number] = [0, 0, 0];
    p[normal] = offset;
    p[flow] = f;
    p[across] = a;
    return p;
  };
  const spanFlow = box.max[flow] - box.min[flow];
  const spanAcross = box.max[across] - box.min[across];
  const out: [number, number, number][] = [];
  if (kind === "line") {
    // **Inside the extent**: at the very edge a line starts on the boundary and leaves at once (`seedRake`'s rule).
    for (let i = 0; i < n; i++) out.push(point(box.min[flow] + 0.05 * spanFlow, box.min[across] + ((i + 0.5) / n) * spanAcross));
    return out;
  }
  if (kind === "plane") {
    // **A grid across the flow** (ADR-0030): a cross-section near the flow's start, spread over the plane's
    // other two axes, so a volume trace shows how the whole section moves. The lines leave the mid-plane.
    const k = Math.max(1, Math.floor(Math.sqrt(n)));
    const spanNormal = box.max[normal] - box.min[normal];
    for (let i = 0; i < k; i++) {
      for (let j = 0; j < k; j++) {
        const p = point(box.min[flow] + 0.05 * spanFlow, box.min[across] + ((i + 0.5) / k) * spanAcross);
        p[normal] = box.min[normal] + ((j + 0.5) / k) * spanNormal;
        out.push(p);
      }
    }
    return out;
  }
  if (kind === "sphere") {
    const radius = 0.25 * Math.min(spanFlow, spanAcross);
    const cf = (box.min[flow] + box.max[flow]) / 2;
    const ca = (box.min[across] + box.max[across]) / 2;
    for (let i = 0; i < n; i++) {
      const angle = (2 * Math.PI * i) / n;
      out.push(point(cf + radius * Math.cos(angle), ca + radius * Math.sin(angle)));
    }
    return out;
  }
  const inlet = geometry.inlet ?? null;
  if (inlet === null) return [];
  const scale = Math.max(spanFlow, spanAcross, box.max[normal] - box.min[normal]);
  const tolerance = 1e-6 * scale;
  if (offset < inlet.min[normal] - tolerance || offset > inlet.max[normal] + tolerance) return [];
  const inFlow = inlet.max[flow] - inlet.min[flow];
  const inAcross = inlet.max[across] - inlet.min[across];
  // The inlet is a face at one end of the flow, thin along one of the plane's axes. Along the other it is a row to seed.
  const thinFlow = inFlow <= inAcross;
  const thin = thinFlow ? inFlow : inAcross;
  if (thin > 0.25 * (thinFlow ? spanFlow : spanAcross)) return [];
  const faceFlow = thinFlow ? (inlet.min[flow] + inlet.max[flow]) / 2 : null;
  const faceAcross = thinFlow ? null : (inlet.min[across] + inlet.max[across]) / 2;
  const centre = (box.min[thinFlow ? flow : across] + box.max[thinFlow ? flow : across]) / 2;
  const nudge = 0.015 * (thinFlow ? spanFlow : spanAcross);
  for (let i = 0; i < n; i++) {
    const t = (i + 0.5) / n;
    if (thinFlow && faceFlow !== null) out.push(point(faceFlow + (centre >= faceFlow ? nudge : -nudge), inlet.min[across] + t * inAcross));
    else if (faceAcross !== null) out.push(point(inlet.min[flow] + t * inFlow, faceAcross + (centre >= faceAcross ? nudge : -nudge)));
  }
  return out;
}

/** The mesh patch of the case's inlet: the boundary of kind `velocity_inlet` and the patch of the same name (OpenFOAM names a patch after its boundary). */
export function inletPatchOf(boundaries: readonly { name: string; kind: string }[], patches: readonly { id: string; name: string }[]): { id: string; name: string } | null {
  for (const b of boundaries) {
    if (b.kind !== "velocity_inlet") continue;
    const found = patches.find((p) => p.name.toLowerCase() === b.name.toLowerCase());
    if (found !== undefined) return found;
  }
  return null;
}

/** Whether a boundary kind lets the flow in: `velocity_inlet`, and any other `*_inlet` the catalogue has. */
function isInletKind(kind: string): boolean {
  return kind === "velocity_inlet" || kind.endsWith("_inlet");
}

/**
 * The mesh patches of EVERY inlet, in the tree's order, each once: the patches a volume trace seeds from. A mixing
 * elbow has two, and seeding one of them (`inletPatchOf`) drew one stream and none of the jet that mixes into it.
 */
export function inletPatchesOf(boundaries: readonly { name: string; kind: string }[], patches: readonly { id: string; name: string }[]): { id: string; name: string }[] {
  const out: { id: string; name: string }[] = [];
  for (const b of boundaries) {
    if (!isInletKind(b.kind)) continue;
    const found = patches.find((p) => p.name.toLowerCase() === b.name.toLowerCase());
    if (found !== undefined && !out.some((p) => p.id === found.id)) out.push(found);
  }
  return out;
}
