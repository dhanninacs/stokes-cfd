/**
 * The iso-surface control's decisions (ADR-0026 amendment 1), as pure functions so they are tested in Node:
 * which fields can be contoured, what a typed value is sent as, how `run.iso_surface`'s reply and the
 * `run.iso_ready` / `run.iso_failed` events are read, and what each refusal says to a user.
 *
 * The contract: `run.iso_surface {run?, field, value}` answers `{run, field, value, units, state, triangles?,
 * points?, range: {min, max}, key, result}` with `state` `ready` (cached), `empty` (the contour found nothing)
 * or `cutting` (the host is cutting it; the summary follows as `run.iso_ready {run_id, field, value, ...}` or
 * `run.iso_failed {run_id, field, value, code, message}`). Refusals are errors with a stable code:
 * `results.iso_out_of_range`, `results.iso_too_large`, `results.host_unavailable`, `results.iso_invalid`.
 *
 * The surface in the viewport: `IsoView` (which surface, shown or not) is what the control asks for, and
 * `IsoDrawn` is what the viewport's `IsoLayer` (viewport/isoLayer.ts) made of it.
 *
 * Import rule: no React, no store, no DOM. Everything read from the wire is data.
 */
import type { RunFields } from "./resultsApi";

export const ISO_COMMAND = "run.iso_surface";
export const ISO_EVENTS = { ready: "run.iso_ready", failed: "run.iso_failed" } as const;

/** The fields a contour can be cut through (`stokes.run.iso.ISO_FIELDS`): one component each. */
export const ISO_FIELDS: readonly string[] = ["U_mag", "T", "p"];

/**
 * How long the control waits for the event before asking again, and how much longer each time after. The
 * event is the answer; the ask is for a lost one, so it is slow and backs off (never faster than 1 s), which
 * also keeps a long cut from collecting duplicate requests in the server's cut queue.
 */
export const ISO_POLL_MS = 5_000;
export const ISO_POLL_MAX_MS = 30_000;

export interface IsoField {
  name: string;
  units: string;
  min: number;
  max: number;
}

/** The contourable fields the run has, `T` first, with their units and ranges. */
export function isoFields(data: RunFields | null): IsoField[] {
  if (data === null || data.surfaces !== "ok") return [];
  const out: IsoField[] = [];
  for (const name of ISO_FIELDS) {
    const found = data.fields.find((f) => f.name === name);
    if (found !== undefined) out.push({ name, units: found.units, min: found.min, max: found.max });
  }
  return out;
}

/** The value to start the input with: the middle of the field's range, to four significant digits. */
export function middleValue(field: IsoField): string {
  return String(Number(((field.min + field.max) / 2).toPrecision(4)));
}

/**
 * What a typed value is sent as: a plain number is in the unit the field shows, which is the SI unit the run
 * stores (`K`, `Pa`, `m2/s2`), so it goes as a JSON number; anything else (`27 degC`, `300 [K]`) goes as
 * quantity text for the server to parse. Null for an empty input.
 */
export function isoValueParam(text: string): number | string | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const number = Number(trimmed);
  return Number.isFinite(number) && /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(trimmed) ? number : trimmed;
}

export type IsoState = "ready" | "empty" | "cutting";

