/**
 * The wire contract of the results (T-216 server, T-218 front end), in ONE module: the command, the routes,
 * the defensive reader of the command's reply and the byte fetch. When the server's contract changes this is
 * the only file that has to.
 *
 * * `run.fields {run?}` answers `{run, time, fields: [{name, units, min, max}], slice: bool, surfaces:
 *   "ok" | "missing", two_d?: bool}` from the server's cache only (it never reads the solver's files);
 * * `GET /api/results/field/{run}/{target}?field=<name>`: one field on one surface, `target` a `mesh_patch`
 *   node id or the word `slice` (the codec is `state/fieldCodec.ts`); 404 for an unknown run, patch or no
 *   cache, 400 for an unknown field;
 * * `GET /api/results/slice/{run}`: the slice as transport version 2, the payload a mesh patch route serves
 *   (`parseGeometry` reads it); `GET /api/results/slice-edges/{run}`: its cell edges in the edge codec
 *   (`parseMeshEdges`); 404 when the run has no slice.
 * * the same three routes take an optional `?plane=ox,oy,oz,nx,ny,nz` (metres): the cut at any plane, on demand
 *   (`state/slicePlane.ts` builds the text); the field route then reads `?plane=...&field=<name>`, its header
 *   range is still the run's global one. A plane that misses the model is 404 `results.slice_empty`, a plane
 *   not cut before on a run whose host is gone is 404 `results.host_unavailable`, a malformed one 400.
 *
 * Import rule: no React, no store, no DOM beyond `fetch`. Everything read from the wire is data: a missing
 * or mistyped field gives a neutral value, never an exception.
 */

import { planeQuery, type Plane } from "../state/slicePlane";

export const RESULTS_COMMANDS = {
  fields: "run.fields",
} as const;

/** The target of the slice's field route. */
export const SLICE_TARGET = "slice";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** One field the run has: its name, unit and range over the whole case. */
export interface FieldInfo {
  name: string;
  units: string;
  min: number;
  max: number;
  /**
   * The 1st and 99th percentile of the field, when the server sends them. TODO(server): `run.fields` does not
   * send these yet (`agent/stokes_agent/extract.py` header); until it does, auto range is min..max.
   */
  p01?: number;
  p99?: number;
}

/** The reply of `run.fields`, trimmed to what the front end uses. */
export interface RunFields {
  run: string;
  time: number;
  fields: FieldInfo[];
  /** The run has a mid-plane slice. */
  slice: boolean;
  /** The mesh surfaces the fields are drawn on exist on the server. */
  surfaces: "ok" | "missing";
  /**
   * The mesh is one cell thick (an `empty` patch, or one cell along an axis): the case is 2-D. Absent when the
   * server does not say (an older one); the front end then guesses from the model's box.
   * TODO(server): `run.fields` does not send `two_d` yet.
   */
  twoD?: boolean;
  /** Every time the run wrote fields at, ascending (a transient run's steps); absent from an older server. */
  times?: number[];
}

/** `run.fields`' reply, or null for a payload that is not one (no run id). */
export function readRunFields(payload: unknown): RunFields | null {
  if (!isRecord(payload)) return null;
  const run = text(payload.run);
  if (run === "") return null;
  const fields: FieldInfo[] = [];
  if (Array.isArray(payload.fields)) {
    for (const f of payload.fields) {
      if (!isRecord(f) || text(f.name) === "") continue;
      const min = num(f.min);
      const max = num(f.max);
      if (min === null || max === null) continue;
      const p01 = num(f.p01);
      const p99 = num(f.p99);
      fields.push({ name: text(f.name), units: text(f.units), min, max, ...(p01 !== null && p99 !== null ? { p01, p99 } : {}) });
    }
  }
  return {
    run,
    time: num(payload.time) ?? 0,
    fields,
    slice: payload.slice === true,
    surfaces: payload.surfaces === "missing" ? "missing" : "ok",
    ...(typeof payload.two_d === "boolean" ? { twoD: payload.two_d } : {}),
    ...(Array.isArray(payload.times) ? { times: payload.times.map(num).filter((t): t is number => t !== null) } : {}),
  };
}

/** The message of a refusal: the server's own words, or a plain one when it said none. */
export function refusalText(code: string, message: string): string {
  if (code === "run.no_results" || code === "run.not_ready") return message !== "" ? message : "The run has no results to show yet.";
  return message !== "" ? message : "The results could not be read.";
}

// ---------------------------------------------------------------------------------------------- routes
const ROUTE = "/api/results";

/** The route of one field on one surface. The pieces are encoded: a run id or a node id is data. */
export function fieldRoute(run: string, target: string, field: string): string {
  return `${ROUTE}/field/${encodeURIComponent(run)}/${encodeURIComponent(target)}?field=${encodeURIComponent(field)}`;
}

