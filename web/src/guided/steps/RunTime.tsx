/**
 * The Run card's time (ADR-0022 addendum 2026-11-24): steady or transient, and a transient run's end time and
 * time step.
 *
 * Shown when it matters: a boundary value varies in time (the set-up check's `setup.profile_steady` says the
 * steady run would use its time-mean), or the run is transient already. **The switch follows the finding's
 * fix**: Transient is offered only when the fix is `solve.configure {transient: true}`. A conjugate case is
 * written steady only for now, so there the finding has no such fix, the card says the run uses the mean and
 * offers no Transient (a transient conjugate run is refused at compile). The switch and the two times go
 * through `solve.configure`, as the card's other fields do; a time takes a number in seconds or quantity text
 * (`2 [ms]`), and a refusal is shown under the field. The check's `setup.profile_short` (the run ends before
 * one period) shows under the times.
 */
import { useEffect, useState } from "react";
import { configureSolve } from "../../panels/runControl";
import { useStore } from "../../state/store";
import { Button } from "../../components/ui";
import type { NodeSummary, SetupCheck } from "../../api/types";
import { StatusDot } from "../../components/ui";
import { messageOf } from "../commands";
import { Note } from "../controls";
import type { ActionState } from "../useAction";
import { fixSwitchesTo, readWriteSuggestion, saveEveryNote, saveEveryProblem, secondsInput, stepProblem, timeFindings, type WriteSuggestion } from "./runTimeModel";

function seconds(raw: unknown): number | null {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "object" && raw !== null && typeof (raw as { value?: unknown }).value === "number") return (raw as { value: number }).value;
  return null;
}

function tidy(n: number | null): string {
  return n === null ? "" : String(Number(n.toPrecision(6)));
}

function SecondsBox({
  label,
  value,
  disabled,
  onCommit,
  ws,
  check,
  placeholder,
  title,
}: {
  label: string;
  value: number | null;
  disabled: boolean;
  onCommit: (v: number | string) => Promise<void>;
  ws: string;
  /** A rule a typed number must keep, checked before it is sent: the reason, or null. The server checks it too. */
  check?: (v: number) => string | null;
  placeholder?: string;
  title?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState("");
  const commit = () => {
    const text = draft;
    if (text === null) return;
    const read = secondsInput(text);
    if ("error" in read) {
      setError(`${label} ${read.error}`);
      return;
    }
    const broken = typeof read.value === "number" && check !== undefined ? check(read.value) : null;
    if (broken !== null) {
      setError(`${label} ${broken}`);
      return;
    }
    if (read.value === value) {
      setDraft(null);
      setError("");
      return;
    }
    onCommit(read.value).then(
      () => {
        setDraft(null);
        setError("");
      },
      (err: unknown) => setError(messageOf(err)),
    );
  };
  const errorId = `${ws}-error`;
  return (
    <>
      <label className="guided-field">
        <span>{label} (s)</span>
        <input
          className="field"
          inputMode="decimal"
          spellCheck={false}
          autoComplete="off"
          aria-label={`${label} in s`}
          aria-invalid={error !== "" ? true : undefined}
          aria-describedby={error !== "" ? errorId : undefined}
          title={title ?? "Seconds, or a quantity such as 20 [ms]"}
          disabled={disabled}
          placeholder={placeholder}
          value={draft ?? tidy(value)}
          onChange={(e) => {
            setDraft(e.target.value);
            setError("");
          }}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") {
              setDraft(null);
              setError("");
            }
          }}
          data-ws={ws}
        />
      </label>
      {error !== "" && (
        <span id={errorId} role="alert" className="guided-run-time-error" data-ws={`${ws}-error`}>
          {error}
        </span>
      )}
    </>
  );
}

