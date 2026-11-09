/**
 * What the front end keeps about geometry operations (ADR-0021 decision 8): the type catalogue
 * `operation.types` answers, the runs under way from the `geometry.op_*` events, and the small
 * pieces the operation card (T-029) and the progress line share. Pure functions over the wire's
 * data, as `state/parts.ts` is for the import: the store holds the state and calls these.
 *
 * An operation node's status and its outputs are the server's alone; what is kept here is only what
 * a card and a progress line need to show (the label, the stage, how far the run has come and the
 * message of a failure), all of it read defensively from the event payloads.
 *
 * Import rule: no React and no store, so the tests run in Node (`npm.cmd test`).
 */

import type { NodeTypeSchema, ParamSchema, UnitsSchema } from "../api/types";
import { formatNumber } from "../util/units";

// ---------------------------------------------------------------------------------------------- the type catalogue
/** One entry of `operation.types`: the type, its label and the schema of its own parameters. */
export interface OperationTypeInfo {
  type: string;
  label: string;
  /** False for a type whose module is not built yet: the card is not offered for it. */
  built: boolean;
  /** Least number of input parts the run reads. */
  min_inputs: number;
  /** `json` parameters that name further parts (a boolean's `tools`). */
  part_params: string[];
  /** Parameters that name a tag whose stored members the run receives. */
  tag_params: string[];
  /** The parameters of the operation, as the node registry gives them. */
  schema: NodeTypeSchema;
}

/** The parameters every operation node carries; the command takes them as its own arguments. */
export const OPERATION_COMMON_KEYS: readonly string[] = ["inputs", "outputs", "auto_run"];

