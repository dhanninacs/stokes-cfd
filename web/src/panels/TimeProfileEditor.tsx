/**
 * The time-profile editor (ADR-0022 addendum 2026-11-24): one boundary value (an inlet's speed or mass flow,
 * an outlet's pressure, an inlet's temperature) as a constant, a table, a sine or a pulse.
 *
 * **One component for both modes**: the Guided Boundaries card opens it under a boundary's row ("Varies in
 * time"), and the boundary's Properties page in Full mode shows it under the condition. It edits a draft
 * (`timeProfileModel.ts`) and asks the server for each change: `boundary.profile_preview` checks the draft
 * and samples it over the run's time for the plot, and its refusal is shown under the field it names. Apply
 * sends `boundary.set_profile` (one undo step); Constant sends it with no profile, which keeps the value the
 * boundary had before.
 *
 * The table takes a block pasted from a spreadsheet into any of its cells: the rows replace the ones from
 * that cell on. Every control is a button or an input, in tab order; the form picker is a radio group that
 * the arrow keys move along.
 */
import { useEffect, useId, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { Plus, X } from "lucide-react";
import { StokesApiError } from "../api/client";
import { useStore } from "../state/store";
import {
  FORMS,
  FORM_FIELDS,
  VALUE_WORDS,
  addRow,
  axisNumber,
  draftFromStored,
  errorKey,
  pasteTaken,
  previewNotes,
  previewRefusals,
  isBlockPaste,
  keptAfter,
  parsePasted,
  pasteNote,
  pasteRows,
  plotShape,
  removeRow,
  setCell,
  setField,
  switchForm,
  toProfile,
  unitOf,
  type ProfileDraft,
  type ProfileForm,
  type ProfilePreview,
} from "./timeProfileModel";
import "./timeProfile.css";

const PLOT_W = 280;
const PLOT_H = 64;
const PREVIEW_DELAY_MS = 250;

export interface TimeProfileEditorProps {
  boundaryId: string;
  /** The boundary's name, for the controls' accessible names. */
  boundaryName: string;
  /** `velocity`, `mass_flow`, `pressure` or `temperature`. */
  parameter: string;
  /** The value's constant (SI), which a new profile is built around. */
  constant: number | null;
  /** The value's stored profile, or null while it is constant. */
  stored: unknown;
  /** The form a constant value opens on (the "Varies in time" control opens a sine). */
  openForm?: ProfileForm;
  /** Called after Apply or Constant has been accepted. */
  onDone?: () => void;
}

function messageOf(err: unknown): { field: string; message: string } {
  if (err instanceof StokesApiError) return { field: errorKey(typeof err.details.field === "string" ? err.details.field : undefined), message: err.message };
  return { field: "form", message: err instanceof Error ? err.message : String(err) };
}

/** The draft's preview from the server, asked again a moment after the last change. */
function usePreview(parameter: string, draft: ProfileDraft): ProfilePreview | null {
  const [preview, setPreview] = useState<ProfilePreview | null>(null);
  // The preview of the draft as it is now: a refusal while a field is wrong.
  useEffect(() => {
    const profile = toProfile(draft, parameter);
    if (profile === null) {
      setPreview(null);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      const client = useStore.getState().client;
      if (client === null) return;
      client
        .call<ProfilePreview>("boundary.profile_preview", { parameter, profile, points: 160 })
        .then((reply) => live && setPreview(reply))
        .catch((err: unknown) => live && setPreview({ ok: false, ...messageOf(err) }));
    }, PREVIEW_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [parameter, draft]);
  return preview;
}

function FormPicker({ value, onChange, label }: { value: ProfileForm; onChange: (form: ProfileForm) => void; label: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (step === 0) return;
    e.preventDefault();
    const next = (index + step + FORMS.length) % FORMS.length;
    onChange(FORMS[next].id);
    refs.current[next]?.focus();
  };
  return (
    <div className="segmented tp-forms" role="radiogroup" aria-label={label} data-ws="profile-form">
      {FORMS.map((form, index) => {
        const on = form.id === value;
        return (
          <button
            key={form.id}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            title={form.tip}
            data-form={form.id}
            onClick={() => onChange(form.id)}
            onKeyDown={(e) => move(e, index)}
          >
            {form.label}
          </button>
        );
      })}
    </div>
  );
}

function FieldNote({ text, field }: { text: string | undefined; field: string }) {
  return text ? (
    <span role="status" className="tp-note" data-ws="profile-note" data-field={field}>
      {text}
    </span>
  ) : null;
}

function FieldError({ id, text }: { id: string; text: string | undefined }) {
  return text ? (
    <span id={id} role="alert" className="tp-error">
      {text}
    </span>
  ) : null;
}

/**
 * The waveform. While a field is wrong the last good waveform stays, faded, rather than an empty block; a
 * dashed mark shows where one period (or the table) ends when the plot runs past it.
 */
function Plot({ preview, parameter, stale }: { preview: ProfilePreview | null; parameter: string; stale: boolean }) {
  const shape = useMemo(
    () => (preview?.ok && preview.times && preview.values ? plotShape(preview.times, preview.values, PLOT_W, PLOT_H, preview.profile?.mean ?? null) : null),
    [preview],
  );
  const unit = unitOf("value", parameter);
  if (shape === null)
    return (
      <div className="tp-plot tp-plot-empty muted" data-ws="profile-plot-empty">
        {stale ? "No plot until the field marked in red is corrected" : "Plotting…"}
      </div>
    );
  const run = preview?.run_end_time;
  const cycle = preview?.profile?.cycle ?? 0;
  const cycleX = cycle > 0 && cycle < shape.end ? (cycle / shape.end) * PLOT_W : null;
  return (
    <figure className={`tp-plot${stale ? " tp-plot-stale" : ""}`} data-ws="profile-plot" aria-label={`${VALUE_WORDS[parameter] ?? parameter} over time, ${axisNumber(shape.low)} to ${axisNumber(shape.high)} ${unit}`}>
      <svg viewBox={`0 0 ${PLOT_W} ${PLOT_H}`} preserveAspectRatio="none" role="img" aria-hidden="true">
        {shape.meanY !== null && <line className="tp-mean" x1={0} x2={PLOT_W} y1={shape.meanY} y2={shape.meanY} />}
        {cycleX !== null && <line className="tp-cycle" x1={cycleX} x2={cycleX} y1={0} y2={PLOT_H} />}
        <path className="tp-wave" d={shape.path} vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="tp-axes">
        <span>
          {axisNumber(shape.low)} to {axisNumber(shape.high)} {unit}
        </span>
        <span>
          {cycleX !== null ? `${(preview?.profile as { form?: string } | undefined)?.form === "table" ? "table ends" : "period"} ${axisNumber(cycle)} s · ` : ""}0 to {axisNumber(shape.end)} s{run ? " (run)" : ""}
        </span>
      </figcaption>
    </figure>
  );
}

export default function TimeProfileEditor({ boundaryId, boundaryName, parameter, constant, stored, openForm = "sine", onDone }: TimeProfileEditorProps) {
  const base = useId();
  const initial = useMemo(() => {
    const read = draftFromStored(stored, parameter, constant);
    return read.form === "constant" && openForm !== "constant" ? switchForm(read, openForm, parameter, constant) : read;
    // The draft starts from what is stored when the editor opens; a later store change does not reset typing.
  }, [boundaryId, parameter]);
  const [draft, setDraft] = useState<ProfileDraft>(initial);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<{ field: string; message: string } | null>(null);
  const preview = usePreview(parameter, draft);
  // The last preview the server accepted, which the plot keeps showing while a field is wrong.
  const [good, setGood] = useState<ProfilePreview | null>(null);
  useEffect(() => {
    if (preview?.ok) setGood(preview);
  }, [preview]);
  const refused = preview !== null && !preview.ok;
  const [pasted, setPasted] = useState<{ text: string; warn: boolean } | null>(null);
  const word = VALUE_WORDS[parameter] ?? parameter;
  const valueUnit = unitOf("value", parameter);

  const errors: Record<string, string> = {};
  for (const refused of previewRefusals(preview)) errors[refused.field] = refused.message;
  if (refusal !== null) errors[refusal.field] = refusal.message;
  const fieldNotes = previewNotes(preview);

  // The server names every wrong row when it can (`errors`); when it names only the first, say so.
  const oneAtATime = preview !== null && !preview.ok && preview.errors === undefined && (preview.field ?? "").startsWith("rows.");

  const edit = (next: ProfileDraft) => {
    setRefusal(null);
    setPasted(null);
    setDraft(next);
  };

  const commit = async (profile: Record<string, unknown> | null) => {
    if (busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      await useStore.getState().execute("boundary.set_profile", { boundary: boundaryId, parameter, profile }, { silent: true });
      await useStore.getState().refresh();
      onDone?.();
    } catch (err) {
      setRefusal(messageOf(err));
    } finally {
      setBusy(false);
    }
  };

  const onPaste = (e: ClipboardEvent<HTMLInputElement>, index: number) => {
    const text = e.clipboardData.getData("text");
    if (!isBlockPaste(text)) return;
    const block = parsePasted(text);
    // A paste that left every line out (a thousands-grouped comma list) is still taken and said, not typed raw.
    if (!pasteTaken(block)) return;
    e.preventDefault();
    const next = pasteRows(draft, index, block.rows);
    edit(next);
    setPasted(pasteNote(block, keptAfter(next.rows.length, index, block.rows.length)));
  };

  const fieldId = (name: string) => `${base}-${name}`;
  const errorId = (name: string) => `${base}-${name}-error`;
  const described = (name: string) => (errors[name] ? errorId(name) : undefined);

  return (
    <div className="tp" data-ws="time-profile" data-parameter={parameter} data-form={draft.form} aria-label={`${word} of ${boundaryName} over time`} role="group">
      <FormPicker label={`How ${word.toLowerCase()} varies`} value={draft.form} onChange={(form) => edit(switchForm(draft, form, parameter, constant))} />
      {draft.form === "sine" || draft.form === "square" ? (
        <div className="tp-fields">
          {FORM_FIELDS[draft.form].map((field) => {
            const unit = unitOf(field.unit, parameter);
            return (
              <div key={field.name} className="tp-field">
                <label htmlFor={fieldId(field.name)} className="tp-label">
                  {field.label}
                </label>
                <span className="tp-input">
                  <input
                    id={fieldId(field.name)}
                    className="field"
                    inputMode="decimal"
                    spellCheck={false}
                    autoComplete="off"
                    aria-invalid={errors[field.name] ? true : undefined}
                    aria-describedby={described(field.name)}
                    data-field={field.name}
                    value={draft.fields[field.name] ?? ""}
                    onChange={(e) => edit(setField(draft, field.name, e.target.value))}
                  />
                  <span className="muted tp-unit">{unit === "" ? "0-1" : unit}</span>
                </span>
                <FieldError id={errorId(field.name)} text={errors[field.name]} />
                {!errors[field.name] && <FieldNote field={field.name} text={fieldNotes[field.name]} />}
              </div>
            );
          })}
        </div>
      ) : null}
      {draft.form === "table" ? (
        <div className="tp-table-wrap">
          <table className="tp-table" aria-label={`${word} table`} data-ws="profile-table">
            <thead>
              <tr>
                <th scope="col">Time s</th>
                <th scope="col">
                  {word} {valueUnit}
                </th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {draft.rows.map((row, index) => (
                <tr key={index} data-row={index}>
                  {(["time", "value"] as const).map((column) => {
                    const key = `rows.${index}.${column}`;
                    return (
                      <td key={column}>
                        <input
                          className="field"
                          inputMode="decimal"
                          spellCheck={false}
                          autoComplete="off"
                          aria-label={`Row ${index + 1} ${column === "time" ? "time in s" : `${word.toLowerCase()} in ${valueUnit}`}`}
                          aria-invalid={errors[key] ? true : undefined}
                          aria-describedby={described(key)}
                          data-cell={key}
                          value={row[column]}
                          onChange={(e) => edit(setCell(draft, index, column, e.target.value))}
                          onPaste={(e) => onPaste(e, index)}
                        />
                        <FieldError id={errorId(key)} text={errors[key]} />
                      </td>
                    );
                  })}
                  <td>
                    <button type="button" className="guided-x no-drag" aria-label={`Remove row ${index + 1}`} title="Remove this row" disabled={draft.rows.length <= 2} onClick={() => edit(removeRow(draft, index))}>
                      <X size={12} aria-hidden="true" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="btn ghost tp-add no-drag" onClick={() => edit(addRow(draft))} data-ws="profile-add-row" title="Add a row; or paste rows from a spreadsheet into a cell">
            <Plus size={12} aria-hidden="true" /> Add row
          </button>
          <FieldError id={errorId("rows")} text={errors.rows} />
          {oneAtATime && (
            <span
              className="tp-note"
              data-ws="profile-one-refusal"
              title="The rows are checked in order: the next problem, if any, shows once this one is corrected."
            >
              Rows are checked in order
            </span>
          )}
          {pasted !== null && (
            <span role="status" className={pasted.warn ? "tp-error" : "tp-note"} data-ws="profile-paste-note">
              {pasted.text}
            </span>
          )}
        </div>
      ) : null}
      {draft.form !== "constant" && <Plot preview={refused ? good : preview} parameter={parameter} stale={refused} />}
      <FieldError id={errorId("form")} text={errors.form} />
      {!errors.form && <FieldNote field="form" text={fieldNotes.form} />}
      <div className="tp-actions">
        {draft.form === "constant" ? (
          <button type="button" className="btn primary no-drag" disabled={busy} onClick={() => void commit(null)} data-ws="profile-constant">
            {stored ? "Make constant" : "Keep constant"}
          </button>
        ) : (
          <button
            type="button"
            className="btn primary no-drag"
            disabled={busy || refused}
            title={refused ? "Correct the field marked in red first" : undefined}
            onClick={() => void commit(toProfile(draft, parameter))}
            data-ws="profile-apply"
          >
            {busy ? "Applying…" : "Apply"}
          </button>
        )}
        {onDone !== undefined && (
          <button type="button" className="btn ghost no-drag" disabled={busy} onClick={onDone}>
            Close
          </button>
        )}
      </div>
    </div>
  );
}
