/**
 * The Mesh panel's logic (ADR-0024, T-211b): what each state of a mesh result is called, how far a
 * run has come, whether Generate and Cancel can be pressed and why not, and how each quality number
 * stands against its threshold. Pure functions over what the server sends (the `mesh_result` node,
 * the `mesh.*` events, `mesh.meshers`), so every state and verdict is tested in Node and
 * `MeshPanel.tsx` stays a thin renderer.
 *
 * Import rule: no React, no store, no DOM. Types come from `../api/types` as types only.
 *
 * Where the facts are (read these, do not guess them):
 *
 * * the result node `/mesh/results/<mesher>`: `params.state`, `cells`, `points`, `faces`, `seconds`,
 *   `ok`, `error`, `quality`; `meta.warnings`, `meta.patch_tags`, `meta.patch_types`; `status` is
 *   `stale` when geometry or tags moved on, and `warning` when the quality missed the bar
 *   (`stokes/mesh/service.py`);
 * * the events `mesh.progress {node_id, step, stage, fraction}`, `mesh.log {node_id, step, lines}`,
 *   `mesh.done`, `mesh.failed`, `mesh.cancelled`, `mesh.skipped` (the service's own names; the
 *   agent's `job.state` and `log.lines` never reach a client directly).
 */

import type { NodeStatus, NodeSummary } from "../api/types";
import { activeResultOf, meshResultsOf } from "../state/meshPatches";
import { followsShape } from "./mesherKinds";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

// ---------------------------------------------------------------------------------------------- the mesher catalogue
/** One entry of `mesh.meshers` (`stokes/mesh/catalogue.py`): `built` false carries the reason in `why`. */
export interface MesherInfo {
  id: string;
  label: string;
  built: boolean;
  domains: string[];
  why: string;
  /**
   * The output-structure families this mesher's **validated** recipe produces.
   *
   * **Empty on today's trunk**: the field is additive on the mesh branch and not merged. An empty tuple
   * therefore means "this build does not say", **not** "this mesher has no family" - which is why the
   * outline falls back to the flat list rather than showing an empty family.
   *
   * The closed ids are `structured-hexahedral`, `hex-dominant`, `tetrahedral` and `polyhedral`; **only the
   * first two carry a validated recipe today**, and an unvalidated mode claims none.
   */
  outputStructures: string[];
  /** How `cores` is spent: `mpi`, `threads` or `serial` (ADR-0032); "" from a server that does not say. */
  parallel?: string;
}

/** `mesh.meshers`, read defensively: an entry without an id is dropped, a payload that is not ours gives none. */
export function readMeshers(payload: unknown): MesherInfo[] {
  if (!isRecord(payload) || !Array.isArray(payload.meshers)) return [];
  const out: MesherInfo[] = [];
  for (const entry of payload.meshers) {
    if (!isRecord(entry) || text(entry.id) === "") continue;
    out.push({
      id: text(entry.id),
      label: text(entry.label) || text(entry.id),
      built: entry.built === true,
      domains: strings(entry.domains),
      why: text(entry.why),
      /*
        **Additive and optional by design**: a trunk build has no `output_structures` key, and an absent
        field must read as "not stated" rather than as a refused family. The reader takes only strings from
        the closed set - an unknown id from a newer build is kept, because the outline groups by what the
        catalogue says and ignores what it does not know.
      */
      outputStructures: strings(entry.output_structures),
      parallel: text(entry.parallel),
    });
  }
  return out;
}

/**
 * The steps a mesher's job runs, in order (`stokes/mesh/drivers/blockmesh.py: steps`). The events carry
 * a step's name but not how many there are, so "step 1 of 2" needs this table. A mesher not listed has
 * no known count and the line says the step's name alone.
 */
export const MESHER_STEPS: Readonly<Record<string, readonly string[]>> = {
  blockmesh: ["blockMesh", "checkMesh"],
  // The drivers' own step names (stokes/mesh/drivers). Only blockMesh was listed, so every other mesher showed
  // an indeterminate bar for its whole run (the elbow: 90 s at what read as a fixed 35 %). A step a run does
  // not have (decomposePar on one core, extrudeMesh off 2D) is never reported, so the bar skips past it.
  snappy: ["blockMesh", "surfaceFeatures", "decomposePar", "snappyHexMesh", "reconstructPar", "extrudeMesh", "checkMesh"],
  // A planar symmetry plane is snapped onto its plane as far as the cells allow (agent `flatten` action).
  cfmesh: ["cartesianMesh", "flattenSymmetry", "checkMesh"],
  cfmesh_tet: ["tetMesh", "flattenSymmetry", "checkMesh"],
  // A planar symmetry face is flattened and its type set back (stokes/mesh/drivers/cfmesh.py).
  cfmesh_poly: ["pMesh", "improveSymmetryPlanes", "symmetryType", "checkMesh"],
  polyhex: ["cartesianMesh", "polyDualMesh", "checkMesh"],
};

