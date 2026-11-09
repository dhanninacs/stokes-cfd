/**
 * The Run panel's logic (T-217): what each state of a run is called, how far it has come, whether Run
 * and Stop can be pressed and why not, the two drawer readouts, the toast a run's end raises, and how
 * the live run state (the `run` slice of the store) follows the `run.*` events. Pure functions over
 * what the server sends (see `runApi.ts` for the shapes), so every state is tested in Node and the
 * components stay thin renderers.
 *
 * Import rule: no React, no store, no DOM. Types come from `../api/types` as types only.
 *
 * Worth knowing:
 *
 * * `done_unconverged` is not a failure: many real runs stop at the iteration limit with the residuals
 *   still falling. It reads "finished, not converged" and its dot is amber, never red.
 * * `skipped` means the host cannot run (no OpenFOAM): it is shown with its reason.
 * * `interrupted` means the outcome is unknown (the agent restarted): it is never a spinner.
 */

import type { NodeSummary } from "../api/types";
import { EMPTY_SERIES, appendResidual, latestValues, type ResidualSeries } from "../state/residualSeries";
import {
  SOLVER_SETTINGS_PATH,
  SOLVER_SETTINGS_TYPE,
  isRunActive,
  type RunEvent,
  type RunStartReply,
  type RunState,
  type RunSummary,
  type SolveCheck,
} from "./runApi";

// ---------------------------------------------------------------------------------------------- numbers and words
/** The most job-log lines kept; the oldest go first (a run writes 10 lines a second for hours). */
export const JOB_LOG_CAP = 5000;

/** `run.status` and `run.residuals` are asked every 2 s while a run is in flight, in case an event was lost. */
export const POLL_MS = 2000;

/** A residual as the readout prints it: "8e-5", "1.2e-3"; "-" for a number that is not one. */
export function formatResidual(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "-";
  const [mantissa, exponent] = value.toExponential(1).split("e");
  return `${mantissa.replace(/\.0$/, "")}e${Number(exponent)}`;
}

