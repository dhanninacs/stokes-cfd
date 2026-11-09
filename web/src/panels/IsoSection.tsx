/**
 * The Iso-surface section of the Results panel (ADR-0026 amendment 1): a field (`T` or `p`), a value in that
 * field's unit, and Show iso, which calls `run.iso_surface`. The answer is shown as it comes: `ready` with its
 * triangle count, `empty`, or `cutting`, which is followed by the `run.iso_ready` / `run.iso_failed` event and,
 * for an event that went missing, a slow re-ask (`ISO_POLL_MS`, backing off). A refusal (out of range, too
 * large, host unavailable) is a line in words.
 *
 * Everything that decides something is `isoModel.ts` (tested). View state only: `run.iso_surface` is a read
 * and nothing here is journaled. A ready surface is handed to the viewport (`state.iso`, drawn by
 * `viewport/isoLayer.ts` from `/api/results/iso/{run}`), replacing the one there; the section then offers its
 * visibility switch and Hide, which takes it out of the viewport.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronsUpDown, CircleAlert, LoaderCircle } from "lucide-react";
import { StokesApiError } from "../api/client";
import { Button, Switch } from "../components/ui";
import { useStore } from "../state/store";
import { cancelIsoAsk } from "../guided/looksControl";
import {
  ISO_COMMAND,
  ISO_CODES,
  ISO_POLL_MAX_MS,
  ISO_POLL_MS,
  hideIso,
  isoFields,
  setIsoVisible,
  showIso,
  isoRefusalText,
  isoStateText,
  isoValueParam,
  middleValue,
  readIsoEvent,
  readIsoReply,
  sameSurface,
  type IsoSummary,
} from "./isoModel";
import { Note } from "../components/Note";

type Shown = { kind: "idle" } | { kind: "asking" } | { kind: "summary"; summary: IsoSummary } | { kind: "refused"; text: string };

export default function IsoSection() {
  const client = useStore((s) => s.client);
  const connected = useStore((s) => s.connected);
  const data = useStore((s) => s.results.data);
  const fields = useMemo(() => isoFields(data), [data]);
  const [field, setField] = useState("");
  const [value, setValue] = useState("");
  const [shown, setShown] = useState<Shown>({ kind: "idle" });
  /** The request in flight, so an older answer never replaces a newer one. */
  const seq = useRef(0);

  // The field follows the run: the first contourable one, with the middle of its range to start from.
  const chosen = fields.find((f) => f.name === field) ?? fields[0] ?? null;
  useEffect(() => {
    if (chosen === null) return;
    if (chosen.name !== field) {
      setField(chosen.name);
      setValue(middleValue(chosen));
    }
  }, [chosen, field]);
  // New results are a new run: what was shown belongs to the old one. **Only a CHANGE of run hides the iso**, not
  // the first run this panel sees: Full mode mounts the panel after the guided Looks card may already have an iso
  // on screen, and hiding it on mount dropped a running Iso look (R2 review). The card's own ask still in flight
  // is cancelled with it, or its answer would draw the surface back after the hide.
  const lastRun = useRef<string | undefined>(undefined);
  useEffect(() => {
    const run = data?.run;
    const before = lastRun.current;
    lastRun.current = run;
    seq.current++;
    setShown({ kind: "idle" });
    if (before === undefined || before === run) return;
    cancelIsoAsk();
    const store = useStore.getState();
    store.setIso(hideIso(store.iso));
  }, [data?.run]);
  // A ready surface goes to the viewport, replacing the one there; an empty one leaves nothing drawn.
  const answered = shown.kind === "summary" ? shown.summary : null;
  useEffect(() => {
    if (answered === null) return;
    const store = useStore.getState();
    store.setIso(showIso(store.iso, answered));
  }, [answered]);
  const iso = useStore((s) => s.iso);
  const drawn = useStore((s) => s.isoDrawn);

  const ask = async (mine: number, params: Record<string, unknown>): Promise<IsoSummary | null> => {
    if (client === null) return null;
    try {
      const summary = readIsoReply(await client.call(ISO_COMMAND, params));
      if (mine !== seq.current) return null;
      if (summary === null) setShown({ kind: "refused", text: "The server's answer about the iso-surface was not understood." });
      else setShown({ kind: "summary", summary });
      return summary;
    } catch (err) {
      if (mine !== seq.current) return null;
      const code = err instanceof StokesApiError ? err.code : "";
      setShown({ kind: "refused", text: isoRefusalText(code, err instanceof Error ? err.message : String(err)) });
      return null;
    }
  };

  const show = () => {
    if (data === null || chosen === null) return;
    const param = isoValueParam(value);
    if (param === null) {
      setShown({ kind: "refused", text: "Type a value first." });
      return;
    }
    const mine = ++seq.current;
    setShown({ kind: "asking" });
    void ask(mine, { run: data.run, field: chosen.name, value: param });
  };

  // **Cutting**: the event is the answer; a slow re-ask covers an event lost to a reconnect.
  const cutting = shown.kind === "summary" && shown.summary.state === "cutting" ? shown.summary : null;
  useEffect(() => {
    if (cutting === null || client === null) return;
    const mine = seq.current;
    const off = client.onEvent((event, payload) => {
      const found = readIsoEvent(event, payload);
      if (found === null || mine !== seq.current) return;
      if (found.kind === "ready") {
        if (sameSurface(found.summary, cutting)) setShown({ kind: "summary", summary: found.summary });
      } else if (sameSurface(found, cutting) && found.code !== ISO_CODES.busy) {
        // A busy refusal is a duplicate request turned away; the first cut goes on and reports itself.
        setShown({ kind: "refused", text: isoRefusalText(found.code, found.message) });
      }
    });
    let delay = ISO_POLL_MS;
    let timer = 0;
    const again = () => {
      timer = window.setTimeout(() => {
        void ask(mine, { run: cutting.run, field: cutting.field, value: cutting.value }).then((next) => {
          if (next !== null && next.state === "cutting" && mine === seq.current) {
            delay = Math.min(delay * 2, ISO_POLL_MAX_MS);
            again();
          }
        });
      }, delay);
    };
    again();
    return () => {
      off();
      window.clearTimeout(timer);
    };
    // `ask` is a fresh closure each render; the effect is about the surface being cut.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cutting, client]);

  const busy = shown.kind === "asking" || cutting !== null;
  const disabled = !connected || chosen === null;
  return (
    <section className="mesh-section" aria-label="Iso-surface" data-ws="results-iso">
      <div className="section-title">Iso-surface</div>
      <div className="results-range">
        <label className="results-range-cell">
          <span className="muted">Field</span>
          <div className="select-wrap">
            <select
              className="field"
              aria-label="Field of the iso-surface"
              value={chosen?.name ?? ""}
              disabled={disabled}
              onChange={(e) => {
                const next = fields.find((f) => f.name === e.target.value);
                if (next === undefined) return;
                setField(next.name);
                setValue(middleValue(next));
              }}
              data-ws="results-iso-field"
            >
              {fields.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name}
                </option>
              ))}
            </select>
            <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
          </div>
        </label>
        <label className="results-range-cell">
          <span className="muted">Value</span>
          <input
            className="field mono"
            inputMode="decimal"
            spellCheck={false}
            aria-label="Iso value"
            disabled={disabled}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") show();
            }}
            data-ws="results-iso-value"
          />
        </label>
        {chosen !== null && chosen.units !== "" && (
          <span className="muted results-units" data-ws="results-iso-units">
            {chosen.units}
          </span>
        )}
      </div>
      {chosen !== null && (
        <p className="muted mesh-hint">
          {chosen.name} spans {Number(chosen.min.toPrecision(4))} to {Number(chosen.max.toPrecision(4))}
          {chosen.units !== "" ? ` ${chosen.units}` : ""}.
        </p>
      )}
      <div className="mesh-actions">
        <Button
          aria-label="Show iso"
          aria-disabled={disabled || busy || undefined}
          title={chosen === null ? "Show results with speed, T or p first" : "Cut the surface where the field equals the value"}
          onClick={() => !disabled && !busy && show()}
          data-ws="results-iso-show"
        >
          Show iso
        </Button>
      </div>
      {chosen === null && <Note className="mesh-hint" tip="Show the results of a run with speed, T or p to cut an iso-surface.">Show results first</Note>}
      {shown.kind !== "idle" && (
        <div
          className="mesh-state"
          data-tone={shown.kind === "refused" ? "bad" : busy ? "busy" : "ok"}
          data-state={shown.kind === "summary" ? shown.summary.state : shown.kind}
          aria-live="polite"
          data-ws="results-iso-state"
        >
          {busy ? <LoaderCircle size={14} aria-hidden="true" className="mesh-spin" /> : shown.kind === "refused" ? <CircleAlert size={14} aria-hidden="true" /> : null}
          <span>{shown.kind === "asking" ? "Asking the server…" : shown.kind === "refused" ? shown.text : shown.kind === "summary" ? isoStateText(shown.summary) : ""}</span>
        </div>
      )}
      {iso.surface !== null && (
        <div data-ws="results-iso-viewport">
          <Switch
            checked={iso.visible}
            onChange={(next) => useStore.getState().setIso(setIsoVisible(useStore.getState().iso, next))}
            label="Show in viewport"
            description={drawn.state === "loading" ? "Loading the surface…" : drawn.state === "error" ? drawn.message : undefined}
          />
          <div className="mesh-actions">
            <Button aria-label="Hide iso" title="Take the iso-surface out of the viewport" onClick={() => useStore.getState().setIso(hideIso(useStore.getState().iso))} data-ws="results-iso-hide">
              Hide
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
