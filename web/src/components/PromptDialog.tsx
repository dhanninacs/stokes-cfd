import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

interface PromptDialogProps {
  open: boolean;
  title: string;
  label: string;
  initialValue?: string;
  placeholder?: string;
  /** Quiet explanation under the field. */
  help?: string;
  confirmLabel?: string;
  /** Return a message to block the submit, or null when the value is fine. */
  validate?: (value: string) => string | null;
  /**
   * What to do with the value. Resolving to a message keeps the dialog open with it (a name the
   * server refused); anything else leaves the closing to the caller flipping `open`.
   */
  onSubmit: (value: string) => void | Promise<string | null | undefined>;
  onCancel: () => void;
}

/**
 * Modal single-field prompt (new project name, folder path). Used instead of window.prompt,
 * which the Qt host would draw as a native dialog. Enter submits, Escape cancels, Tab stays
 * inside, and the focus goes back to where it was.
 */
export function PromptDialog(props: PromptDialogProps) {
  // A separate component per opening, so the field always starts from initialValue.
  return props.open ? <Dialog {...props} /> : null;
}

function Dialog({ title, label, initialValue = "", placeholder, help, confirmLabel = "OK", validate, onSubmit, onCancel }: PromptDialogProps) {
  const [value, setValue] = useState(initialValue);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const cancelRef = useRef(onCancel);
  useEffect(() => {
    cancelRef.current = onCancel;
  });
  const titleId = useId();
  const fieldId = useId();
  const noteId = useId();

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    input.current?.focus();
    input.current?.select();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault(); // the global handler must not also clear the selection
      e.stopPropagation();
      cancelRef.current();
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previous?.focus?.();
    };
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const text = value.trim();
    const problem = text ? (validate?.(text) ?? null) : "Enter a value.";
    if (problem) {
      setError(problem);
      input.current?.focus();
      return;
    }
    setBusy(true);
    try {
      const message = await onSubmit(text);
      // A message blocks the submit: the dialog stays open, saying what the server said.
      if (message) {
        setError(message);
        input.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  };

  const trap = (e: KeyboardEvent<HTMLFormElement>) => {
    if (e.key !== "Tab") return;
    const nodes = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("input, button:not([disabled])"));
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  };

  return createPortal(
    <div
      className="scrim"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <form className="dialog glass" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={noteId} onSubmit={submit} onKeyDown={trap}>
        <h2 id={titleId} className="mb-3 text-[15px] font-semibold">
          {title}
        </h2>
        <label htmlFor={fieldId} className="mb-1 block text-[12.5px]">
          {label}
        </label>
        <input
          ref={input}
          id={fieldId}
          className={`field ${error ? "error" : ""}`}
          value={value}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => {
            setValue(e.target.value);
            setError(null);
          }}
        />
        <div id={noteId} className="mt-1.5 min-h-[18px] text-[12px]" role={error ? "alert" : undefined} style={{ color: error ? "var(--danger)" : "var(--text-muted)" }}>
          {error ?? help}
        </div>
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" className="btn no-drag" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn primary no-drag" disabled={busy}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