/** How often the fields are written: `solve.write_suggestion` asked again whenever the settings change. */
function useWriteSuggestion(settings: NodeSummary | null, on: boolean): WriteSuggestion | null {
  const execute = useStore((s) => s.execute);
  const [found, setFound] = useState<WriteSuggestion | null>(null);
  const key = on ? JSON.stringify([settings?.params.end_time, settings?.params.delta_t, settings?.params.write_time, settings?.params.write_interval]) : "";
  useEffect(() => {
    if (!on) return;
    let live = true;
    execute("solve.write_suggestion", {}, { silent: true })
      .then((raw) => live && setFound(readWriteSuggestion(raw)))
      .catch(() => live && setFound(null));
    return () => {
      live = false;
    };
    // `key` stands for the settings it is made of.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [execute, on, key]);
  return on ? found : null;
}

export default function RunTime({ settings, setup, active, action }: { settings: NodeSummary | null; setup: SetupCheck | null; active: boolean; action: ActionState }) {
  const transient = settings?.params.transient === true;
  const { steady, short } = timeFindings(setup);
  const suggestion = useWriteSuggestion(settings, transient);
  if (!transient && steady === null) return null;
  const end = seconds(settings?.params.end_time);
  const step = seconds(settings?.params.delta_t);
  const every = seconds(settings?.params.write_time);
  const note = suggestion === null ? null : saveEveryNote(suggestion);
  // The switch is offered where the check's fix says the run can go that way (not to transient on a conjugate case).
  const towards = fixSwitchesTo(steady);
  const offerSwitch = transient || towards === true;
  const set = (values: Record<string, unknown>) =>
    void action.run(async () => {
      if (settings === null) await configureSolve({});
      await configureSolve({ values });
    });
  // A time is committed here rather than through `action`, so its refusal shows under its own field.
  const setTime = async (values: Record<string, unknown>) => {
    if (settings === null) await configureSolve({});
    await configureSolve({ values });
  };
  return (
    <div className="guided-run-time" data-ws="guided-run-time">
      {offerSwitch && (
        <div className="segmented" role="radiogroup" aria-label="Steady or transient run">
          {(["steady", "transient"] as const).map((mode) => {
            const on = (mode === "transient") === transient;
            return (
              <button key={mode} type="button" role="radio" aria-checked={on} disabled={active || action.busy} onClick={() => !on && set({ transient: mode === "transient" })} data-ws={`guided-${mode}`}>
                {mode === "steady" ? "Steady" : "Transient"}
              </button>
            );
          })}
        </div>
      )}
      {steady !== null && (
        <div className="guided-run-time-warn" data-ws="guided-needs-transient">
          <StatusDot status="warning" />
          <Note tip={towards === true ? `${steady.message}. A transient run follows the profile in time.` : steady.message}>
            {towards === true ? "A value varies in time: run transient" : towards === false ? "Conjugate runs are steady: switch back" : "Steady run: values held at their time-mean"}
          </Note>
        </div>
      )}
      {transient && (
        <>
          <SecondsBox label="End time" value={end} disabled={active || action.busy} onCommit={(v) => setTime({ end_time: v })} check={(v) => (step !== null && step > v * (1 + 1e-9) ? `is shorter than the time step (${tidy(step)} s)` : null)} ws="guided-end-time" />
          <SecondsBox label="Time step" value={step} disabled={active || action.busy} onCommit={(v) => setTime({ delta_t: v })} check={(v) => stepProblem(v, end)} ws="guided-time-step" />
          {/* How often the fields are written: the frames of the time slider and the playback. */}
          <SecondsBox
            label="Save every"
            value={every !== null && every > 0 ? every : null}
            placeholder={suggestion !== null && suggestion.current.steps ? `${tidy(suggestion.current.writeTime)} (every ${settings?.params.write_interval ?? 50} steps)` : undefined}
            title="How often the fields are written, in seconds or as a quantity such as 10 [ms]: each write is a frame of the time slider and the playback"
            disabled={active || action.busy}
            onCommit={(v) => setTime({ write_time: v })}
            check={(v) => saveEveryProblem(v, step, end)}
            ws="guided-save-every"
          />
          {note !== null && (
            <div className="guided-run-time-frames" data-ws="guided-save-frames">
              <Note tip={suggestion?.period != null ? `The fastest profile repeats every ${tidy(suggestion.period)} s; about 20 frames a period draw it smoothly` : "About 50 frames over the run draw it smoothly"}>
                {note.text}
              </Note>
              {note.offer !== null && (
                <Button disabled={active || action.busy} onClick={() => void setTime({ write_time: note.offer }).catch((e: unknown) => action.fail(messageOf(e)))} data-ws="guided-save-suggested">
                  {`Use ${tidy(note.offer)} s (${suggestion?.frames ?? 0} frames)`}
                </Button>
              )}
            </div>
          )}
          {short !== null && (
            <Note tip={short} data-ws="guided-run-short">
              Ends before one period of the profile
            </Note>
          )}
        </>
      )}
    </div>
  );
}
