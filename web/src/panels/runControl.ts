/**
 * Running a solve from the interface (T-217): what Run and Stop do, and how the live run state is kept
 * true. The decisions (is Run enabled, why not, what a state is called) are `runPanelModel.ts`; the
 * names and shapes of the commands and events are `runApi.ts`; this file reads and writes the stores,
 * calls the commands and keeps three things going on its own, whether or not any panel is on screen:
 *
 * * `solve.check`, debounced, after the model, the mesh or a run changes: Run's enabled state and its
 *   reason come from it;
 * * `run.list`, the residual history and the log tail, when the connection comes up or the project
 *   changes (events sent while the page was away are lost);
 * * `run.status` and `run.residuals` every two seconds while a run is in flight, in case an event was
 *   lost, so a panel can never say "running" for ever.
 *
 * The top bar, the ribbon and the panel all press `pressRun`; this module is loaded with the top bar, so
 * the three things above run from the start.
 */
import { useMemo } from "react";
import { create } from "zustand";
import { StokesApiError } from "../api/client";
import { useStore } from "../state/store";
import { lastIteration } from "../state/residualSeries";
import { readCase } from "../guided/guidedTree";
import { RUN_COMMANDS, readResidualReply, residualsLoading, readRunList, readRunRecord, readRunStart, readSolveCheck, readSolveEngines, type SolveEngine } from "./runApi";
import { POLL_MS, findSolverSettings, iterationLimit, markCancelling, markStopped, mergeRecord, noteStart, noticeToast, reduceRunEvent, runButton, seedJobLog, setRuns, shouldPoll, type LogView, type RunButtonState, type RunLive } from "./runPanelModel";

/** How long `solve.check` waits after the last change: the window `setup.check` uses (`store.ts`, `SETUP_CHECK_DEBOUNCE_MS`). */
const SOLVE_CHECK_DEBOUNCE_MS = 250;

/** What the Solve panel learns from the server once per connection, and what is in flight from this side. */
interface RunContext {
  engines: SolveEngine[];
  /** `solve.engines` has answered (the engine choice can be drawn). */
  loaded: boolean;
  /** A start, a stop or a configure is in flight. */
  busy: boolean;
  /** The refusal of the last start, in the server's words; cleared by the next press. */
  error: string;
}

export const useRunContext = create<RunContext>(() => ({ engines: [], loaded: false, busy: false, error: "" }));

function messageOf(err: unknown): string {
  return err instanceof StokesApiError ? err.message : err instanceof Error ? err.message : String(err);
}

/** A server older than T-214 has no `solve.*` or `run.*`: it says so with this code, once, and the panels then stay quiet. */
const UNKNOWN_COMMAND = "api.unknown_command";
let unsupported = false;
/** Grows each time the server is seen to know `solve.*`: an unknown-command answer to a question asked before that is old news. */
let epoch = 0;

function noteUnsupported(err: unknown, asked: number): boolean {
  if (err instanceof StokesApiError && err.code === UNKNOWN_COMMAND) {
    if (asked === epoch) unsupported = true;
    return true;
  }
  return false;
}

function setRun(change: (live: RunLive) => RunLive): void {
  useStore.setState((s) => {
    const next = change(s.run);
    return next === s.run ? s : { run: next };
  });
}

// ---------------------------------------------------------------------------------------------- asking the server
let checkTimer: ReturnType<typeof setTimeout> | 0 = 0;
let checkSeq = 0;

/** Ask `solve.check`, debounced: a burst of changes costs one answer. */
export function scheduleSolveCheck(): void {
  if (unsupported) return;
  if (checkTimer !== 0) clearTimeout(checkTimer);
  checkTimer = setTimeout(() => {
    checkTimer = 0;
    void askSolveCheck();
  }, SOLVE_CHECK_DEBOUNCE_MS);
}

async function askSolveCheck(): Promise<void> {
  if (unsupported) return;
  const { client, project } = useStore.getState();
  if (client === null || project === null) {
    setRun((live) => (live.check === null ? live : { ...live, check: null }));
    return;
  }
  const seq = ++checkSeq;
  const asked = epoch;
  try {
    const answer = readSolveCheck(await client.call(RUN_COMMANDS.check));
    // An older answer never replaces a newer one.
    if (seq === checkSeq) setRun((live) => ({ ...live, check: answer }));
  } catch (err) {
    if (noteUnsupported(err, asked)) return;
    // A failed check keeps the last answer and does not toast on every keystroke; the reason goes to the console.
    useStore.getState().log("error", `${RUN_COMMANDS.check}: ${messageOf(err)}`);
  }
}