export function stepsOf(mesher: string): readonly string[] {
  return MESHER_STEPS[mesher] ?? [];
}

// ---------------------------------------------------------------------------------------------- the result
/** The states of a `mesh_result` (`RESULT_STATES` in `stokes/mesh/model.py`), plus `none` for no result at all. */
export type MeshPhase = "none" | "queued" | "running" | "cancelling" | "done" | "failed" | "cancelled" | "interrupted" | "skipped";

export const MESH_PHASES: readonly MeshPhase[] = ["none", "queued", "running", "cancelling", "done", "failed", "cancelled", "interrupted", "skipped"];

/** A state in flight: a job exists and nothing has ended it. */
export function isActive(phase: MeshPhase): boolean {
  return phase === "queued" || phase === "running" || phase === "cancelling";
}

function phaseOf(value: unknown): MeshPhase {
  const s = text(value);
  return (MESH_PHASES as readonly string[]).includes(s) && s !== "none" ? (s as MeshPhase) : "queued";
}

/** The quality numbers of a finished mesh; `null` is "the log did not carry it" and is never a pass. */
export interface MeshQuality {
  maxNonOrtho: number | null;
  maxSkewness: number | null;
  maxAspectRatio: number | null;
  failedChecks: number | null;
  /** OpenFOAM's own verdict ("Mesh OK."), or null when there is no report. */
  meshOk: boolean | null;
  /** The project's thresholds the server recorded with the numbers. */
  limitNonOrtho: number | null;
  limitSkewness: number | null;
}

/**
 * How closely the mesh follows the CAD, as the server measured it.
 *
 * Claude's 1115 spec: four axes, each with the threshold it is judged against, so the card can show
 * a value and its limit the way the checkMesh rows already do. Every field is optional because the
 * measurement may be absent — a mesh whose surfaces are open has no volume error, and a part with
 * no sharp edges has nothing to capture. `null` is "not measured", which must never read as a pass.
 */
export interface MeshFidelity {
  maxDeviationMm: number | null;
  maxDeviationPct: number | null;
  meanDeviationMm: number | null;
  meanDeviationPct: number | null;
  volumeErrorPct: number | null;
  featureCapturePct: number | null;
  characteristicLengthMm: number | null;
  /** The CAD's sharp edges. **Zero means the capture could not be measured**, not 100 %. */
  features: number | null;
  sampled: number | null;
  points: number | null;
  /** "Good" | "Acceptable" | "Poor", as the server judged it. */
  verdict: string;
  limitMaxPct: number | null;
  limitMeanPct: number | null;
  limitVolumePct: number | null;
  limitCapturePct: number | null;
}

/** The fidelity block, or null when the record has none. */
export function readFidelity(quality: unknown): MeshFidelity | null {
  if (!isRecord(quality)) return null;
  const f = isRecord(quality.fidelity) ? quality.fidelity : null;
  if (f === null) return null;
  const limits = isRecord(f.thresholds) ? f.thresholds : {};
  return {
    maxDeviationMm: num(f.max_deviation_mm),
    maxDeviationPct: num(f.max_deviation_pct),
    meanDeviationMm: num(f.mean_deviation_mm),
    meanDeviationPct: num(f.mean_deviation_pct),
    volumeErrorPct: num(f.volume_error_pct),
    featureCapturePct: num(f.feature_capture_pct),
    characteristicLengthMm: num(f.characteristic_length_mm),
    features: num(f.features),
    sampled: num(f.sampled),
    points: num(f.points),
    verdict: text(f.verdict),
    limitMaxPct: num(limits.max_deviation_pct),
    limitMeanPct: num(limits.mean_deviation_pct),
    limitVolumePct: num(limits.volume_error_pct),
    limitCapturePct: num(limits.feature_capture_pct),
  };
}

