/**
 * The wire contract of the solve and run commands (T-214, T-217), in ONE module: every command name,
 * every event name, every wire type and the defensive readers that turn a payload into those types.
 * When the server's contract changes this is the only file that has to.
 *
 * Import rule: no React, no store, no DOM. The store and the pure models import this file; this file
 * imports nothing of ours, so there is no cycle however the other modules grow.
 *
 * The source of the shapes is `.mailbox/plan/e2e/T214-CONTRACTS.md` (as implemented and tested) and
 * the T-214b update: the states, `run.residuals` with `null` where the solver did not report a field,
 * and the events. Everything read from the wire is data: a missing or mistyped field gives a neutral
 * value, never an exception.
 */

// ---------------------------------------------------------------------------------------------- names
/** The commands the Run and Solve panels send. */
export const RUN_COMMANDS = {
  engines: "solve.engines",
  configure: "solve.configure",
  check: "solve.check",
  start: "run.start",
  cancel: "run.cancel",
  stop: "run.stop",
  resume: "run.resume",
  status: "run.status",
  list: "run.list",
  log: "run.log",
  residuals: "run.residuals",
  clear: "run.clear",
} as const;

/** The events the server pushes about a run. */
export const RUN_EVENTS = {
  started: "run.started",
  progress: "run.progress",
  residual: "run.residual",
  log: "run.log",
  finished: "run.finished",
  changed: "run.changed",
  skipped: "run.skipped",
} as const;

/** A mesh job's log lines share the job log with a run's (`mesh.log {node_id, step, lines}`, T-207). */
export const MESH_LOG_EVENT = "mesh.log";

/** The events that end the life of the project the run state belongs to. */
export const PROJECT_EVENTS: readonly string[] = ["project.opened", "project.closed"];

/** The node the Solve settings card edits (`stokes/engines/model.py`). */
export const SOLVER_SETTINGS_TYPE = "solver_settings";
export const SOLVER_SETTINGS_PATH = "/solver/settings";

/** The settings the card shows, in order (`solver_settings`: the rest are the engine's and the slice's own). */
export const SOLVE_SETTING_NAMES: readonly string[] = ["engine", "turbulence", "iterations", "write_interval", "residual_target"];

// ---------------------------------------------------------------------------------------------- small readers
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

// ---------------------------------------------------------------------------------------------- states
/** The nine states of a run record (`RUN_STATES`, T-214b). */
export const RUN_STATES = ["queued", "running", "cancelling", "done", "done_unconverged", "failed", "cancelled", "interrupted", "skipped"] as const;
export type RunState = (typeof RUN_STATES)[number];

export function readRunState(value: unknown): RunState | null {
  return typeof value === "string" && (RUN_STATES as readonly string[]).includes(value) ? (value as RunState) : null;
}

/** A state in flight: a job exists and nothing has ended it. `interrupted` is NOT in flight: the outcome is unknown and nothing will report. */
export function isRunActive(state: RunState): boolean {
  return state === "queued" || state === "running" || state === "cancelling";
}

/**
 * What a finished run measured on one patch (`monitors` of the run record: `{"<patch>": {flow_rate, p_mean}}`):
 * the volumetric flow through it and its mean pressure, each null when the solver did not report it.
 * An incompressible solver's pressure is kinematic (p/rho, m2/s2).
 */
export interface MonitorValue {
  patch: string;
  flowRate: number | null;
  pMean: number | null;
}

/** What a run record says, trimmed to what the panels show. */
export interface RunSummary {
  id: string;
  name: string;
  state: RunState;
  /** The case changed since this run (the mesh, the settings or the physics). */
  stale: boolean;
  iterations: number;
  converged: boolean;
  seconds: number;
  /** The solver step now running (`run.status` and `run.progress`), or "". */
  step: string;
  /** 0..1, or null when nothing says. */
  fraction: number | null;
  /** The record's error text, `run.no_host: ...` for a skipped run. */
  error: string;
  host: string;
  /** The residuals of the last block the record holds, by field. */
  last: { name: string; value: number }[];
  /** The patch monitors of the record, in patch name order; empty until the run has read them. */
  monitors: MonitorValue[];
  /** Ended by Stop (`run.stop`): the iteration it was on was written, so it is a result to look at and resume. */
  stopped?: boolean;
  /** The run this one continued (`run.resume`), or "". */
  resumedFrom?: string;
  /** `mesh` for an imported case's mesh-only run (the Mesh step's Generate); otherwise a simulation. */
  kind?: string;
}

/** A record of an imported case's mesh-only run, which builds the mesh and solves nothing. */
export function isMeshRun(run: Pick<RunSummary, "kind"> | null | undefined): boolean {
  return run?.kind === "mesh";
}