/** The engines the server offers: asked once per connection, and needing no project. */
export async function loadEngines(): Promise<void> {
  const client = useStore.getState().client;
  if (client === null) return;
  try {
    const engines = readSolveEngines(await client.call(RUN_COMMANDS.engines));
    useRunContext.setState({ engines, loaded: true });
  } catch {
    // An older server has no solve.*: the card then says so instead of drawing an engine choice.
    useRunContext.setState({ engines: [], loaded: false });
  }
}

/** Everything the panels need after a connection or a project change: the runs, the chart, the log tail, the check. */
export async function loadRunState(): Promise<void> {
  const { client, project } = useStore.getState();
  if (client === null || unsupported) return;
  if (project === null) {
    setRun((live) => ({ ...live, runs: [], activeRun: null, residuals: null, residualRun: null, check: null }));
    return;
  }
  void askSolveCheck();
  const asked = epoch;
  try {
    const list = readRunList(await client.call(RUN_COMMANDS.list));
    setRun((live) => setRuns(live, list));
    await catchUpResiduals();
    const active = list[0];
    if (active !== undefined && useStore.getState().run.jobLog.length === 0) {
      const tail = (await client.call(RUN_COMMANDS.log, { run: active.id, lines: 200 })) as { step?: unknown; lines?: unknown };
      const lines = Array.isArray(tail.lines) ? tail.lines.filter((l): l is string => typeof l === "string") : [];
      setRun((live) => seedJobLog(live, active.id, typeof tail.step === "string" ? tail.step : "", lines));
    }
  } catch (err) {
    // A server without run.* leaves the readouts empty rather than the window unstarted.
    noteUnsupported(err, asked);
  }
}

/**
 * The full record of a run that has just ended (T-219 D). The end arrives as an event that says the state, the
 * iterations and the time; the monitors (the flow rate and the mean pressure of each patch) and the last
 * residuals are read from the run's files when it ends and are in the record only, so one `run.status` brings
 * them in. Without it the Monitors table and the pressure drop stayed empty until the project was reopened.
 */
async function refreshRecord(runId: string): Promise<void> {
  const client = useStore.getState().client;
  if (client === null || unsupported) return;
  try {
    const record = readRunRecord(await client.call(RUN_COMMANDS.status, { run: runId }));
    if (record !== null) setRun((live) => mergeRecord(live, record));
  } catch {
    // The end is already shown from the event; a record that cannot be read leaves the monitors empty.
  }
}

/** How many times a history the server is still reading back is asked for again, `POLL_MS` apart (a minute). */
const HISTORY_RETRIES = 30;
let historyRetry: ReturnType<typeof setTimeout> | 0 = 0;

/**
 * The residual blocks the chart has not drawn: `since` is the last iteration it holds.
 *
 * **A reopened project's finished run answers `loading`** while the server reads its history back from
 * the host (`run.residuals`); without asking again the chart stayed "unavailable" until the next reload.
 */
async function catchUpResiduals(retries = HISTORY_RETRIES): Promise<void> {
  const { client, run } = useStore.getState();
  const active = run.activeRun;
  if (client === null || active === null) return;
  const since = run.residualRun === active ? lastIteration(run.residuals) : 0;
  const reply = await client.call(RUN_COMMANDS.residuals, { run: active, since });
  const blocks = readResidualReply(reply);
  if (blocks.length > 0) setRun((live) => reduceRunEvent(live, { kind: "residual", runId: active, blocks }));
  if (historyRetry !== 0) clearTimeout(historyRetry);
  historyRetry = 0;
  if (residualsLoading(reply) && retries > 0) {
    historyRetry = setTimeout(() => {
      historyRetry = 0;
      if (useStore.getState().run.activeRun === active) void catchUpResiduals(retries - 1).catch(() => undefined);
    }, POLL_MS);
  }
}

// ---------------------------------------------------------------------------------------------- the poll
let poller: ReturnType<typeof setInterval> | 0 = 0;
let polling = false;