/** Seconds as the toast says them: "2.5 s", "12 s", "1 min 5 s". */
export function formatRunSeconds(seconds: number): string {
  if (!(seconds > 0)) return "";
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)} min ${whole % 60} s`;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

// ---------------------------------------------------------------------------------------------- the live state
/** What a run's end tells the user, once: a `seq` that grows with every end, so a subscriber toasts each exactly once. */
export interface RunNotice {
  seq: number;
  runId: string;
  state: RunState;
  iterations: number;
  seconds: number;
  converged: boolean;
  message: string;
  /** `mesh` for an imported case's mesh-only run, whose end is "the mesh is built", not a solve. */
  kind?: string;
}

export type LogView = "events" | "job";

/** The `run` slice of the store: everything the panels read that the tree does not hold. */
export interface RunLive {
  /** The run records, newest first. */
  runs: RunSummary[];
  /** The newest run (`runs[0]`), or null. The Residuals tab and the Results stage follow this one. */
  activeRun: string | null;
  /** The active run's residual history (events, and `run.residuals` after a reconnect); null before the first block. */
  residuals: ResidualSeries | null;
  /** The run `residuals` belongs to. */
  residualRun: string | null;
  /** The last `solve.check`: null until it has answered. */
  check: SolveCheck | null;
  /** The job log: the lines of the runs and the meshes, in the order they came, capped at `JOB_LOG_CAP`. */
  jobLog: string[];
  /** The job, step and source of the last line, so a change of step gets a heading. */
  jobKey: string;
  /** The Log tab's view. */
  logView: LogView;
  /** The last end of a run, or null. */
  notice: RunNotice | null;
}

export const NO_RUN: RunLive = { runs: [], activeRun: null, residuals: null, residualRun: null, check: null, jobLog: [], jobKey: "", logView: "events", notice: null };

/** A run record with nothing known yet: an event arrived for a run the list has not told us about. */
function stub(id: string, state: RunState): RunSummary {
  return { id, name: "", state, stale: false, iterations: 0, converged: false, seconds: 0, step: "", fraction: null, error: "", host: "", last: [], monitors: [] };
}

/** The records with one changed (or added at the front: an unknown run is the newest). */
function upsert(runs: RunSummary[], id: string, state: RunState, change: (run: RunSummary) => RunSummary): RunSummary[] {
  const at = runs.findIndex((r) => r.id === id);
  if (at < 0) return [change(stub(id, state)), ...runs];
  const next = change(runs[at]);
  return next === runs[at] ? runs : runs.map((r, i) => (i === at ? next : r));
}

/**
 * The run records after one event. Immutable; an event that changes nothing returns the input, and an
 * event that is not about a run (a job log line, a reset) returns the input too.
 */
export function applyRunEvent(runs: RunSummary[], event: RunEvent): RunSummary[] {
  switch (event.kind) {
    case "started":
      return upsert(runs, event.runId, "running", (r) => (r.state === "running" ? r : { ...r, state: "running", error: "" }));
    case "progress":
      return upsert(runs, event.runId, "running", (r) => {
        // A late progress event never reopens a run that has ended or is stopping.
        const state = r.state === "queued" ? "running" : r.state;
        const fraction = event.fraction ?? r.fraction;
        const step = event.step !== "" ? event.step : r.step;
        return state === r.state && fraction === r.fraction && step === r.step ? r : { ...r, state, fraction, step };
      });
    case "residual": {
      const lastBlock = event.blocks[event.blocks.length - 1];
      if (lastBlock === undefined) return runs;
      return upsert(runs, event.runId, "running", (r) => {
        const last = lastBlock.values.flatMap((v) => (v.value === null || !Number.isFinite(v.value) ? [] : [{ name: v.name, value: v.value }]));
        return { ...r, iterations: Math.max(r.iterations, Math.round(lastBlock.iteration)), last: last.length > 0 ? last : r.last };
      });
    }
    case "finished":
      return upsert(runs, event.runId, event.state, (r) => ({ ...r, state: event.state, seconds: event.seconds > 0 ? event.seconds : r.seconds, converged: event.converged, fraction: event.state === "done" ? 1 : r.fraction }));
    case "changed":
      // A change to a run the list does not hold is not a run to invent: the next `run.list` brings it.
      return runs.some((r) => r.id === event.runId) ? upsert(runs, event.runId, "queued", (r) => (r.stale === event.stale ? r : { ...r, stale: event.stale })) : runs;
    case "skipped":
      return upsert(runs, event.runId, "skipped", (r) => ({ ...r, state: "skipped", error: event.message !== "" ? event.message : event.code }));
    default:
      return runs;
  }
}

/** The job log with `lines` added under the job and step they came from, a heading line when that changed. */
export function appendJobLog(log: readonly string[], lastKey: string, source: string, jobId: string, step: string, lines: readonly string[]): { log: string[]; key: string } {
  if (lines.length === 0) return { log: log as string[], key: lastKey };
  const key = `${source}:${jobId}:${step}`;
  const heading = key === lastKey ? [] : [`--- ${source}${step !== "" ? ` · ${step}` : ""} ---`];
  const merged = [...log, ...heading, ...lines];
  return { log: merged.length > JOB_LOG_CAP ? merged.slice(-JOB_LOG_CAP) : merged, key };
}

function activeOf(runs: RunSummary[]): string | null {
  return runs[0]?.id ?? null;
}

/** The live state after one event (the store's reducer). */
export function reduceRunEvent(live: RunLive, event: RunEvent): RunLive {
  if (event.kind === "reset") return { ...NO_RUN, logView: live.logView };
  if (event.kind === "log") {
    const { log, key } = appendJobLog(live.jobLog, live.jobKey, event.source, event.jobId, event.step, event.lines);
    return log === live.jobLog ? live : { ...live, jobLog: log, jobKey: key };
  }
  const runs = applyRunEvent(live.runs, event);
  let next: RunLive = runs === live.runs ? live : { ...live, runs, activeRun: activeOf(runs) };

  if (event.kind === "started" && next.residualRun !== event.runId) {
    // A new run starts with an empty chart.
    next = { ...next, residuals: null, residualRun: event.runId };
  } else if (event.kind === "residual") {
    if (next.residualRun === null || next.residualRun === event.runId) {
      const grown = appendResidual(next.residuals ?? EMPTY_SERIES, event.blocks);
      if (grown !== next.residuals || next.residualRun !== event.runId) next = { ...next, residuals: grown, residualRun: event.runId };
    }
  } else if (event.kind === "finished" || event.kind === "skipped") {
    const state = event.kind === "finished" ? event.state : "skipped";
    next = { ...next, notice: noticeOf(next, live, event.runId, state, event.kind === "finished" ? event.seconds : 0, event.kind === "finished" && event.converged, event.kind === "skipped" ? (event.message !== "" ? event.message : event.code) : null) };
  }
  return next;
}

/** The notice for a run's end; `message` null reads the record's own error. */
function noticeOf(now: RunLive, before: RunLive, runId: string, state: RunState, seconds: number, converged: boolean, message: string | null): RunNotice {
  const run = now.runs.find((r) => r.id === runId);
  return { seq: (before.notice?.seq ?? 0) + 1, runId, state, iterations: run?.iterations ?? 0, seconds, converged, message: message ?? run?.error ?? "", kind: run?.kind };
}

/** `run.list` answered: the records replace ours, and the chart follows the newest. A chart of another run is dropped (its blocks come from `run.residuals`). */
export function setRuns(live: RunLive, runs: RunSummary[]): RunLive {
  const active = activeOf(runs);
  const keep = active !== null && live.residualRun === active;
  return { ...live, runs, activeRun: active, residuals: keep ? live.residuals : null, residualRun: active };
}

/** `run.status` answered for one run: its record replaces ours; an end the events missed raises its notice. */
export function mergeRecord(live: RunLive, record: RunSummary): RunLive {
  const at = live.runs.findIndex((r) => r.id === record.id);
  if (at < 0) return live;
  const was = live.runs[at];
  const merged: RunSummary = { ...record, name: record.name !== "" ? record.name : was.name, last: record.last.length > 0 ? record.last : was.last, iterations: Math.max(record.iterations, was.iterations) };
  const runs = live.runs.map((r, i) => (i === at ? merged : r));
  const ended = isRunActive(was.state) && !isRunActive(merged.state);
  const next = { ...live, runs, activeRun: activeOf(runs) };
  return ended ? { ...next, notice: noticeOf(next, live, merged.id, merged.state, merged.seconds, merged.converged, null) } : next;
}

/** `run.start` answered: a run that is new appears at once, before its first event. A reused answer changes nothing. */
export function noteStart(live: RunLive, reply: RunStartReply): RunLive {
  if (reply.reused) return live;
  const known = live.runs.some((r) => r.id === reply.run);
  const runs = known ? live.runs.map((r) => (r.id === reply.run && r.name === "" ? { ...r, name: reply.name } : r)) : [{ ...stub(reply.run, reply.state ?? "queued"), name: reply.name }, ...live.runs];
  return { ...live, runs, activeRun: activeOf(runs), residuals: live.residualRun === reply.run ? live.residuals : null, residualRun: reply.run };
}

/**
 * `run.stop` answered `stopping`: the solver will write the iteration it is on and end. The record keeps
 * `running` until then, so the flag is what turns the button into "Already stopping.".
 */
export function markStopped(live: RunLive, runId: string): RunLive {
  const runs = live.runs.map((r) => (r.id === runId && !r.stopped ? { ...r, stopped: true } : r));
  return { ...live, runs };
}

/**
 * Whether Resume makes sense for a run: it solved (`done` or `done_unconverged`), it was stopped or ended
 * short of the iteration limit, and it did not converge. A run already at the limit would resume and end at
 * once, so the limit has to be raised first; a converged run has nothing left to do.
 */
export function canResume(run: RunSummary | null, limit: number | null): boolean {
  if (run === null || run.state !== "done_unconverged") return false;
  if (run.stopped === true) return true;
  return limit !== null && run.iterations < limit;
}

/** `run.cancel` answered true: the run says it is stopping until the job's own end arrives. */
export function markCancelling(live: RunLive, runId: string): RunLive {
  const runs = live.runs.map((r) => (r.id === runId && (r.state === "running" || r.state === "queued") ? { ...r, state: "cancelling" as const } : r));
  return { ...live, runs };
}

/** `run.log` answered after a reload: the tail fills an empty job log; a log the events are feeding is left alone. */
export function seedJobLog(live: RunLive, runId: string, step: string, lines: readonly string[]): RunLive {
  if (live.jobLog.length > 0 || lines.length === 0) return live;
  const { log, key } = appendJobLog(live.jobLog, live.jobKey, "run", runId, step, lines);
  return { ...live, jobLog: log, jobKey: key };
}

// ---------------------------------------------------------------------------------------------- the buttons
export interface RunButtonState {
  label: "Run" | "Stop";
  enabled: boolean;
  /** Why it is disabled; "" when it is not. */
  reason: string;
  /** Run again although nothing that decides the run has changed (`run.start force`). */
  force: boolean;
}

/** The first thing wrong, in the server's own words: the suggestion, or the message when it has none. */
export function whyNotReady(check: SolveCheck): string {
  const first = check.findings.find((f) => f.severity === "error");
  const suggestion = check.next.trim() !== "" && check.next !== "run.start" ? check.next.trim() : "";
  if (suggestion !== "") return suggestion;
  if (first !== undefined) return first.suggestion !== "" ? first.suggestion : first.message;
  return "The case is not ready to run.";
}

/**
 * The top bar's button and the panel's. Stop while a run is in flight (disabled with "Already stopping."
 * once asked); Run is enabled only when `solve.check` says ready, and otherwise says why.
 *
 * **An imported case (a tutorial) is not judged by `solve.check`**: that checks the CAD model's own
 * set-up (regions, materials, boundaries), which a case does not have, so Run stayed disabled with
 * "give the domain's region a material" for every tutorial. The case's dictionaries are its set-up.
 */
export function runButton(run: RunSummary | null, check: SolveCheck | null, busy = false, isCase = false): RunButtonState {
  if (run !== null && isRunActive(run.state)) {
    return run.state === "cancelling" || run.stopped === true ? { label: "Stop", enabled: false, reason: "Already stopping.", force: false } : { label: "Stop", enabled: true, reason: "", force: false };
  }
  const no = (reason: string): RunButtonState => ({ label: "Run", enabled: false, reason, force: false });
  if (busy) return no("Working on the last request.");
  if (isCase) {
    const solved = run !== null && run.kind !== "mesh" && (run.state === "done" || run.state === "done_unconverged");
    return { label: "Run", enabled: true, reason: "", force: solved };
  }
  if (check === null) return no("Checking whether the case is ready to run.");
  if (check.state !== "ready") return no(whyNotReady(check));
  const again = run !== null && (run.state === "done" || run.state === "done_unconverged") && !run.stale;
  return { label: "Run", enabled: true, reason: "", force: again };
}

// ---------------------------------------------------------------------------------------------- readouts
export type RunReadoutState = "idle" | "ready" | "running" | "done" | "unconverged" | "failed" | "stale" | "cancelled" | "interrupted" | "skipped";

export interface RunReadout {
  text: string;
  state: RunReadoutState;
  /** A longer sentence for the tooltip. */
  detail: string;
}

/** The colour each state's dot takes: tokens that every theme defines, never a literal colour. */
export const RUN_DOT: Record<RunReadoutState, string> = {
  idle: "var(--text-muted)",
  ready: "var(--text-muted)",
  running: "var(--accent)",
  done: "var(--ok)",
  unconverged: "var(--warn)",
  failed: "var(--danger)",
  stale: "var(--warn)",
  cancelled: "var(--text-muted)",
  interrupted: "var(--warn)",
  skipped: "var(--warn)",
};

/** The residual a readout names while a run is going: pressure when the solver reports it, else the first field. */
function headline(latest: readonly { name: string; value: number }[]): { name: string; value: number } | null {
  return latest.find((f) => f.name === "p") ?? latest[0] ?? null;
}

/**
 * The drawer strip's Run readout, or null while nothing is known yet (the strip then shows nothing,
 * rather than claiming a state it was not told). `latest` is the chart's last numbers, which are newer
 * than the record's while a run goes.
 */
export function runReadout(run: RunSummary | null, check: SolveCheck | null, latest: readonly { name: string; value: number }[] = [], isCase = false): RunReadout | null {
  if (run === null) {
    if (isCase) return { text: "Run · ready", state: "ready", detail: "The case is ready to run: its own dictionaries are its set-up." };
    if (check === null) return null;
    if (check.state === "ready") return { text: "Run · ready", state: "ready", detail: "The case is ready to run." };
    const why = whyNotReady(check);
    return { text: `Run · ${why}`, state: "idle", detail: why };
  }
  const it = `it ${run.iterations}`;
  if (run.kind === "mesh") {
    // An imported case's mesh-only run (the Mesh step's Generate): no iterations, no residuals.
    if (isRunActive(run.state)) return { text: "Mesh · building", state: "running", detail: "Running the case's own mesh steps." };
    if (run.state === "done") return { text: "Mesh · built · ready to run", state: "ready", detail: "The case's mesh is built; Run solves it." };
    if (run.state === "failed") return { text: "Mesh · failed", state: "failed", detail: run.error !== "" ? run.error : "The mesh steps failed." };
  }
  switch (run.state) {
    case "queued":
      return { text: "Run · starting", state: "running", detail: "The run is waiting to start." };
    case "running": {
      if (run.stopped === true) return { text: `Run · ${it} · stopping, saving`, state: "running", detail: "Writing the iteration it is on; the result can then be looked at and resumed." };
      const h = headline(latest.length > 0 ? latest : run.last);
      return { text: `Run · ${it}${h !== null ? ` · ${h.name} ${formatResidual(h.value)}` : ""}`, state: "running", detail: "The run is going." };
    }
    case "cancelling":
      return { text: "Run · stopping", state: "running", detail: "The run was asked to stop." };
    case "done":
    case "done_unconverged": {
      if (run.stale) return { text: "Run · stale", state: "stale", detail: "The case changed since this run; its result no longer matches it." };
      if (run.state === "done") return { text: `Run · ${it} · converged`, state: "done", detail: `Converged in ${plural(run.iterations, "iteration")}.` };
      return run.stopped === true
        ? { text: `Run · ${it} · stopped`, state: "unconverged", detail: `Stopped at ${plural(run.iterations, "iteration")}; that iteration was saved, so its fields can be shown and the run resumed.` }
        : { text: `Run · ${it} · not converged`, state: "unconverged", detail: `Finished at ${plural(run.iterations, "iteration")} without reaching the target. The residuals may still be falling.` };
    }
    case "failed":
      return { text: "Run · failed", state: "failed", detail: run.error !== "" ? run.error : "The run failed." };
    case "cancelled":
      return { text: "Run · cancelled", state: "cancelled", detail: `Stopped at ${plural(run.iterations, "iteration")}.` };
    case "interrupted":
      return { text: "Run · interrupted", state: "interrupted", detail: "The agent stopped reporting this run; its outcome is unknown." };
    case "skipped": {
      const why = run.error.replace(/^[a-z_.]+:\s*/, "");
      return { text: why !== "" ? `Run · skipped: ${why}` : "Run · skipped", state: "skipped", detail: why !== "" ? why : "No host could run it." };
    }
  }
}

// ---------------------------------------------------------------------------------------------- the panel's state line
export type Tone = "neutral" | "busy" | "ok" | "warn" | "bad";

/** The one-line state under the buttons and the tone its icon takes. */
export function stateLine(run: RunSummary | null, limit: number | null): { text: string; tone: Tone } {
  if (run === null) return { text: "No run yet", tone: "neutral" };
  const took = formatRunSeconds(run.seconds);
  const inTime = took === "" ? "" : ` in ${took}`;
  if (run.kind === "mesh") {
    if (isRunActive(run.state)) return { text: `Building the mesh${run.step !== "" ? ` (${run.step})` : ""}`, tone: "busy" };
    if (run.state === "done") return { text: `Mesh built${inTime}. Press Run to solve`, tone: "ok" };
    if (run.state === "failed") return { text: `Mesh failed${run.error !== "" ? `: ${run.error}` : ""}`, tone: "bad" };
  }
  switch (run.state) {
    case "queued":
      return { text: "Waiting to start", tone: "busy" };
    case "running": {
      if (run.stopped === true) return { text: `Stopping at iteration ${run.iterations}: saving it`, tone: "busy" };
      const of = limit !== null && limit > 0 ? ` of ${limit}` : "";
      return { text: `Running: iteration ${run.iterations}${of}${run.step !== "" ? ` (${run.step})` : ""}`, tone: "busy" };
    }
    case "cancelling":
      return { text: "Stopping", tone: "busy" };
    case "done":
      return run.stale ? { text: `Finished: converged in ${plural(run.iterations, "iteration")}${inTime}. Out of date`, tone: "warn" } : { text: `Finished: converged in ${plural(run.iterations, "iteration")}${inTime}`, tone: "ok" };
    case "done_unconverged":
      if (run.stopped === true) return { text: `Stopped and saved at iteration ${run.iterations}: results can be shown, or Resume${run.stale ? ". Out of date" : ""}`, tone: "warn" };
      return { text: `Finished, not converged: ${plural(run.iterations, "iteration")}${inTime}${run.stale ? ". Out of date" : ""}`, tone: "warn" };
    case "failed":
      return { text: `Failed${run.error !== "" ? `: ${run.error}` : ""}`, tone: "bad" };
    case "cancelled":
      return { text: `Stopped at iteration ${run.iterations}`, tone: "neutral" };
    case "interrupted":
      return { text: "Interrupted: the agent stopped reporting this run, so its outcome is unknown", tone: "warn" };
    case "skipped":
      return { text: `Skipped: ${run.error !== "" ? run.error.replace(/^[a-z_.]+:\s*/, "") : "no host was available"}`, tone: "warn" };
  }
}

/** How far the run has come, 0..1: the solver's own fraction when it reports one, else iterations over the limit; null when nothing says. */
export function progressFraction(run: RunSummary | null, limit: number | null): number | null {
  if (run === null) return null;
  if (run.state === "done") return 1;
  if (!isRunActive(run.state)) return null;
  if (run.fraction !== null && run.fraction > 0) return run.fraction;
  if (limit !== null && limit > 0 && run.iterations > 0) return Math.min(1, run.iterations / limit);
  return run.state === "queued" ? 0 : null;
}

// ---------------------------------------------------------------------------------------------- the toast
/** The toast a run's end raises, or null for an end that needs none (a run the user stopped says so in the readout). */
export function noticeToast(note: RunNotice): { kind: "info" | "error" | "ok"; text: string } | null {
  const took = formatRunSeconds(note.seconds);
  if (note.kind === "mesh") {
    if (note.state === "done") return { kind: "ok", text: `Mesh built${took !== "" ? ` · ${took}` : ""}` };
    if (note.state === "failed") return { kind: "error", text: note.message !== "" ? `Mesh failed: ${note.message}` : "Mesh failed" };
  }
  switch (note.state) {
    case "done":
      return { kind: "ok", text: `Run converged in ${plural(note.iterations, "iteration")}${took !== "" ? ` · ${took}` : ""}` };
    case "done_unconverged":
      return { kind: "info", text: `Run stopped at ${plural(note.iterations, "iteration")} without converging` };
    case "failed":
      return { kind: "error", text: note.message !== "" ? `Run failed: ${note.message}` : "Run failed" };
    case "skipped":
      return { kind: "info", text: `Run skipped: ${note.message.replace(/^[a-z_.]+:\s*/, "") || "no host was available"}` };
    case "interrupted":
      return { kind: "info", text: "Run interrupted: its outcome is unknown" };
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------------------------- the settings node
/** `/solver/settings` found in the tree; null when absent. */
export function findSolverSettings(tree: NodeSummary | null): NodeSummary | null {
  const group = tree?.children?.find((c) => c.path === "/solver") ?? null;
  return group?.children?.find((c) => c.type === SOLVER_SETTINGS_TYPE || c.path === SOLVER_SETTINGS_PATH) ?? null;
}

function positive(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/** The convergence target the chart draws, from `residual_target`; null when unset. */
export function residualTarget(settings: NodeSummary | null): number | null {
  return positive((settings?.params as Record<string, unknown> | undefined)?.residual_target);
}

/** The iteration limit, from `iterations`; null when unset. */
export function iterationLimit(settings: NodeSummary | null): number | null {
  return positive((settings?.params as Record<string, unknown> | undefined)?.iterations);
}

/**
 * The turbulence models the CHOSEN ENGINE can express, for the select's options.
 *
 * **Found by Codex's item 2** and it was a real inconsistency: the catalogue declares the models per
 * engine - `of14.incompressibleFluid` is `("laminar",)` while `of14.kOmegaSST` adds `kOmegaSST` - but the
 * settings card rendered the schema's single list, so a laminar run was offered `kOmegaSST` as if it were
 * a model it could choose. It is not: `solve.py` refuses `turbulence == "kOmegaSST"` with
 * `solve.needs_turbulence`, which is the correct behaviour and the reason the option must not be offered.
 *
 * **An engine with no entry keeps the schema's list**: a stale or unknown engine id is not evidence that
 * a model is unsupported, and hiding options on a guess is worse than showing one too many.
 */
export function turbulenceOptionsFor(
  engines: readonly { id: string; turbulence: readonly string[] }[],
  engineId: string,
  offered: readonly string[],
): string[] {
  const engine = engines.find((e) => e.id === engineId);
  if (engine === undefined || engine.turbulence.length === 0) return [...offered];
  // **The intersection, in the schema's order**, so the list stays stable as the engine changes.
  return offered.filter((name) => engine.turbulence.includes(name));
}

/** The settings the card shows that the node's schema has, in the card's order. */
export function visibleSettingNames(properties: Record<string, unknown> | undefined, wanted: readonly string[]): string[] {
  return wanted.filter((name) => properties?.[name] !== undefined);
}

/** The chart's last numbers for the active run, for the readout. */
export function liveLatest(live: RunLive): { name: string; value: number }[] {
  return live.residualRun !== null && live.residualRun === live.activeRun ? latestValues(live.residuals) : [];
}

/** True while the panels should ask the server for the run's state in case an event was lost. */
export function shouldPoll(run: RunSummary | null): boolean {
  return run !== null && isRunActive(run.state);
}
