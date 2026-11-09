/**
 * The small controls the cards share: a row of choice chips, an error line and a note. Theme tokens only
 * (`guided.css`); a chip says what it is in words and carries `aria-pressed`, so a state is never colour alone.
 */

export interface Choice<T extends string> {
  value: T;
  label: string;
  /** A second line under the label (the length a size means). */
  sub?: string;
  /** Why the choice cannot be made now; it becomes the tooltip. */
  disabled?: string;
}

export function Chips<T extends string>({
  label,
  value,
  choices,
  onChange,
  busy = false,
  wide = false,
}: {
  label: string;
  /** The chosen one, or null when none of them is. */
  value: T | null;
  choices: readonly Choice<T>[];
  onChange: (value: T) => void;
  busy?: boolean;
  wide?: boolean;
}) {
  return (
    <div className={`guided-chips${wide ? " wide" : ""}`} role="group" aria-label={label}>
      {choices.map((c) => (
        <button
          key={c.value}
          type="button"
          className="guided-chip no-drag"
          aria-pressed={c.value === value}
          aria-disabled={busy || c.disabled !== undefined ? true : undefined}
          title={c.disabled}
          data-choice={c.value}
          onClick={() => {
            if (!busy && c.disabled === undefined) onChange(c.value);
          }}
        >
          <span>{c.label}</span>
          {c.sub !== undefined && <span className="guided-chip-sub">{c.sub}</span>}
        </button>
      ))}
    </div>
  );
}

export function ErrorLine({ text }: { text: string }) {
  return text === "" ? null : (
    <p role="alert" className="guided-error" data-ws="guided-error">
      {text}
    </p>
  );
}

export { Note } from "../components/Note";
