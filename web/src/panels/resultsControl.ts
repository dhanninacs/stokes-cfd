/**
 * Reading what a run has to show into the interface (T-218): what "Show results" does, and what keeps the
 * `results` slice of the store true without a panel on screen. The decisions (what is shown, when to load,
 * when to drop, the reducers) are `fieldsModel.ts`; the names and shapes of the command and the routes are
 * `resultsApi.ts`; the per-surface arrays are fetched by `viewport/fields.ts`; this file calls `run.fields`
 * (the server answers from its cache: the fields, their ranges and units, whether there is a slice) and
 * writes the store. It does three things on its own:
 *
 * * a run that ends `done` or `done_unconverged` (`run.notice`, raised by the event or by the poll, so a
 *   lost event cannot lose the results) is read at once, whatever stage is in front;
 * * entering the Results stage with a finished run whose results are not held reads them;
 * * a new run starting over held results, or the runs going away (a project opened or closed), drops them,
 *   so the colours never show a run the case no longer matches; the view settings stay.
 *
 * Nothing here is a project command: `run.fields` is a read.
 */
import { useMemo } from "react";
import { StokesApiError } from "../api/client";
import { hasImportedCase, meshPatchesOf } from "../state/meshPatches";
import { useStore } from "../state/store";
import { RESULTS_COMMANDS, readRunFields, refusalText } from "./resultsApi";
import { dropped, failed, isFinalRun, loaded, loading, scalarFields, shouldAutoLoad, shouldDrop, type FieldOption } from "./fieldsModel";
import type { ResultsLive } from "./fieldsModel";

/** A server older than T-216 has no `run.fields`: it says so with this code. */
const UNKNOWN_COMMAND = "api.unknown_command";

function setResults(change: (live: ResultsLive) => ResultsLive): void {
  useStore.setState((s) => {
    const next = change(s.results);
    return next === s.results ? s : { results: next };
  });
}

function messageOf(err: unknown): string {
  if (err instanceof StokesApiError) return err.code === UNKNOWN_COMMAND ? "This server cannot show results yet." : refusalText(err.code, err.message);
  return err instanceof Error ? err.message : String(err);
}

let loadSeq = 0;

/** `run.fields`. An answer to an older request never replaces a newer one; the colouring refetches the arrays for new results. */
export async function loadResults(): Promise<void> {
  const { client, project } = useStore.getState();
  if (client === null || project === null) return;
  const seq = ++loadSeq;
  setResults(loading);
  try {
    const reply = readRunFields(await client.call(RESULTS_COMMANDS.fields, {}));
    if (seq !== loadSeq) return;
    if (reply === null) throw new Error("The server's answer about the results was not understood.");
    setResults((live) => loaded(live, reply));
  } catch (err) {
    if (seq !== loadSeq) return;
    const text = messageOf(err);
    setResults((live) => failed(live, text));
    useStore.getState().log("error", `${RESULTS_COMMANDS.fields}: ${text}`);
  }
}

/** "Show results": the Results stage in front and the newest finished run's results read. */
export function showResults(): void {
  const state = useStore.getState();
  if (state.stage !== "Results") state.setStage("Results");
  void loadResults();
}

/** The scalar fields the run has, for the selector; the same array while the results are the same. */
export function useFieldOptions(): FieldOption[] {
  const data = useStore((s) => s.results.data);
  return useMemo(() => scalarFields(data), [data]);
}

/** Whether a run with results is on record, so the panels can offer "Show results". */
export function useHasFinishedRun(): boolean {
  return useStore((s) => s.run.runs.some(isFinalRun));
}

// ---------------------------------------------------------------------------------------------- the ranges over every time
/** The runs whose all-times ranges were asked for, so a run is asked once a session. */
const rangesAsked = new Set<string>();

/**
 * A transient run's results read with several times and no all-times range yet: ask the server to work it out
 * (`run.time_ranges`, in the background); `run.ranges` then reads the results again, and the legend's Auto range
 * spans every time (ADR-0022 addendum 2026-11-24).
 */
function askTimeRanges(live: ResultsLive): void {
  const data = live.data;
  const { client } = useStore.getState();
  if (client === null || data === null || data.timeRanges === "ready" || data.timeRanges === undefined) return;
  if ((data.times ?? []).filter((t) => t > 0).length < 2 || rangesAsked.has(data.run)) return;
  rangesAsked.add(data.run);
  client
    .call<{ state?: string }>("run.time_ranges", { run: data.run })
    .then((reply) => {
      if (reply?.state !== "computing") return;
      // Say it is on its way until `run.ranges` reads the results again.
      setResults((now) => (now.data !== null && now.data.run === data.run && now.data.timeRanges !== "ready" ? { ...now, data: { ...now.data, timeRanges: "computing" } } : now));
    })
    .catch(() => rangesAsked.delete(data.run));
}

let rangesWired: unknown = null;

function wireRangeEvents(): void {
  const { client } = useStore.getState();
  if (client === null || client === rangesWired) return;
  rangesWired = client;
  client.onEvent((event, data) => {
    if (event !== "run.ranges") return;
    const shown = useStore.getState().results.data;
    if (shown !== null && data.run_id === shown.run) void loadResults();
  });
}

// ---------------------------------------------------------------------------------------------- the wiring
useStore.subscribe((state, previous) => {
  if (state.results.data !== previous.results.data && state.results.data !== null) {
    wireRangeEvents();
    askTimeRanges(state.results);
  }
  // A run's end: read its results now, whichever stage the window is in.
  const note = state.run.notice;
  if (note !== null && note !== previous.run.notice && (note.state === "done" || note.state === "done_unconverged")) {
    void loadResults();
    return;
  }
  // The tree too: whether the project is an imported case (load in any stage) is read from it, and after a
  // reload it can arrive after the runs - the mesh then stayed off the Mesh step until something else changed.
  if (state.stage !== previous.stage || state.run.runs !== previous.run.runs || state.project !== previous.project || state.tree !== previous.tree) {
    if (shouldDrop(state.run.runs, state.results)) {
      loadSeq++;
      setResults(dropped);
    } else if (state.connected && shouldAutoLoad(state.stage, state.run.runs, state.results, hasImportedCase(state.tree) && meshPatchesOf(state.tree).length === 0)) {
      void loadResults();
    }
  }
});
