/**
 * The small pieces the Explore dialogs are made of: a dialog around one form that shows what the server
 * refused beside the fields, labelled rows, text and quantity inputs, and a select drawn the way the rest
 * of the app draws it (no native arrow: panels.css `.select-wrap`).
 */
import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ChevronsUpDown } from "lucide-react";
import { Dialog } from "../../components/Dialog";
import { describeError } from "./commands";

const BARE_NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

/** A bare number is in the unit the field shows; anything else (a quantity, an expression) carries its own. */
export function quantityText(text: string, unit: string): string {
  const value = text.trim();
  return BARE_NUMBER.test(value) && unit ? `${value} [${unit}]` : value;
}

/** A list the way people type it: "1, 2, 3" or "1 2 3"; an item with its own unit ("2 [m/s]") stays whole. */
export function splitList(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  const tokens = trimmed.split(/[\s,;]+/).filter(Boolean);
  if (!/[,;]/.test(trimmed) && tokens.every((t) => BARE_NUMBER.test(t))) return tokens;
  return trimmed.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
}

export function wholeNumber(text: string): number | null {
  const n = Number(text.trim());
  return text.trim() !== "" && Number.isInteger(n) ? n : null;
}

// ---------------------------------------------------------------------------------------------- dialog
interface FormDialogProps {
  title: string;
  submit: string;
  wide?: boolean;
  onClose: () => void;
  /** Runs when the form is submitted; throw to show the message beside the fields and keep the dialog open. */
  onSubmit: () => Promise<void>;
  /** The dialog cannot be submitted yet. */
  disabled?: boolean;
  children: ReactNode;
}

/** A dialog around one form: Enter submits, the server's refusal shows inside, and the dialog closes on success. */
export function FormDialog({ title, submit, wide, onClose, onSubmit, disabled, children }: FormDialogProps) {
  const formId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const busyRef = useRef(false);

  const run = async (e: FormEvent) => {
    e.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await onSubmit();
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      wide={wide}
      title={title}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn no-drag" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form={formId} className="btn primary no-drag" disabled={busy || disabled}>
            {submit}
          </button>
        </>
      }
    >
      <form id={formId} className="xf-form" onSubmit={run} noValidate>
        {children}
        <div className="xf-error" role={error ? "alert" : undefined}>
          {error}
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------------- fields
export function FieldRow({ label, htmlFor, aside, hint, error, children }: { label: string; htmlFor?: string; aside?: ReactNode; hint?: string; error?: string; children: ReactNode }) {
  return (
    <div className="xf-row">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <label htmlFor={htmlFor} className="text-[12.5px]">
          {label}
        </label>
        {aside && <span className="muted text-[11px]">{aside}</span>}
      </div>
      {children}
      {error ? (
        <div role="alert" className="mt-1 text-[12px]" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      ) : (
        hint && <div className="muted mt-1 text-[11.5px] leading-snug">{hint}</div>
      )}
    </div>
  );
}

interface InputProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  mono?: boolean;
  invalid?: boolean;
  label?: string;
}

export function TextInput({ id, value, onChange, placeholder, mono, invalid, label }: InputProps) {
  return (
    <input
      id={id}
      className={`field ${mono ? "mono" : ""} ${invalid ? "error" : ""}`}
      value={value}
      placeholder={placeholder}
      autoComplete="off"
      spellCheck={false}
      aria-label={label}
      aria-invalid={invalid || undefined}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** A quantity input: the unit it is read in sits inside its right edge, as in the Properties panel. */
export function UnitInput({ id, value, onChange, unit, placeholder, invalid, label }: InputProps & { unit: string }) {
  return (
    <div className="xf-unit">
      <input
        id={id}
        className={`field ${invalid ? "error" : ""}`}
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        aria-label={label}
        aria-invalid={invalid || undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {unit && <span className="xf-unit-chip muted">{unit}</span>}
    </div>
  );
}

export function SelectInput({ id, value, onChange, children, label }: { id: string; value: string; onChange: (value: string) => void; children: ReactNode; label?: string }) {
  return (
    <div className="select-wrap">
      <select id={id} className="field" value={value} aria-label={label} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
      <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
    </div>
  );
}
