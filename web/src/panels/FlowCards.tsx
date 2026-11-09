/**
 * The two flow helpers (ADR-0022 §10.2, T-050): "External flow…" and "Internal flow…".
 *
 * §10.2 is explicit that these reuse what exists and that there is **no wizard framework**:
 *
 * * External flow is the enclosure card plus a velocity field and an "Add default conditions" switch
 *   (on); when the operation finishes, `setup.defaults(external)` runs and the conditions list opens.
 * * Internal flow is the extract-fluid card with its seed pick, then an **Openings** step: the caps
 *   are highlighted, the user picks the inlet cap(s), Next, the outlet cap(s), Finish — which calls
 *   `setup.defaults(internal, inlet, outlet)`.
 *
 * The state machine is `flowCardsLogic.ts` (tested without a renderer). This file renders the small extra
 * fields and drives the sequence; the operation itself is run by the existing `OperationCard`, so
 * there is one card implementation, not two.
 *
 * The `op_done` hook lives here because it belongs to these helpers alone: it fires only when a run
 * this component started finishes, which it tracks by operation type. A run started anywhere else is
 * left alone.
 */

import { useEffect, useRef, useState } from "react";
import { StokesApiError } from "../api/client";
import { useStore } from "../state/store";
import { Button } from "../components/ui";
import {
  DEFAULT_EXTERNAL,
  NO_OPENINGS,
  beginOpenings,
  defaultsParams,
  openingsFinished,
  openingsPrompt,
  openingsReady,
  shouldAddDefaults,
  takeOpenings,
  type ExternalFlowOptions,
  type FlowTemplate,
  type OpeningsState,
} from "./flowCardsLogic";

/**
 * The helpers' transient state in the store is avoided on purpose: a flow is one run plus a short
 * pick sequence, so it lives in the component that started it.
 */
export interface FlowRun {
  id: number;
  template: FlowTemplate;
  /** The body the operation was run on; `setup.defaults` takes it as `target`. */
  target: string;
  external: ExternalFlowOptions;
}

export interface FlowCardsProps {
  /** The run in progress, or null when neither helper is open. */
  run: FlowRun | null;
  onClose(): void;
}

/**
 * The panel the two helpers show while they are active: the external fields, or the openings step.
 *
 * It is deliberately small and sits above the operation card, which is what actually runs the
 * enclosure or the extraction — "there is no wizard framework" (§10.2).
 */