/** The parameters the card does not offer: the outputs belong to the run, not to the form. */
export const OPERATION_HIDDEN_KEYS: readonly string[] = ["outputs"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A node id, as `stokes.model.ids` makes one (`n_3fa9c2d1`): what tells a part list from a word list. */
const NODE_ID = /^[a-z]{1,4}_[0-9a-f]{8}$/;

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function finite(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** The parameters of a schema entry, checked one by one; null for one that is not an object. */
function readProperties(value: unknown): Record<string, ParamSchema> | null {
  if (!isRecord(value)) return null;
  const out: Record<string, ParamSchema> = {};
  for (const [name, schema] of Object.entries(value)) if (isRecord(schema)) out[name] = schema as ParamSchema;
  return out;
}

/**
 * `operation.types` as the store keeps it, by type. A payload that is not one of ours gives an
 * empty catalogue, and an entry without a type is dropped: the card then says the type is unknown
 * rather than rendering a half-read schema.
 */
export function readOperationTypes(payload: unknown): Record<string, OperationTypeInfo> {
  if (!isRecord(payload) || !Array.isArray(payload.types)) return {};
  const out: Record<string, OperationTypeInfo> = {};
  for (const entry of payload.types) {
    if (!isRecord(entry) || typeof entry.type !== "string" || entry.type === "") continue;
    const label = typeof entry.label === "string" && entry.label !== "" ? entry.label : entry.type;
    const raw = isRecord(entry.schema) ? entry.schema : null;
    const properties = readProperties(raw?.properties) ?? {};
    const schema: NodeTypeSchema = {
      title: typeof raw?.title === "string" && raw.title !== "" ? raw.title : label,
      description: typeof raw?.description === "string" ? raw.description : "",
      "x-type": typeof raw?.["x-type"] === "string" ? (raw["x-type"] as string) : entry.type,
      "x-children": strings(raw?.["x-children"]),
      properties,
    };
    out[entry.type] = {
      type: entry.type,
      label,
      built: entry.built === true,
      min_inputs: Math.max(0, Math.trunc(finite(entry.min_inputs, 1))),
      part_params: strings(entry.part_params),
      tag_params: strings(entry.tag_params),
      schema,
    };
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- the events
/** A `geometry.op_*` event, typed; null for another event or a payload that is not one of ours. */
export type OperationEvent =
  | { kind: "progress"; nodeId: string; stage: string; fraction: number | null; message: string }
  | { kind: "done"; nodeId: string; outputs: string[]; seconds: number }
  | { kind: "failed"; nodeId: string; code: string; message: string };

/** The `geometry.op_progress`, `.op_done` and `.op_failed` events (stokes.geometry.opstore). */
export function readOperationEvent(event: string, data: Record<string, unknown>): OperationEvent | null {
  const nodeId = typeof data.node_id === "string" && data.node_id !== "" ? data.node_id : null;
  if (nodeId === null) return null;
  switch (event) {
    case "geometry.op_progress": {
      const raw = data.fraction;
      const fraction = typeof raw === "number" && Number.isFinite(raw) ? Math.min(1, Math.max(0, raw)) : null;
      return {
        kind: "progress",
        nodeId,
        stage: typeof data.stage === "string" ? data.stage : "",
        fraction,
        message: typeof data.message === "string" ? data.message : "",
      };
    }
    case "geometry.op_done":
      return { kind: "done", nodeId, outputs: strings(data.outputs), seconds: finite(data.seconds, 0) };
    case "geometry.op_failed":
      return {
        kind: "failed",
        nodeId,
        code: typeof data.code === "string" ? data.code : "geometry.op_failed",
        message: typeof data.message === "string" && data.message !== "" ? data.message : "the operation failed",
      };
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------------------------- the runs under way
/** One run under way, as the progress line and the card show it. */
export interface OperationProgress {
  nodeId: string;
  /** The type's label ("Enclosure"); empty when the tree does not know the node yet. */
  label: string;
  /** The stage the run reported ("build", "subtract", "tag"). */
  stage: string;
  /** 0..1, or null while the run does not know. */
  fraction: number | null;
  message: string;
  /** When the last event for this run arrived, in ms: the line shows the newest of all of them. */
  at: number;
}

/** The runs under way after one progress event. */
export function noteOperationProgress(
  map: Record<string, OperationProgress>,
  event: { nodeId: string; stage: string; fraction: number | null; message: string },
  label: string,
  now: number,
): Record<string, OperationProgress> {
  const old = map[event.nodeId];
  return {
    ...map,
    [event.nodeId]: {
      nodeId: event.nodeId,
      label: label || old?.label || "",
      stage: event.stage,
      fraction: event.fraction,
      message: event.message,
      at: now,
    },
  };
}

/** The runs under way without `nodeId` (its run ended, or it was cancelled). */
export function dropOperation(map: Record<string, OperationProgress>, nodeId: string): Record<string, OperationProgress> {
  if (!(nodeId in map)) return map;
  const { [nodeId]: _gone, ...rest } = map;
  return rest;
}

/** The run that spoke last, or null while none is under way. */
export function latestOperation(map: Record<string, OperationProgress>): OperationProgress | null {
  let latest: OperationProgress | null = null;
  for (const entry of Object.values(map)) if (latest === null || entry.at > latest.at) latest = entry;
  return latest;
}

/** How far the run has come, 0..1, or null while that is not known (a run that has just started). */
export function operationFraction(p: OperationProgress): number | null {
  if (p.fraction === null || p.fraction <= 0) return null;
  return Math.min(1, p.fraction);
}

/** "Enclosure: cutting 1 body out of the enclosure 60 %", the one line the card and the line show. */
export function operationText(p: OperationProgress): string {
  const name = p.label || "Operation";
  const fraction = operationFraction(p);
  const percent = fraction === null ? null : Math.round(fraction * 100);
  if (percent === null) return p.message ? `${name}: ${p.message}…` : `${name}…`;
  return p.message ? `${name}: ${p.message} ${percent} %` : `${name}: ${percent} %`;
}

// ---------------------------------------------------------------------------------------------- the form
/** What the card's form starts from: the schema's defaults and the display text of each quantity. */
export interface OperationForm {
  values: Record<string, unknown>;
  displays: Record<string, string>;
}

/**
 * The card's initial form: every parameter of the operation type at its default (the preset wins,
 * which is how the Boolean submenu's Fuse, Cut and Common differ), with the quantity defaults
 * written as the display text `SchemaFields` reseeds a field from. The `outputs` of the type are
 * left out: the run writes them.
 */
export function initialValues(schema: NodeTypeSchema, units: UnitsSchema | null, preset: Record<string, unknown> = {}): OperationForm {
  const values: Record<string, unknown> = {};
  const displays: Record<string, string> = {};
  for (const [name, property] of Object.entries(schema.properties)) {
    if (OPERATION_HIDDEN_KEYS.includes(name)) continue;
    const given = preset[name];
    const value = given !== undefined ? given : property.default;
    if (value === undefined) continue;
    values[name] = value;
    if (property["x-kind"] === "quantity" && typeof value === "number" && Number.isFinite(value)) {
      const si = units?.dimensions[property["x-dimension"] ?? "dimensionless"]?.si_unit ?? "";
      displays[name] = si === "" ? formatNumber(value, 6) : `${formatNumber(value, 6)} ${si}`;
    }
  }
  return { values, displays };
}

/** The params `operation.create` takes: the type's own, never `inputs`, `outputs` or `auto_run`. */
export function ownParams(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(params)) if (!OPERATION_COMMON_KEYS.includes(name)) out[name] = value;
  return out;
}

/** How a run reads the pick: a boolean takes the first body and the rest as its `tools`. */
export function operationInputs(type: string, parts: readonly string[]): { inputs: string[]; tools: string[] } {
  if (type === "boolean" && parts.length > 1) return { inputs: parts.slice(0, 1), tools: parts.slice(1) };
  return { inputs: [...parts], tools: [] };
}

/** The picked faces as tag members (`state/store`'s `OperationDraft.faces`). */
export type PickedFaces = Array<{ part: string; faces: "all" | Array<number | [number, number]> }>;

/**
 * The payload a card's Run sends: the `params` `operation.create` takes, and the `inputs` it takes
 * beside them (the command has `inputs` as its own argument, so it never rides inside `params`).
 *
 * The params are the form's own, the `tools` a boolean's extra bodies become, and — for a type
 * whose schema has a `faces` parameter, such as Remove faces — the faces the pick held at the
 * moment the card opened. Without the last part a face pick could never reach `operation.create`:
 * the form offers no `faces` field, because the pick is the field, so the parameter would stay at
 * its empty default and the server would refuse the run with "needs at least one face to remove".
 */
export interface OperationCreatePayload {
  inputs: string[];
  params: Record<string, unknown>;
}

export function operationCreatePayload(
  type: string,
  params: Record<string, unknown>,
  parts: readonly string[],
  faces: PickedFaces | null,
  schema: NodeTypeSchema | undefined,
): OperationCreatePayload {
  const { inputs, tools } = operationInputs(type, parts);
  const out = ownParams({ ...params, inputs });
  if (tools.length > 0 && schema?.properties?.tools !== undefined) out.tools = tools;
  if (faces !== null && faces.length > 0 && schema?.properties?.faces !== undefined) out.faces = faces;
  return { inputs, params: out };
}

/**
 * A `json` parameter in words, for the read-only line the card shows instead of raw JSON: the
 * members of a selection ("12 faces of 2 bodies"), or a list of parts ("3 parts"). Null when the
 * value is neither — an empty list, an axis list ("x", "y") — and the field keeps its JSON block,
 * because a list of words is not a list of parts.
 */
export function operationJsonText(value: unknown): string | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const members = value.filter(isRecord);
  if (members.length === value.length) {
    let faces = 0;
    let bodies = 0;
    for (const member of members) {
      if (typeof member.part !== "string") return null;
      bodies++;
      const ids = member.faces;
      if (!Array.isArray(ids)) continue;
      for (const entry of ids) {
        if (typeof entry === "number") faces++;
        else if (Array.isArray(entry) && entry.length === 2 && entry.every((n) => typeof n === "number")) faces += Math.max(0, entry[1] - entry[0] + 1);
        else if (entry === "all") continue;
        else return null;
      }
    }
    return `${faces} face${faces === 1 ? "" : "s"} of ${bodies} bod${bodies === 1 ? "y" : "ies"}`;
  }
  if (value.every((entry) => typeof entry === "string" && NODE_ID.test(entry))) {
    const parts = value.length;
    return `${parts} part${parts === 1 ? "" : "s"}`;
  }
  return null;
}

/**
 * The seed a picked point makes: the point moved along the (outward) face normal by 1 % of the
 * part's bounding-box diagonal, so the point lands just inside the body of fluid beside the face
 * rather than on its surface. Every component is in the viewport's own unit (metres, SI).
 */
export function seedAt(point: readonly number[], normal: readonly number[], diagonal: number): [number, number, number] {
  const step = 0.01 * (Number.isFinite(diagonal) && diagonal > 0 ? diagonal : 0);
  return [point[0] - normal[0] * step, point[1] - normal[1] * step, point[2] - normal[2] * step];
}