/** `monitors` of a record: an object keyed by patch; anything else reads as none. */
export function readMonitors(raw: unknown): MonitorValue[] {
  if (!isRecord(raw)) return [];
  const out: MonitorValue[] = [];
  for (const [patch, entry] of Object.entries(raw)) {
    if (!isRecord(entry)) continue;
    out.push({ patch, flowRate: num(entry.flow_rate), pMean: num(entry.p_mean) });
  }
  return out.sort((a, b) => a.patch.localeCompare(b.patch));
}

/** One run record (`run.list`, `run.status`), or null for a payload that is not one. */
export function readRunRecord(raw: unknown): RunSummary | null {
  if (!isRecord(raw)) return null;
  const id = text(raw.id !== undefined ? raw.id : raw.run);
  if (id === "") return null;
  const timings = isRecord(raw.timings) ? raw.timings : {};
  const last: RunSummary["last"] = [];
  if (isRecord(raw.last_residuals)) {
    for (const [name, value] of Object.entries(raw.last_residuals)) {
      const v = num(value);
      if (v !== null) last.push({ name, value: v });
    }
  }
  const fraction = num(raw.fraction);
  return {
    id,
    name: text(raw.name),
    state: readRunState(raw.state) ?? "queued",
    stale: raw.stale === true,
    iterations: Math.max(0, Math.round(num(raw.iterations) ?? 0)),
    converged: raw.converged === true,
    seconds: num(timings.seconds) ?? num(raw.seconds) ?? 0,
    step: text(raw.step),
    fraction: fraction === null ? null : Math.min(1, Math.max(0, fraction)),
    error: text(raw.error),
    host: text(raw.host),
    last,
    monitors: readMonitors(raw.monitors),
    stopped: raw.stopped === true,
    resumedFrom: text(raw.resumed_from),
    kind: text(raw.kind) || "simulation",
  };
}

/** `run.list`: the records, newest first (the server's order is kept). */
export function readRunList(payload: unknown): RunSummary[] {
  if (!isRecord(payload) || !Array.isArray(payload.runs)) return [];
  const out: RunSummary[] = [];
  for (const entry of payload.runs) {
    const run = readRunRecord(entry);
    if (run !== null) out.push(run);
  }
  return out;
}

/** The reply of `run.start`: `{run, state, job, basis, reused, name}`. */
export interface RunStartReply {
  run: string;
  state: RunState | null;
  reused: boolean;
  name: string;
}

export function readRunStart(payload: unknown): RunStartReply | null {
  if (!isRecord(payload) || text(payload.run) === "") return null;
  return { run: text(payload.run), state: readRunState(payload.state), reused: payload.reused === true, name: text(payload.name) };
}

// ---------------------------------------------------------------------------------------------- solve.check and solve.engines
export interface SolveFinding {
  code: string;
  severity: "error" | "warning";
  message: string;
  suggestion: string;
  nodes: string[];
}

export interface SolveCheck {
  state: "ready" | "not_ready";
  /** `run.start` when ready, else the first error's suggestion. */
  next: string;
  findings: SolveFinding[];
}

/** `solve.check`; null for a payload that is not one (the panel then says it does not know yet). */
export function readSolveCheck(payload: unknown): SolveCheck | null {
  if (!isRecord(payload)) return null;
  const state = payload.state === "ready" ? "ready" : payload.state === "not_ready" ? "not_ready" : null;
  if (state === null) return null;
  const findings: SolveFinding[] = [];
  if (Array.isArray(payload.findings)) {
    for (const f of payload.findings) {
      if (!isRecord(f)) continue;
      findings.push({
        code: text(f.code),
        severity: f.severity === "warning" ? "warning" : "error",
        message: text(f.message),
        suggestion: text(f.suggestion),
        nodes: strings(f.nodes),
      });
    }
  }
  return { state, next: text(payload.next), findings };
}

export interface SolveEngine {
  id: string;
  label: string;
  built: boolean;
  turbulence: string[];
  why: string;
}