/** A `mesh_result` node as the panel shows it. */
export interface MeshResultView {
  id: string;
  path: string;
  name: string;
  phase: MeshPhase;
  /** The node's own status: `stale` means geometry or tags changed since this mesh was made. */
  status: NodeStatus;
  mesher: string;
  host: string;
  cells: number;
  points: number;
  faces: number;
  seconds: number;
  /** The server's verdict: OpenFOAM's AND the project's thresholds. */
  ok: boolean;
  error: string;
  hasQuality: boolean;
  /** The CAD-fidelity block, or null when the mesh was not measured against its CAD. */
  fidelity: MeshFidelity | null;
  quality: MeshQuality;
  warnings: string[];
  /** Box side to tag id (`meta.patch_tags`), the boundary mapping the mesh was written with. */
  patchTags: Record<string, string>;
}

const NO_QUALITY: MeshQuality = {
  maxNonOrtho: null,
  maxSkewness: null,
  maxAspectRatio: null,
  failedChecks: null,
  meshOk: null,
  limitNonOrtho: null,
  limitSkewness: null,
};

/** The `quality` json of a result, read number by number. */
export function readQuality(value: unknown): MeshQuality {
  if (!isRecord(value)) return NO_QUALITY;
  const limits = isRecord(value.thresholds) ? value.thresholds : {};
  return {
    maxNonOrtho: num(value.max_non_ortho),
    maxSkewness: num(value.max_skewness),
    maxAspectRatio: num(value.max_aspect_ratio),
    failedChecks: num(value.failed_checks),
    meshOk: typeof value.mesh_ok === "boolean" ? value.mesh_ok : null,
    limitNonOrtho: num(limits.max_non_ortho),
    limitSkewness: num(limits.max_skewness),
  };
}

/** The result node as a view; null for no node. */
export function readResult(node: NodeSummary | null): MeshResultView | null {
  if (node === null) return null;
  const p = node.params as Record<string, unknown>;
  const meta = isRecord(node.meta) ? node.meta : {};
  const tags: Record<string, string> = {};
  if (isRecord(meta.patch_tags)) for (const [side, id] of Object.entries(meta.patch_tags)) if (typeof id === "string") tags[side] = id;
  return {
    id: node.id,
    path: node.path,
    name: node.name,
    phase: phaseOf(p.state),
    status: node.status,
    mesher: text(p.mesher),
    host: text(p.host),
    cells: num(p.cells) ?? 0,
    points: num(p.points) ?? 0,
    faces: num(p.faces) ?? 0,
    seconds: num(p.seconds) ?? 0,
    ok: p.ok === true,
    error: text(p.error),
    hasQuality: isRecord(p.quality) && Object.keys(p.quality).length > 0,
    quality: readQuality(p.quality),
    fidelity: readFidelity(p.quality),
    warnings: strings(meta.warnings),
    patchTags: tags,
  };
}

/**
 * `/mesh/settings` and the result IN USE under `/mesh/results` (`activeResultOf`: one result per mesher is
 * kept, ADR-0034), found in the tree; either may be absent.
 */
export function findMeshNodes(tree: NodeSummary | null): { settings: NodeSummary | null; result: NodeSummary | null } {
  const mesh = tree?.children?.find((c) => c.path === "/mesh") ?? null;
  const settings = mesh?.children?.find((c) => c.type === "mesh_settings") ?? null;
  return { settings, result: activeResultOf(tree) };
}

/** One kept mesh as the "Mesh to use" picker lists it. */
export interface KeptMesh {
  id: string;
  mesher: string;
  /** "Polyhedral (cfMesh pMesh) · 79,149 cells". */
  label: string;
  active: boolean;
}

/**
 * The finished meshes a project keeps, one per mesher (ADR-0034), for the picker: each with the mesher's
 * own label when `labels` has it. Results still meshing or that failed are left out (there is nothing to
 * show or solve on), and the list is empty when there is only one mesh, since then there is no choice.
 */
export function keptMeshes(tree: NodeSummary | null, labels: ReadonlyMap<string, string>): KeptMesh[] {
  const active = activeResultOf(tree);
  const out: KeptMesh[] = [];
  for (const node of meshResultsOf(tree)) {
    const view = readResult(node);
    if (view === null || view.phase !== "done") continue;
    const mesher = typeof node.params.mesher === "string" ? node.params.mesher : "";
    out.push({ id: node.id, mesher, label: `${labels.get(mesher) ?? (mesher || node.name)} · ${formatCount(view.cells)} cells`, active: active !== null && node.id === active.id });
  }
  return out.length > 1 ? out : [];
}

/** True when the result says the mesh no longer matches the geometry, the tags or the settings. */
export function isStale(view: MeshResultView | null): boolean {
  return view !== null && view.status === "stale" && view.phase === "done";
}

