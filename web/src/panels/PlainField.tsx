import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Pencil } from "lucide-react";
import type { NodeSummary } from "../api/types";
import { useStore } from "../state/store";
import { isStructural } from "./nodeRules";
import { FOCUS_TREE_EVENT } from "./treeKeyboard";

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** A field's inline error, under it; the panels and `SchemaFields` share it. */
export function FieldError({ id, message }: { id: string; message: string }) {
  if (!message) return null;
  return (
    <div id={id} role="alert" className="mt-1 text-[12px]" style={{ color: "var(--danger)" }}>
      {message}
    </div>
  );
}

type Parsed = { ok: true; value: unknown } | { ok: false; error: string };

/**
 * A single-line input with the panel's edit contract: Enter applies, Esc reverts to the last applied
 * text (and, when there is nothing to revert, hands focus back to the tree), blur applies pending edits,
 * and a refusal from the server shows inline in red.
 */
function CommitInput({
  value,
  parse,
  onCommit,
  disabled,
  field,
  className = "",
  trailing,
  label,
  inputId,
  errorId,
}: {
  /** The applied text from the model. */
  value: string;
  /** Turns the text into what to send, or an error message. */
  parse?: (text: string) => Parsed;
  /** Sends it. Resolves to an error message, or "" on success. */
  onCommit: (value: unknown) => Promise<string>;
  disabled?: boolean;
  field: "plain" | "name";
  className?: string;
  trailing?: ReactNode;
  label?: string;
  inputId: string;
  errorId: string;
}) {
  const [text, setText] = useState(value);
  const [error, setError] = useState("");
  const sent = useRef<string | null>(null); // text already sent, until the model echoes it back
  useEffect(() => {
    setText(value); // the model changed (another client, undo): show it
    setError("");
    sent.current = null;
  }, [value]);

  const apply = async () => {
    if (text === value || text === sent.current) return;
    const parsed: Parsed = parse ? parse(text) : { ok: true, value: text };
    if (!parsed.ok) return setError(parsed.error);
    const failure = await onCommit(parsed.value);
    if (!failure) sent.current = text;
    setError(failure);
  };

  return (
    <>
      <div className="relative">
        <input
          id={inputId}
          data-prop-field={field}
          aria-label={label}
          aria-invalid={error !== ""}
          aria-describedby={error ? errorId : undefined}
          className={`field ${error ? "error" : ""} ${className}`}
          value={text}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setText(e.target.value);
            setError("");
          }}
          onBlur={() => void apply()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void apply();
            } else if (e.key === "Escape") {
              e.preventDefault();
              if (text !== value || error) {
                setText(value);
                setError("");
              } else {
                e.currentTarget.blur();
                window.dispatchEvent(new CustomEvent(FOCUS_TREE_EVENT));
              }
            }
          }}
        />
        {trailing}
      </div>
      <FieldError id={errorId} message={error} />
    </>
  );
}

/** The editable node name in the panel header. The fixed groups of the project skeleton stay read-only. */
export function NameField({ node }: { node: NodeSummary }) {
  const execute = useStore((s) => s.execute);
  const id = useId();
  const errorId = useId();
  const rename = useCallback(
    async (value: unknown): Promise<string> => {
      const name = String(value).trim();
      if (!name) return "A name is required.";
      try {
        await execute("node.rename", { node: node.id, name }, { silent: true });
        return "";
      } catch (err) {
        return messageOf(err);
      }
    },
    [execute, node.id],
  );
  return (
    <CommitInput
      value={node.name}
      onCommit={rename}
      disabled={isStructural(node)}
      field="name"
      label="Name"
      className="!h-9 pr-8 text-[14px] font-semibold"
      inputId={id}
      errorId={errorId}
      trailing={
        <Pencil size={12} className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[var(--text-faint)]" aria-hidden="true" />
      }
    />
  );
}