async function poll(): Promise<void> {
  if (polling) return;
  const { client, run } = useStore.getState();
  const active = run.runs[0];
  if (client === null || active === undefined || !shouldPoll(active)) return;
  polling = true;
  try {
    const record = readRunRecord(await client.call(RUN_COMMANDS.status, { run: active.id }));
    if (record !== null) setRun((live) => mergeRecord(live, record));
    await catchUpResiduals();
  } catch {
    // A poll that fails is retried in two seconds; the events are the primary path.
  } finally {
    polling = false;
  }
}

function syncPoller(): void {
  const s = useStore.getState();
  const want = s.connected && shouldPoll(s.run.runs[0] ?? null);
  if (want && poller === 0) poller = setInterval(() => void poll(), POLL_MS);
  else if (!want && poller !== 0) {
    clearInterval(poller);
    poller = 0;
  }
}

// ---------------------------------------------------------------------------------------------- the buttons
/** Run's state for a component: it follows the newest run, the last `solve.check` and what is in flight. */
export function useRunButton(): RunButtonState {
  const run = useStore((s) => s.run.runs[0] ?? null);
  const check = useStore((s) => s.run.check);
  const busy = useRunContext((c) => c.busy);
  const caseId = useStore((s) => readCase(s.tree)?.id ?? null);
  return useMemo(() => runButton(run, check, busy, caseId !== null), [run, check, busy, caseId]);
}

/** Run's state now, outside React (the palette and the shortcut ask it when they run). */
export function currentRunButton(): RunButtonState {
  const s = useStore.getState();
  return runButton(s.run.runs[0] ?? null, s.run.check, useRunContext.getState().busy, readCase(s.tree) !== null);
}

/** Press Run: `run.start`. A refusal is toasted in the server's words (the store's own toast path) and kept for the panel. */
export async function startRun(): Promise<void> {
  const state = useStore.getState();
  const wanted = currentRunButton();
  if (wanted.label === "Stop") return;
  if (!wanted.enabled) {
    state.pushToast("info", wanted.reason);
    return;
  }
  useRunContext.setState({ busy: true, error: "" });
  // **An imported case runs its own recipe** (`run.start {case}`), as the Full-mode Case panel does;
  // without the id the server ran the CAD model, which a tutorial project does not have.
  const foamCase = readCase(state.tree);
  const params: Record<string, unknown> = { ...(wanted.force ? { force: true } : {}), ...(foamCase !== null ? { case: foamCase.id } : {}) };
  try {
    const reply = readRunStart(await state.execute(RUN_COMMANDS.start, params));
    if (reply?.reused === true) state.pushToast("info", "Nothing has changed since the last run; its result is still current.");
    else if (reply !== null) setRun((live) => noteStart(live, reply));
  } catch (err) {
    useRunContext.setState({ error: messageOf(err) });
  } finally {
    useRunContext.setState({ busy: false });
  }
  await refreshAfterStart();
}

/**
 * The runs, chart and log after a start, read AFTER the button is free again.
 *
 * **"Starting…" covers the start request only.** It used to wait for these reads too, so one that
 * never answered (a reply the client could not parse) kept the button on "Starting… Working on the last
 * request" after the run itself had long failed. The start's reply already put the new run in place.
 */
async function refreshAfterStart(): Promise<void> {
  try {
    await loadRunState();
  } catch {
    // The events and the poll bring the same state; a failed read leaves the readouts as they are.
  }
}

/**
 * Press Stop: `run.stop`. A running solve writes the iteration it is on and ends, so its fields can be shown
 * and Resume continues it; a run with no solver going yet is cancelled. The job's own end reports either way.
 */
export async function stopRun(): Promise<void> {
  const state = useStore.getState();
  const active = state.run.runs[0];
  if (active === undefined) return;
  try {
    const answer = (await state.execute(RUN_COMMANDS.stop, {})) as { stopping?: boolean; cancelled?: boolean };
    if (answer.stopping === true) setRun((live) => markStopped(live, active.id));
    else if (answer.cancelled === true) setRun((live) => markCancelling(live, active.id));
  } catch (err) {
    useRunContext.setState({ error: messageOf(err) });
  }
}

/**
 * Press Generate in the Mesh step of an imported case: `run.start {case, mesh_only}` runs only the
 * case's own mesh steps (blockMesh, snappyHexMesh, createZones, ...). The finished mesh is shown by the
 * same path a finished run's fields are: `done` raises the notice and the results are read.
 */