// ---------------------------------------------------------------------------------------------- the events
/** A `mesh.*` event, typed; null for another event or a payload that is not one of ours. */
export type MeshEvent =
  | { kind: "progress"; nodeId: string; step: string; stage: string; fraction: number | null }
  | { kind: "log"; nodeId: string; step: string; lines: string[] }
  | { kind: "done" | "cancelled"; nodeId: string }
  | { kind: "failed" | "skipped"; nodeId: string; code: string; message: string };

export function readMeshEvent(event: string, data: Record<string, unknown>): MeshEvent | null {
  const nodeId = text(data.node_id);
  if (nodeId === "") return null;
  switch (event) {
    case "mesh.progress": {
      const raw = num(data.fraction);
      return { kind: "progress", nodeId, step: text(data.step), stage: text(data.stage), fraction: raw === null ? null : Math.min(1, Math.max(0, raw)) };
    }
    case "mesh.log":
      return { kind: "log", nodeId, step: text(data.step), lines: strings(data.lines) };
    case "mesh.done":
      return { kind: "done", nodeId };
    case "mesh.cancelled":
      return { kind: "cancelled", nodeId };
    case "mesh.failed":
    case "mesh.skipped":
      return { kind: event === "mesh.failed" ? "failed" : "skipped", nodeId, code: text(data.code), message: text(data.message) };
    default:
      return null;
  }
}

/** The most log lines kept per step, and shown in all. */
export const LOG_CAP = 200;

/** What the panel keeps of a run that the tree does not hold: the step, the stage and the log. */
export interface MeshLive {
  nodeId: string | null;
  /** The step the last progress event named ("blockMesh"), or "". */
  step: string;
  /** The last stage reported ("running", "cancelling", "done" for a step that ended). */
  stage: string;
  /** When the last event for this run arrived, in ms. */
  at: number;
  /** The log by step, in the order the steps began. An array, not an object: a step name is data from the wire. */
  logs: { step: string; lines: string[] }[];
}

export const NO_LIVE: MeshLive = { nodeId: null, step: "", stage: "", at: 0, logs: [] };

function appendLines(logs: MeshLive["logs"], step: string, lines: readonly string[]): MeshLive["logs"] {
  const at = logs.findIndex((l) => l.step === step);
  if (at < 0) return [...logs, { step, lines: lines.slice(-LOG_CAP) }];
  return logs.map((l, i) => (i === at ? { step, lines: [...l.lines, ...lines].slice(-LOG_CAP) } : l));
}

/**
 * The live state after one event. An event for a node other than the one being followed starts a new
 * run (the service keeps one result and replaces it on every generate), so the old run's log never
 * shows under the new one.
 */
export function applyMeshEvent(live: MeshLive, event: MeshEvent, now: number): MeshLive {
  const base = live.nodeId === event.nodeId ? live : { ...NO_LIVE, nodeId: event.nodeId };
  switch (event.kind) {
    case "progress":
      return { ...base, step: event.step !== "" ? event.step : base.step, stage: event.stage, at: now };
    case "log":
      return { ...base, at: now, logs: appendLines(base.logs, event.step, event.lines) };
    default:
      return { ...base, stage: event.kind, at: now };
  }
}

/**
 * Merge the tail `mesh.log` returned for the running step. The tail is the agent's own file and is
 * complete where the events (throttled to ten a second, so a burst can lose lines) are not: it
 * replaces the step's lines when it is at least as long.
 */
export function mergeLogTail(live: MeshLive, step: string, lines: readonly string[]): MeshLive {
  if (step === "" || lines.length === 0) return live;
  const have = live.logs.find((l) => l.step === step)?.lines.length ?? 0;
  if (lines.length < have) return live;
  const at = live.logs.findIndex((l) => l.step === step);
  const kept = lines.slice(-LOG_CAP);
  const logs = at < 0 ? [...live.logs, { step, lines: kept }] : live.logs.map((l, i) => (i === at ? { step, lines: kept } : l));
  return { ...live, logs };
}

/** The log as the panel prints it: a heading line per step, then its lines, the last `LOG_CAP` in all. */
export function logText(live: MeshLive): string[] {
  const out: string[] = [];
  for (const entry of live.logs) {
    if (entry.step !== "") out.push(`--- ${entry.step} ---`);
    out.push(...entry.lines);
  }
  return out.slice(-LOG_CAP);
}