/** `plane=ox,oy,oz,nx,ny,nz` (the commas stay as they are), or "" for no plane: the run's own mid-plane. */
function planeParameter(plane: Plane | null): string | null {
  if (plane === null) return "";
  const text = planeQuery(plane);
  return text === null ? null : `plane=${text.replace(/[^0-9a-zA-Z.,\-]/g, encodeURIComponent)}`;
}

/** The route of the slice cut at `plane`, or of the run's own mid-plane without one. Null for a plane the server refuses (not finite, no normal). */
export function sliceRoute(run: string): string;
export function sliceRoute(run: string, plane: Plane | null): string | null;
export function sliceRoute(run: string, plane: Plane | null = null): string | null {
  const parameter = planeParameter(plane);
  return parameter === null ? null : `${ROUTE}/slice/${encodeURIComponent(run)}${parameter === "" ? "" : `?${parameter}`}`;
}

export function sliceEdgesRoute(run: string): string;
export function sliceEdgesRoute(run: string, plane: Plane | null): string | null;
export function sliceEdgesRoute(run: string, plane: Plane | null = null): string | null {
  const parameter = planeParameter(plane);
  return parameter === null ? null : `${ROUTE}/slice-edges/${encodeURIComponent(run)}${parameter === "" ? "" : `?${parameter}`}`;
}

/** The route of a field on the cut at `plane` (`plane` first, then `field`); the mid-plane's own with no plane, as `fieldRoute(run, SLICE_TARGET, field)`. */
export function sliceFieldRoute(run: string, plane: Plane | null, field: string): string | null {
  const parameter = planeParameter(plane);
  if (parameter === null) return null;
  return `${ROUTE}/field/${encodeURIComponent(run)}/${SLICE_TARGET}?${parameter === "" ? "" : `${parameter}&`}field=${encodeURIComponent(field)}`;
}

/** The codes the server gives a cut it cannot make (404 `results.slice_empty`, 404 `results.host_unavailable`, 400 `results.plane`). */
export const SLICE_CODES = {
  empty: "results.slice_empty",
  host: "results.host_unavailable",
  plane: "results.plane",
} as const;

/** What a cut route answered: the bytes, or why there are none. */
export type CutAnswer =
  | { kind: "ok"; bytes: ArrayBuffer }
  /** The plane misses the mesh (404 `results.slice_empty`). */
  | { kind: "empty" }
  /** The plane was not cut before and the run's host is gone (404 `results.host_unavailable`). */
  | { kind: "host" }
  /** Any other 404 or 400: the route has nothing for this run (an older server, no slice). */
  | { kind: "missing" };

/** The text of a refusal's body, whatever its shape (`{"detail": ...}`, `{"code": ...}` or plain text); "" when it cannot be read. */
async function bodyText(res: { text?: () => Promise<string> }): Promise<string> {
  try {
    return typeof res.text === "function" ? await res.text() : "";
  } catch {
    return "";
  }
}

/**
 * A cut route's bytes, with the refusals told apart: a plane that misses the model is not a missing slice.
 * The code is looked for anywhere in the refusal's body, so the server may carry it as `detail` or as `code`.
 * Any failure other than a 400 or 404 throws.
 */
export async function fetchCut(client: { apiBase: string; token: string }, route: string, signal?: AbortSignal): Promise<CutAnswer> {
  const res = await fetch(client.apiBase.replace(/\/$/, "") + route, { headers: { "X-Stokes-Token": client.token }, cache: "no-store", signal });
  if (res.status === 404 || res.status === 400) {
    const body = await bodyText(res);
    if (body.includes(SLICE_CODES.empty)) return { kind: "empty" };
    if (body.includes(SLICE_CODES.host)) return { kind: "host" };
    return { kind: "missing" };
  }
  if (!res.ok) throw new Error(`results: HTTP ${res.status}`);
  return { kind: "ok", bytes: await res.arrayBuffer() };
}

/**
 * The bytes of one of the routes above, with the token the client holds. A 404 (unknown run or patch, no
 * cache, no slice) and a 400 (a field the server does not have) are `null`: the surface keeps its own
 * colour, never zeros. Any other failure throws.
 */
export async function fetchResultBytes(client: { apiBase: string; token: string }, route: string, signal?: AbortSignal): Promise<ArrayBuffer | null> {
  const res = await fetch(client.apiBase.replace(/\/$/, "") + route, { headers: { "X-Stokes-Token": client.token }, cache: "no-store", signal });
  if (res.status === 404 || res.status === 400) return null;
  if (!res.ok) throw new Error(`results: HTTP ${res.status}`);
  return res.arrayBuffer();
}