export async function generateCaseMesh(caseId: string): Promise<void> {
  const state = useStore.getState();
  useRunContext.setState({ busy: true, error: "" });
  try {
    const reply = readRunStart(await state.execute(RUN_COMMANDS.start, { case: caseId, mesh_only: true, force: true }));
    if (reply !== null) setRun((live) => noteStart(live, reply));
  } catch (err) {
    useRunContext.setState({ error: messageOf(err) });
  } finally {
    useRunContext.setState({ busy: false });
  }
  await refreshAfterStart();
}

/** Press Resume: `run.resume` continues the newest result from the last iteration it saved. */
export async function resumeRun(): Promise<void> {
  const state = useStore.getState();
  useRunContext.setState({ busy: true, error: "" });
  try {
    const reply = readRunStart(await state.execute(RUN_COMMANDS.resume, {}));
    if (reply !== null) setRun((live) => noteStart(live, reply));
  } catch (err) {
    useRunContext.setState({ error: messageOf(err) });
  } finally {
    useRunContext.setState({ busy: false });
  }
  await refreshAfterStart();
}

/** The one handler of the Run / Stop button, wherever it is drawn (the top bar, the ribbon, the panel, the palette). */
export function pressRun(): void {
  const wanted = currentRunButton();
  if (wanted.label === "Stop") {
    if (wanted.enabled) void stopRun();
  } else {
    void startRun();
  }
}

/** The Log tab's view: the model's events, or the solver's own lines. */
export function setLogView(view: LogView): void {
  setRun((live) => (live.logView === view ? live : { ...live, logView: view }));
}

/** Clear the job log (the drawer's Clear button on the Job view). */
export function clearJobLog(): void {
  setRun((live) => (live.jobLog.length === 0 ? live : { ...live, jobLog: [], jobKey: "" }));
}

/** One `solve.configure` edit: one undo step; a refusal is the command's own message. */
export async function configureSolve(params: Record<string, unknown>): Promise<unknown> {
  return useStore.getState().execute(RUN_COMMANDS.configure, params, { silent: true });
}

/** The iteration limit of the project's settings, for the state line. */
export function useIterationLimit(): number | null {
  const tree = useStore((s) => s.tree);
  return useMemo(() => iterationLimit(findSolverSettings(tree)), [tree]);
}

// ---------------------------------------------------------------------------------------------- the wiring
let projectKey = "";
let toasted = "";

useStore.subscribe((state, previous) => {
  const key = `${state.project?.name ?? ""}|${state.project?.path ?? ""}`;
  if ((state.connected && !previous.connected) || (state.project !== null && key !== projectKey) || (previous.run.runs.length > 0 && state.run.runs.length === 0 && state.project !== null)) {
    projectKey = key;
    if (state.connected && !previous.connected) {
      unsupported = false; // a new connection may be to a newer server
      epoch++;
      void loadEngines();
    }
    void loadRunState();
  }
  if (state.project === null) projectKey = "";
  // The node types arrive with the connection: a server that has the `solver_settings` type has `solve.*`.
  if (unsupported && state.nodeTypes !== previous.nodeTypes && state.nodeTypes.solver_settings !== undefined) {
    unsupported = false;
    epoch++;
    void loadEngines();
    void loadRunState();
  }
  // The model, the mesh and the runs decide whether a run can start: ask again after any of them moves.
  const head = state.run.runs[0];
  const was = previous.run.runs[0];
  if (state.tree !== previous.tree || head?.id !== was?.id || head?.state !== was?.state || head?.stale !== was?.stale) {
    if (state.project !== null && state.connected) scheduleSolveCheck();
  }
  if (state.run !== previous.run || state.connected !== previous.connected) syncPoller();
  // A run's end is a toast, once, however many routes (the event, the poll, the start's answer) told us.
  const note = state.run.notice;
  if (note !== null && note !== previous.run.notice) {
    void refreshRecord(note.runId);
    const id = `${note.runId}:${note.state}`;
    if (id !== toasted) {
      toasted = id;
      const toast = noticeToast(note);
      if (toast !== null) state.pushToast(toast.kind, toast.text);
      scheduleSolveCheck();
    }
  }
});