// ---------------------------------------------------------------------------------------------- progress and the state line
/**
 * The phase to show now. The node says `running` until the job's own end; a cancel that has been
 * asked for is reported by the job as stage `cancelling` (or set by the panel when Cancel is
 * pressed), and that is what the user should read in the meantime.
 */
export function phaseNow(view: MeshResultView | null, live: MeshLive): MeshPhase {
  const phase = view?.phase ?? "none";
  if (phase === "running" && view !== null && live.nodeId === view.id && live.stage === "cancelling") return "cancelling";
  return phase;
}

export interface MeshProgress {
  /** 0..1, or null when nothing says (an indeterminate bar). */
  fraction: number | null;
  /** The 1-based step now running, or null when the step is unknown. */
  stepNumber: number | null;
  stepCount: number;
  stepName: string;
}

/**
 * How far the run has come, from the step's NAME rather than the event's `fraction`: a step that ends
 * is reported as `stage: "done"` with `fraction: 1`, which says the step is over and not the job, so
 * the number cannot be trusted for the first of two steps.
 */
export function progressOf(view: MeshResultView | null, live: MeshLive): MeshProgress {
  const phase = phaseNow(view, live);
  const steps = stepsOf(view?.mesher ?? "");
  const stepName = isActive(phase) ? live.step : "";
  if (phase === "done") return { fraction: 1, stepNumber: null, stepCount: steps.length, stepName: "" };
  if (!isActive(phase)) return { fraction: null, stepNumber: null, stepCount: steps.length, stepName: "" };
  const at = steps.indexOf(stepName);
  if (at < 0) return { fraction: phase === "queued" ? 0 : null, stepNumber: null, stepCount: steps.length, stepName };
  const ended = live.stage === "done" ? 1 : 0;
  return { fraction: Math.min(1, (at + ended) / steps.length), stepNumber: at + 1, stepCount: steps.length, stepName };
}

export type Tone = "neutral" | "busy" | "ok" | "warn" | "bad";