/** One surface as the control shows it. */
export interface IsoSummary {
  run: string;
  field: string;
  value: number;
  units: string;
  state: IsoState;
  triangles: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readSummary(raw: Record<string, unknown>, run: string): IsoSummary | null {
  const state = raw.state;
  const value = num(raw.value);
  if ((state !== "ready" && state !== "empty" && state !== "cutting") || value === null || text(raw.field) === "") return null;
  return { run, field: text(raw.field), value, units: text(raw.units), state, triangles: Math.max(0, Math.round(num(raw.triangles) ?? 0)) };
}

/** `run.iso_surface`'s reply, or null for a payload that is not one. */
export function readIsoReply(payload: unknown): IsoSummary | null {
  if (!isRecord(payload)) return null;
  const run = text(payload.run);
  return run === "" ? null : readSummary(payload, run);
}

export type IsoEvent = { kind: "ready"; summary: IsoSummary } | { kind: "failed"; run: string; field: string; value: number; code: string; message: string };

/** `run.iso_ready` or `run.iso_failed`, typed; null for any other event or a payload that is not one. */
export function readIsoEvent(event: string, data: Record<string, unknown>): IsoEvent | null {
  const run = text(data.run_id);
  if (run === "") return null;
  if (event === ISO_EVENTS.ready) {
    const summary = readSummary(data, run);
    return summary === null ? null : { kind: "ready", summary };
  }
  if (event === ISO_EVENTS.failed) {
    const value = num(data.value);
    if (value === null) return null;
    return { kind: "failed", run, field: text(data.field), value, code: text(data.code), message: text(data.message) };
  }
  return null;
}

/** The same surface: the run, the field, and the value to the precision the server keys it by. */
export function sameSurface(a: { run: string; field: string; value: number }, b: { run: string; field: string; value: number }): boolean {
  return a.run === b.run && a.field === b.field && Math.abs(a.value - b.value) <= 1e-9 * Math.max(1, Math.abs(a.value), Math.abs(b.value));
}

/** The codes of the refusals the control explains in its own words. */
export const ISO_CODES = {
  outOfRange: "results.iso_out_of_range",
  tooLarge: "results.iso_too_large",
  host: "results.host_unavailable",
  busy: "results.busy",
  invalid: "results.iso_invalid",
} as const;

/**
 * A refusal as the control says it. Out of range and an invalid value are the server's own sentence (it names
 * the value, the field and its range); the others say what happened and what to do.
 */
export function isoRefusalText(code: string, message: string): string {
  switch (code) {
    case ISO_CODES.outOfRange:
      return message !== "" ? `Out of range: ${message}.` : "Out of range: the field never takes this value.";
    case ISO_CODES.tooLarge:
      return "Too large to show: the surface has more triangles than the viewport is allowed. Choose a value away from where the field is nearly constant.";
    case ISO_CODES.host:
      return `Host unavailable: the surface is not cached and the run's host cannot cut it${message !== "" ? ` (${message})` : ""}.`;
    case ISO_CODES.busy:
      return "The server is cutting other surfaces; try again in a few seconds.";
    default:
      return message !== "" ? message : "The iso-surface could not be cut.";
  }
}

/** Thousands separated, as the mesh counts are. */
function count(n: number): string {
  return n.toLocaleString("en-US");
}

/** `T = 300 K`: the field, the value to six significant digits, and its unit. */
function isoAt(summary: IsoSummary): string {
  return `${summary.field} = ${Number(summary.value.toPrecision(6))}${summary.units !== "" ? ` ${summary.units}` : ""}`;
}

/** The state line under the control. */
export function isoStateText(summary: IsoSummary): string {
  const at = isoAt(summary);
  if (summary.state === "cutting") return `Cutting ${at} on the run's host…`;
  if (summary.state === "empty") return `Empty: ${at} has no surface.`;
  return `Ready: ${at}, ${count(summary.triangles)} triangles`;
}

// ---------------------------------------------------------------------------- the surface in the viewport

/**
 * The iso-surface the viewport draws (view state, no journal record): the surface the control last made ready,
 * or none, and whether it is shown. One at a time: a new one replaces the old.
 */
export interface IsoView {
  surface: IsoSummary | null;
  visible: boolean;
}

export const NO_ISO: IsoView = { surface: null, visible: true };

/**
 * What the viewport does with an answer of the control: a ready surface replaces whatever was drawn (and is
 * shown, even if the old one was switched off); an empty one leaves nothing to draw; a cut still under way
 * changes nothing yet. The same surface again returns the view unchanged, so nothing is fetched twice.
 */
export function showIso(view: IsoView, summary: IsoSummary): IsoView {
  if (summary.state === "cutting") return view;
  if (summary.state === "empty") return view.surface === null ? view : NO_ISO;
  if (view.surface !== null && sameSurface(view.surface, summary)) return view.visible ? view : { ...view, visible: true };
  return { surface: summary, visible: true };
}

/** Hide: the surface leaves the viewport (its mesh is freed); Show iso brings one back. */
export function hideIso(view: IsoView): IsoView {
  return view.surface === null ? view : NO_ISO;
}

/** The visibility switch: the mesh stays in the scene, drawn or not. */
export function setIsoVisible(view: IsoView, visible: boolean): IsoView {
  return view.visible === visible ? view : { ...view, visible };
}

/** The scene source of the iso-surface: a mesh source (`mesh:`), so it is never taken for a part. */
export const ISO_SOURCE = "mesh:iso";

/** The route of a surface (`GET /api/results/iso/{run}?field=&value=`, transport version 2, as the slices). */
export function isoRoute(surface: { run: string; field: string; value: number }): string {
  return `/api/results/iso/${encodeURIComponent(surface.run)}?field=${encodeURIComponent(surface.field)}&value=${encodeURIComponent(String(surface.value))}`;
}

/** The key one surface is fetched and drawn under. */
export function isoKey(surface: { run: string; field: string; value: number }): string {
  return `${surface.run}|${surface.field}|${surface.value}`;
}

/** The viewport's line for a drawn surface: `iso T = 300 K, 12,345 triangles`. */
export function isoViewportText(summary: IsoSummary, triangles: number): string {
  return `iso ${isoAt(summary)}, ${count(triangles)} triangles`;
}

/** What the viewport has made of the surface: the layer writes it, the line and the control read it. */
export interface IsoDrawn {
  state: "none" | "loading" | "shown" | "error";
  /** The key of the surface in the scene (`isoKey`), "" for none. */
  key: string;
  triangles: number;
  message: string;
}

export const NO_ISO_DRAWN: IsoDrawn = { state: "none", key: "", triangles: 0, message: "" };
