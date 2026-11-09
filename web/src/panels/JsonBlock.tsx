import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";

const COLLAPSED_LINES = 3;
/** Rendering tens of thousands of lines would stall the UI; the Log and the API have the whole result. */
const MAX_LINES = 1000;
const KEY_LINE = /^(\s*)("(?:[^"\\]|\\.)*")(:\s?)([\s\S]*)$/;

/** One line of pretty-printed JSON with the key set apart from its value. */
function JsonLine({ text }: { text: string }) {
  const match = KEY_LINE.exec(text);
  if (!match) return <>{text}</>;
  return (
    <>
      {match[1]}
      <span className="muted">{match[2]}</span>
      {match[3]}
      {match[4]}
    </>
  );
}

/** Pretty-printed JSON, cut to its first three lines until the reader opens it. */
export default function JsonBlock({ value }: { value: unknown }) {
  const lines = useMemo(() => JSON.stringify(value, null, 2).split("\n"), [value]);
  const [open, setOpen] = useState(false);
  const collapsible = lines.length > COLLAPSED_LINES;
  const shown = collapsible && !open ? lines.slice(0, COLLAPSED_LINES) : lines.slice(0, MAX_LINES);
  const omitted = open ? lines.length - MAX_LINES : 0;
  return (
    <div className="flex items-start gap-1">
      {collapsible ? (
        <button
          type="button"
          className="mt-[3px] grid h-4 w-4 shrink-0 place-items-center rounded text-[var(--text-faint)] hover:text-[var(--text)]"
          aria-expanded={open}
          aria-label={open ? "Collapse the result" : `Expand the result (${lines.length} lines)`}
          onClick={() => setOpen(!open)}
        >
          <ChevronRight size={13} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 120ms" }} />
        </button>
      ) : (
        <span className="w-4 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <pre className="m-0 font-[inherit] break-all whitespace-pre-wrap">
          {shown.map((text, i) => (
            <span key={i} className="block">
              <JsonLine text={text} />
            </span>
          ))}
        </pre>
        {omitted > 0 && <div className="muted">… {omitted.toLocaleString()} more lines not shown</div>}
        {collapsible && !open && (
          <button type="button" className="muted hover:!text-[var(--text)]" onClick={() => setOpen(true)}>
            … {lines.length - COLLAPSED_LINES} more lines
          </button>
        )}
      </div>
    </div>
  );
}