/** Cell counts with separators, in the reader's locale-free form so the tests do not depend on one. */
export function formatCount(value: number): string {
  return Math.round(value).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** Seconds as the card says them: "3.2 s", "1 min 5 s". */
export function formatSeconds(seconds: number): string {
  if (!(seconds > 0)) return "";
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)} min ${whole % 60} s`;
}

/** The one-line state under the buttons, and the tone its dot or icon takes. */
export function stateLine(view: MeshResultView | null, live: MeshLive): { text: string; tone: Tone } {
  const phase = phaseNow(view, live);
  switch (phase) {
    case "none":
      return { text: "No mesh yet", tone: "neutral" };
    case "queued":
      return { text: "Waiting to start", tone: "busy" };
    case "running": {
      const p = progressOf(view, live);
      if (p.stepNumber !== null) return { text: `Meshing: step ${p.stepNumber} of ${p.stepCount} (${p.stepName})`, tone: "busy" };
      return { text: p.stepName !== "" ? `Meshing: ${p.stepName}` : "Meshing", tone: "busy" };
    }
    case "cancelling":
      return { text: "Stopping", tone: "busy" };
    case "done": {
      const took = formatSeconds(view?.seconds ?? 0);
      const base = `Finished: ${formatCount(view?.cells ?? 0)} cells${took === "" ? "" : ` in ${took}`}`;
      if (isStale(view)) return { text: `${base}. Out of date`, tone: "warn" };
      return view?.ok === true ? { text: base, tone: "ok" } : { text: `${base}. Quality not met`, tone: "warn" };
    }
    case "failed":
      return { text: `Failed${view?.error ? `: ${view.error}` : ""}`, tone: "bad" };
    case "cancelled":
      return { text: "Cancelled", tone: "neutral" };
    case "interrupted":
      return { text: "Interrupted: the agent stopped reporting this job", tone: "warn" };
    case "skipped":
      return { text: `Skipped: ${view?.error ? view.error.replace(/^[a-z_.]+:\s*/, "") : "no host was available"}`, tone: "warn" };
  }
}

// ---------------------------------------------------------------------------------------------- quality against thresholds
export type Verdict = "pass" | "warn" | "unknown";

/** OpenFOAM's checkMesh flags aspect ratios above this itself; the project has no setting for it. */
export const ASPECT_RATIO_LIMIT = 1000;

export interface QualityRow {
  key:
    | "maxNonOrtho"
    | "maxSkewness"
    | "maxAspectRatio"
    | "failedChecks"
    | "maxDeviation"
    | "meanDeviation"
    | "volumeError"
    | "featureCapture";
  /** Which block the row belongs to: OpenFOAM's own checks, or the CAD fidelity measurement. */
  group?: "quality" | "fidelity";
  label: string;
  value: number | null;
  /** The number as shown ("54.2 deg"), or "not reported". */
  valueText: string;
  limit: number;
  limitText: string;
  verdict: Verdict;
  /** The word that carries the verdict where colour cannot: "within limit", "above limit", "not measured". */
  verdictText: string;
}

/** A value against a limit: at or below passes, above warns, a missing number is unknown and never a pass. */
export function verdictOf(value: number | null, limit: number): Verdict {
  if (value === null) return "unknown";
  return value <= limit ? "pass" : "warn";
}

function digits(value: number): string {
  return Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(value >= 10 ? 1 : 2);
}

/**
 * The four quality rows (max non-orthogonality, max skewness, max aspect ratio, failed checks), each
 * with its limit. The first two limits are the project's own, recorded with the numbers
 * (`quality.thresholds`), falling back to the settings' defaults of 70 and 4 when an older result has
 * none; the aspect ratio is checked against checkMesh's 1000.
 */
export function qualityRows(view: MeshResultView | null): QualityRow[] {
  if (view === null || !view.hasQuality) return [];
  const q = view.quality;
  const row = (key: QualityRow["key"], label: string, value: number | null, limit: number, unit: string, show: (v: number) => string): QualityRow => {
    const verdict = verdictOf(value, limit);
    return {
      key,
      label,
      value,
      valueText: value === null ? "not reported" : `${show(value)}${unit}`,
      limit,
      limitText: key === "failedChecks" ? "none allowed" : `limit ${show(limit)}${unit}`,
      verdict,
      verdictText: verdict === "pass" ? "within limit" : verdict === "warn" ? "above limit" : "not measured",
    };
  };
  return [
    row("maxNonOrtho", "Max non-orthogonality", q.maxNonOrtho, q.limitNonOrtho ?? 70, " deg", digits),
    row("maxSkewness", "Max skewness", q.maxSkewness, q.limitSkewness ?? 4, "", digits),
    row("maxAspectRatio", "Max aspect ratio", q.maxAspectRatio, ASPECT_RATIO_LIMIT, "", digits),
    row("failedChecks", "Failed checks", q.failedChecks, 0, "", (v) => String(Math.round(v))),
  ];
}

/**
 * The Fidelity block: how closely the mesh follows the CAD, per Claude's 1115 spec.
 *
 * **A separate block, and deliberately not folded into `qualityRows`.** OpenFOAM's checks ask
 * whether the mesh is well formed; these ask whether it is the RIGHT SHAPE, which is a different
 * question with a different verdict word (Good / Acceptable / Poor rather than within/above limit).
 * A user who sees "Mesh OK" should be able to read underneath it that the mesh also followed the
 * part - or that it did not.
 *
 * **A missing number is never a pass.** Each row is `unknown` when the measurement is absent, and
 * the capture row is `unknown` rather than 0 % when the CAD had no sharp edges to capture, because
 * "nothing to measure" and "measured nothing" are different claims.
 */
export function fidelityRows(view: MeshResultView | null): QualityRow[] {
  const f = view?.fidelity ?? null;
  if (f === null) return [];
  const row = (
    key: QualityRow["key"],
    label: string,
    value: number | null,
    limit: number | null,
    unit: string,
    show: (v: number) => string,
  ): QualityRow => {
    // Lower is better for three of the four; capture is the one where MORE is better, so it gets
    // its own comparison rather than being inverted into a "distance".
    const verdict: Verdict =
      value === null || limit === null
        ? "unknown"
        : key === "featureCapture"
          ? value >= limit
            ? "pass"
            : "warn"
          : value <= limit
            ? "pass"
            : "warn";
    return {
      key,
      group: "fidelity",
      label,
      value,
      valueText: value === null ? "not measured" : `${show(value)}${unit}`,
      limit: limit ?? 0,
      limitText: limit === null ? "no limit" : `${key === "featureCapture" ? "at least" : "limit"} ${show(limit)}${unit}`,
      verdict,
      verdictText: value === null ? "not measured" : verdict === "pass" ? "within limit" : "above limit",
    };
  };
  const mm = (v: number): string => (v < 0.01 ? v.toFixed(4) : v.toFixed(3));
  const pct = (v: number): string => v.toFixed(2);
  return [
    row("maxDeviation", "Max deviation from CAD", f.maxDeviationMm, f.limitMaxPct === null ? null : (f.limitMaxPct / 100) * (f.characteristicLengthMm ?? 0), " mm", mm),
    row("meanDeviation", "Mean deviation from CAD", f.meanDeviationMm, f.limitMeanPct === null ? null : (f.limitMeanPct / 100) * (f.characteristicLengthMm ?? 0), " mm", mm),
    row("volumeError", "Volume error", f.volumeErrorPct, f.limitVolumePct, " %", pct),
    row("featureCapture", "Feature capture", f.featureCapturePct, f.limitCapturePct, " %", pct),
  ];
}

/** The fidelity verdict word, as the server judged it: Good, Acceptable or Poor. */
export function fidelityVerdict(view: MeshResultView | null): string {
  return view?.fidelity?.verdict ?? "";
}

/** The worst of a set of verdicts, for the card's header. */
export function worstVerdict(rows: readonly QualityRow[]): Verdict {
  if (rows.some((r) => r.verdict === "warn")) return "warn";
  if (rows.length === 0 || rows.some((r) => r.verdict === "unknown")) return "unknown";
  return "pass";
}

// ---------------------------------------------------------------------------------------------- the run controls
export interface GenerateInput {
  connected: boolean;
  hasProject: boolean;
  phase: MeshPhase;
  stale: boolean;
  domain: string;
  /** True when the project has a part to mesh. */
  hasPart: boolean;
  /** True when the box holds six numbers. */
  boxFilled: boolean;
  /** The chosen mesher's catalogue entry; null until `mesh.meshers` has answered. */
  mesher: MesherInfo | null;
  /** The settings' host as `host.list` reports it, or null while that is not known. */
  host: { name: string; available: boolean; why: string } | null;
  /** A configure or generate call is in flight from this panel. */
  busy: boolean;
}

export interface GenerateState {
  enabled: boolean;
  /** Why it is disabled, or a one-line hint when it is not. */
  reason: string;
  label: string;
  /** Run again although nothing that decides the mesh has changed (`mesh.generate force`). */
  force: boolean;
}

/**
 * Whether Generate can be pressed. The backend's own refusal is the source of truth for anything
 * only it can know (an unmapped tag, a part with no display mesh yet): those arrive as the command's
 * error and are shown as it words them. What is checked here is what the panel can know without
 * asking, so a button is not offered that cannot work.
 */
export function generateState(input: GenerateInput): GenerateState {
  const label = input.stale ? "Regenerate" : input.phase === "done" ? "Generate again" : "Generate";
  const force = input.phase === "done" && !input.stale;
  const no = (reason: string): GenerateState => ({ enabled: false, reason, label, force });
  if (!input.connected) return no("Not connected to the Stokes server.");
  if (!input.hasProject) return no("Open a project first.");
  if (isActive(input.phase)) return no("A mesh is already running.");
  if (input.busy) return no("Working on the last request.");
  if (input.mesher !== null && !input.mesher.built) return no(`${input.mesher.label} is not available: ${input.mesher.why}`);
  if (input.domain === "box") {
    if (!input.boxFilled) return no("Give the box six numbers (min and max on x, y and z).");
  } else if (!input.hasPart) {
    return no("There is no part to mesh. Import geometry, or choose the box domain.");
  }
  if (input.host !== null && !input.host.available) {
    // **One sentence, and the reason is already a sentence** (claude-win2's GUI review item 6: the
    // card showed *"No host can mesh: No OpenFOAM on this machine: add a Linux host…"* - two prefixes
    // and a doubled colon, which reads like an error log). `why` says what is missing and what to do,
    // so prefixing it only repeats the colon; a host with no reason gets the short form.
    return no(input.host.why !== "" ? input.host.why : `No host can mesh: ${input.host.name} is not available.`);
  }
  return { enabled: true, reason: "", label, force };
}

/** Cancel is offered while a job is in flight, and not again once it has been asked to stop. */
export function cancelState(phase: MeshPhase): { visible: boolean; enabled: boolean; reason: string } {
  if (!isActive(phase)) return { visible: false, enabled: false, reason: "" };
  if (phase === "cancelling") return { visible: true, enabled: false, reason: "Already stopping." };
  return { visible: true, enabled: true, reason: "" };
}

/** `mesh.status` is asked every 2 s while a job is in flight, in case an event was missed (a reconnect). */
export const POLL_MS = 2000;

export function shouldPoll(phase: MeshPhase): boolean {
  return isActive(phase);
}

// ---------------------------------------------------------------------------------------------- the settings form
/** The settings the form shows for a mesher, in order. `box` has its own editor; snappy's size only matters to snappy. */
export function visibleSettings(mesher: string, domain: string): string[] {
  const names = ["host", "domain"];
  if (domain === "box") names.push("box");
  else names.push("domain_part");
  names.push("base_size");
  if (followsShape(mesher)) names.push("surface_size");
  names.push("margin", "max_cells", "cores", "max_non_ortho", "max_skewness");
  return names;
}

/** The length unit a settings quantity shows in ("mm" from "5 mm"), so the box's bare numbers read in it; "" when unknown. */
export function lengthUnitOf(displays: Record<string, string> | undefined): string {
  for (const key of ["base_size", "margin", "surface_size"]) {
    const m = /^\S+\s+(\S+)$/.exec((displays?.[key] ?? "").trim());
    if (m) return m[1];
  }
  return "";
}

const BARE_NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

/** Metres per unit of the length units a display system can name (the Properties panel's table). */
export const METRES_PER: Readonly<Record<string, number>> = { m: 1, mm: 1e-3, cm: 1e-2, um: 1e-6, km: 1e3, in: 0.0254, ft: 0.3048 };

/** The unit the box's six fields are written in: the project's length unit when it is one of those, else metres. */
export function boxUnit(displays: Record<string, string> | undefined): string {
  const unit = lengthUnitOf(displays);
  return unit in METRES_PER ? unit : "m";
}

/** The stored box (SI metres) as the six texts the fields show, in `unit`; six empty strings when it is not set. */
export function boxTexts(box: unknown, unit: string): string[] {
  if (!boxFilled(box)) return ["", "", "", "", "", ""];
  const per = METRES_PER[unit] ?? 1;
  return (box as number[]).map((v) => String(Number((v / per).toPrecision(6))));
}

/** One box entry as `mesh.configure` takes it: a bare number is read in `unit` ("5" with "mm" is "5 [mm]"); text with its own unit goes as it is. */
export function boxEntry(raw: string, unit: string): string | null {
  const t = raw.trim();
  if (t === "") return null;
  if (BARE_NUMBER.test(t)) return unit !== "" ? `${t} [${unit}]` : t;
  return t;
}

/** The six box entries as `values.box`; null while any is empty. A bare number with no known unit goes as metres, the model's own. */
export function boxValue(entries: readonly string[], unit: string): (string | number)[] | null {
  if (entries.length !== 6) return null;
  const out: (string | number)[] = [];
  for (const e of entries) {
    const v = boxEntry(e, unit);
    if (v === null) return null;
    out.push(BARE_NUMBER.test(v) ? Number(v) : v);
  }
  return out;
}

/** Whether the settings' stored box holds six numbers. */
export function boxFilled(box: unknown): boolean {
  return Array.isArray(box) && box.length === 6 && box.every((v) => typeof v === "number" && Number.isFinite(v));
}

// ---------------------------------------------------------------------------------------------- the boundary mapping
export interface MappingRow {
  side: string;
  /** The tag's name, or the id when the tag is gone from the tree. */
  tag: string;
  /** False when the tag no longer exists: the mesh was made from a tag that was since deleted. */
  known: boolean;
}

const SIDE_ORDER = ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax"];

/** Which tag each box side's patch came from, in side order; `nameOf` looks a tag id up in the tree. */
export function mappingRows(view: MeshResultView | null, nameOf: (id: string) => string | null): MappingRow[] {
  if (view === null) return [];
  const sides = Object.keys(view.patchTags).sort((a, b) => SIDE_ORDER.indexOf(a) - SIDE_ORDER.indexOf(b));
  return sides
    .filter((side) => view.patchTags[side] !== "")
    .map((side) => {
      const id = view.patchTags[side];
      const name = nameOf(id);
      return { side, tag: name ?? id, known: name !== null };
    });
}

// ---------------------------------------------------------------------------------------------- tree rows
/** The muted text on the right of a `/mesh` node's tree row; "" for a node that is not one of the mesh types. */
export function meshTreeValue(node: NodeSummary): string {
  const p = node.params as Record<string, unknown>;
  if (node.type === "mesh_settings") return [text(p.mesher), node.displays?.base_size ?? ""].filter((t) => t !== "").join(" · ");
  if (node.type !== "mesh_result") return "";
  const view = readResult(node);
  if (view === null) return "";
  if (view.phase === "done") return `${formatCount(view.cells)} cells`;
  return stateLine(view, NO_LIVE).text.replace(/:.*$/, "");
}