export default function FlowCards({ run, onClose }: FlowCardsProps) {
  const [external, setExternal] = useState<ExternalFlowOptions>(DEFAULT_EXTERNAL);
  const [openings, setOpenings] = useState<OpeningsState>(NO_OPENINGS);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const started = useRef<number | null>(null);

  // A new run resets both the fields and the openings sequence.
  useEffect(() => {
    if (run === null) return;
    setExternal(DEFAULT_EXTERNAL);
    setOpenings(NO_OPENINGS);
    setNote("");
    setBusy(false);
  }, [run?.id]);

  // Watch the run's operation finish. `op_done` is emitted for every run, so this only reacts to the
  // one this helper started, matched by type and by the target part.
  useEffect(() => {
    if (run === null || started.current === run.id) return;
    started.current = run.id;
    const stop = useStore.subscribe((s) => {
      const done = s.lastOperationDone;
      if (done === null) return;
      if (done.type !== (run.template === "external" ? "enclosure" : "extract_fluid")) return;
      // The node the run made is what `setup.defaults` takes as its target; without one there is
      // nothing to give conditions to.
      void afterRun(run, done.output);
    });
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id]);

  /** Call `setup.defaults` for a finished run, then say what happened. */
  async function afterRun(active: FlowRun, node: string | null): Promise<void> {
    const state = useStore.getState();
    if (state.client === null || node === null) return;
    if (!shouldAddDefaults(active.template, active.external)) {
      onClose();
      return;
    }
    setBusy(true);
    try {
      const params = defaultsParams(
        active.template,
        active.target,
        openings,
        active.external,
      );
      const answer = (await state.execute("setup.defaults", params, { silent: true })) as {
        boundaries?: unknown[];
        material?: string | null;
        walls?: number;
      };
      const boundaries = answer.boundaries?.length ?? 0;
      setNote(
        `Added ${boundaries} condition${boundaries === 1 ? "" : "s"}` +
          (answer.material ? ` · ${answer.material}` : ""),
      );
      useStore.getState().pushToast(
        "ok",
        `Default conditions added${answer.material ? ` · ${answer.material}` : ""}`,
      );
      onClose();
    } catch (err) {
      const message = err instanceof StokesApiError ? err.message : String(err);
      setNote(message);
      useStore.getState().pushToast("error", message);
    } finally {
      setBusy(false);
    }
  }

  if (run === null) return null;

  /** Take the live pick as the answer to the current openings step. */
  const takePicked = () => {
    const picked = useStore.getState().picked;
    // The openings state works in ranges (the tag member form); the pick holds individual ids, so
    // contiguous runs become ranges here — the same conversion the Related menu makes.
    const members = Object.entries(picked.faces)
      .filter(([, faces]) => faces.length > 0)
      .map(([part, faces]) => ({ part, faces: toRanges(faces) }));
    if (members.length === 0) {
      setNote("Pick the cap faces in the viewport first.");
      return;
    }
    setOpenings((current) => takeOpenings(current, members));
    setNote("");
  };

  return (
    <section className="flow-card glass" data-ws="flow-card" aria-label={`${run.template} flow`}>
      <header className="flex items-center gap-2 pb-2">
        <span className="section-title">
          {run.template === "external" ? "External flow" : "Internal flow"}
        </span>
        <Button className="ml-auto !h-6 !px-2 text-[11.5px]" onClick={onClose} disabled={busy}>
          Close
        </Button>
      </header>
      {run.template === "external" ? (
        <>
          <label className="mb-2 block text-[12.5px]">
            Velocity
            <input
              className="field mt-1"
              placeholder="e.g. 2 [m/s], or leave to the check"
              value={external.velocity}
              spellCheck={false}
              autoComplete="off"
              disabled={busy}
              onChange={(e) => setExternal((current) => ({ ...current, velocity: e.target.value }))}
            />
          </label>
          <label className="mb-2 flex items-center gap-2 text-[12.5px]">
            <input
              type="checkbox"
              checked={external.addDefaults}
              disabled={busy}
              onChange={(e) => setExternal((current) => ({ ...current, addDefaults: e.target.checked }))}
            />
            Add default conditions
          </label>
        </>
      ) : (
        <>
          <p className="muted m-0 mb-2 text-[12px]">
            {openings.step === null
              ? "Run the extraction, then pick the openings."
              : openingsPrompt(openings)}
          </p>
          {openings.step !== null && (
            <div className="flex items-center gap-2">
              <Button disabled={busy} onClick={takePicked}>
                {openings.step === "inlet" ? "Use pick as inlet" : "Use pick as outlet"}
              </Button>
              <Button
                variant="primary"
                disabled={busy || !openingsReady(openings)}
                onClick={() => {
                  // Finish is only meaningful with both: the run's own completion calls the defaults.
                  if (openingsFinished(openings)) setNote("Both openings taken; run the extraction.");
                  else setNote("Take the inlet first, then the outlet.");
                }}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
      {note !== "" && (
        <p role="status" className="m-0 mt-2 text-[12px]">
          {note}
        </p>
      )}
    </section>
  );
}

/** Contiguous face ids as sorted inclusive ranges, the form a tag member uses. */
export function toRanges(faces: readonly number[]): [number, number][] {
  if (faces.length === 0) return [];
  const sorted = [...faces].sort((a, b) => a - b);
  const ranges: [number, number][] = [];
  let start = sorted[0];
  let previous = sorted[0];
  for (const id of sorted.slice(1)) {
    if (id === previous + 1) {
      previous = id;
      continue;
    }
    ranges.push([start, previous]);
    start = id;
    previous = id;
  }
  ranges.push([start, previous]);
  return ranges;
}

/** Start an openings sequence for an internal flow, for the caller that owns the run. */
export function startOpenings(): OpeningsState {
  return beginOpenings();
}