/** `solve.engines`: unbuilt engines are listed too, with their reason. */
export function readSolveEngines(payload: unknown): SolveEngine[] {
  if (!isRecord(payload) || !Array.isArray(payload.engines)) return [];
  const out: SolveEngine[] = [];
  for (const e of payload.engines) {
    if (!isRecord(e) || text(e.id) === "") continue;
    out.push({ id: text(e.id), label: text(e.label) || text(e.id), built: e.built === true, turbulence: strings(e.turbulence), why: text(e.why) });
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- residuals
/** One block of the solver's log: the iteration it belongs to and the residual of each field it reported. */
export interface ResidualBlock {
  iteration: number;
  time: number | null;
  /** Field name and value; `null` where the solver did not report that field in this block. */
  values: { name: string; value: number | null }[];
}

function readBlockValues(raw: unknown): ResidualBlock["values"] {
  const out: ResidualBlock["values"] = [];
  if (!isRecord(raw)) return out;
  for (const [name, value] of Object.entries(raw)) out.push({ name, value: num(value) });
  return out;
}

/** An event block (`{iteration, time, residuals, continuity}`), or null when it has no iteration. */
export function readResidualBlock(raw: unknown): ResidualBlock | null {
  if (!isRecord(raw)) return null;
  const iteration = num(raw.iteration);
  if (iteration === null) return null;
  return { iteration, time: num(raw.time), values: readBlockValues(raw.residuals) };
}

/**
 * The blocks of a `run.residuals` reply: `iterations`, `time` and each field's list have one length,
 * with `null` where a field was not reported. A list that is too short is read as `null` for the rest,
 * so x and y never shift.
 */
export function readResidualReply(payload: unknown): ResidualBlock[] {
  if (!isRecord(payload) || !Array.isArray(payload.iterations)) return [];
  const iterations = payload.iterations;
  const times = Array.isArray(payload.time) ? payload.time : [];
  const fields = isRecord(payload.fields) ? Object.entries(payload.fields).filter((e): e is [string, unknown[]] => Array.isArray(e[1])) : [];
  const out: ResidualBlock[] = [];
  for (let i = 0; i < iterations.length; i++) {
    const iteration = num(iterations[i]);
    if (iteration === null) continue;
    out.push({ iteration, time: num(times[i]), values: fields.map(([name, list]) => ({ name, value: num(list[i]) })) });
  }
  return out;
}

/**
 * The reply says the server is still reading the run's history back from its host (a reopened project
 * holds none in memory): the blocks are empty for now and the question is worth asking again.
 */
export function residualsLoading(payload: unknown): boolean {
  return isRecord(payload) && payload.loading === true;
}

// ---------------------------------------------------------------------------------------------- events
/** A run or job event, typed; `null` for any other event or a payload that is not one of ours. */
export type RunEvent =
  | { kind: "started"; runId: string }
  | { kind: "progress"; runId: string; step: string; stage: string; fraction: number | null; message: string }
  | { kind: "residual"; runId: string; blocks: ResidualBlock[] }
  | { kind: "log"; source: "run" | "mesh"; jobId: string; step: string; lines: string[] }
  | { kind: "finished"; runId: string; state: RunState; seconds: number; converged: boolean }
  | { kind: "changed"; runId: string; stale: boolean }
  | { kind: "skipped"; runId: string; code: string; message: string }
  | { kind: "reset" };

/**
 * `run.residual` carries `blocks`, a list (the agent's limiter coalesces, and every block is kept);
 * the shape the contract file describes, one block's own fields at the top, is read too.
 */
export function readRunEvent(event: string, data: Record<string, unknown>): RunEvent | null {
  if (PROJECT_EVENTS.includes(event)) return { kind: "reset" };
  if (event === MESH_LOG_EVENT) {
    const jobId = text(data.node_id);
    return jobId === "" ? null : { kind: "log", source: "mesh", jobId, step: text(data.step), lines: strings(data.lines) };
  }
  const runId = text(data.run_id);
  if (runId === "") return null;
  switch (event) {
    case RUN_EVENTS.started:
      return { kind: "started", runId };
    case RUN_EVENTS.progress: {
      const fraction = num(data.fraction);
      return { kind: "progress", runId, step: text(data.step), stage: text(data.stage), fraction: fraction === null ? null : Math.min(1, Math.max(0, fraction)), message: text(data.message) };
    }
    case RUN_EVENTS.residual: {
      const raw = Array.isArray(data.blocks) ? data.blocks : [data];
      const blocks: ResidualBlock[] = [];
      for (const b of raw) {
        const block = readResidualBlock(b);
        if (block !== null) blocks.push(block);
      }
      return { kind: "residual", runId, blocks };
    }
    case RUN_EVENTS.log:
      return { kind: "log", source: "run", jobId: runId, step: text(data.step), lines: strings(data.lines) };
    case RUN_EVENTS.finished: {
      const state = readRunState(data.state);
      return state === null ? null : { kind: "finished", runId, state, seconds: num(data.seconds) ?? 0, converged: data.converged === true };
    }
    case RUN_EVENTS.changed:
      return { kind: "changed", runId, stale: data.stale === true };
    case RUN_EVENTS.skipped:
      return { kind: "skipped", runId, code: text(data.code), message: text(data.message) };
    default:
      return null;
  }
}
