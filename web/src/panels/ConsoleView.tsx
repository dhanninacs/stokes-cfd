import { memo, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { TerminalSquare } from "lucide-react";
import type { CommandSchema } from "../api/types";
import { useStore, type ConsoleLine } from "../state/store";
import { commonPrefix, completionsFor, parseCommandLine, type Completion } from "./commandLine";
import JsonBlock from "./JsonBlock";
import { isSensitive, readStrings, writeJson } from "./storage";

// ---------------------------------------------------------------------------------------------- history
const HISTORY_KEY = "console.history";
const HISTORY_LIMIT = 100;
let history: string[] | null = null; // module level: it must outlive the tab switches that unmount the console

function loadHistory(): string[] {
  history ??= readStrings(HISTORY_KEY, HISTORY_LIMIT);
  return history;
}

function remember(line: string): void {
  const all = loadHistory();
  if (all[all.length - 1] === line) return;
  history = [...all, line].slice(-HISTORY_LIMIT);
  writeJson(HISTORY_KEY, history);
}

// ---------------------------------------------------------------------------------------------- output
function parseJson(text: string): { value: unknown } | null {
  try {
    return { value: JSON.parse(text) as unknown };
  } catch {
    return null;
  }
}

const MAX_RESULT_CHARS = 200_000;
const time = (at: number) => new Date(at).toLocaleTimeString([], { hour12: false });

const Entry = memo(function Entry({ line }: { line: ConsoleLine }) {
  switch (line.kind) {
    case "command": {
      const space = line.text.indexOf(" ");
      const method = space < 0 ? line.text : line.text.slice(0, space);
      const args = space < 0 ? "" : line.text.slice(space + 1);
      return (
        <div className="break-all whitespace-pre-wrap">
          <span className="faint">› </span>
          <span className="font-semibold" style={{ color: "var(--accent-ink)" }}>
            {method}
          </span>
          {args && args !== "{}" && <span className="muted"> {args}</span>}
        </div>
      );
    }
    case "result": {
      // Pretty-printing a multi-megabyte result would stall the UI; show its start and size instead.
      if (line.text.length > MAX_RESULT_CHARS) {
        return (
          <div className="break-all whitespace-pre-wrap pl-5">
            {line.text.slice(0, 600)}
            <span className="muted"> … {line.text.length.toLocaleString()} characters in all</span>
          </div>
        );
      }
      const parsed = parseJson(line.text);
      if (parsed && typeof parsed.value === "object" && parsed.value !== null) return <JsonBlock value={parsed.value} />;
      return <div className="break-all whitespace-pre-wrap pl-5">{line.text}</div>;
    }
    case "error":
      return (
        <div
          role="alert"
          className="flex gap-2 rounded-[5px] px-2 py-0.5 break-all whitespace-pre-wrap"
          style={{ background: "var(--danger-soft)", color: "var(--danger)", boxShadow: "inset 2px 0 0 var(--danger)" }}
        >
          <span aria-hidden="true">✕</span>
          <span className="min-w-0">{line.text}</span>
        </div>
      );
    case "event": {
      const space = line.text.indexOf(" ");
      const name = space < 0 ? line.text : line.text.slice(0, space);
      const data = space < 0 ? "" : line.text.slice(space);
      return (
        <div className="muted break-all whitespace-pre-wrap">
          <span className="faint">{time(line.at)} </span>
          <span style={{ color: "var(--text)" }}>{name}</span>
          {data}
        </div>
      );
    }
    default:
      return <div className="muted break-all whitespace-pre-wrap pl-5">{line.text}</div>;
  }
});

// ---------------------------------------------------------------------------------------------- input
interface Cycle {
  head: string;
  items: Completion[];
  index: number;
  /** The line as the last Tab left it; typing anything else ends the cycle. */
  applied: string;
}

function CommandInput({ commands, onRun }: { commands: readonly CommandSchema[]; onRun: (line: string) => void }) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [cycle, setCycle] = useState<Cycle | null>(null);
  const browsing = useRef<{ index: number; draft: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const completions = useMemo(() => completionsFor(value, commands), [value, commands]);
  const cycling = cycle !== null && cycle.applied === value ? cycle : null;
  const suggestions = useMemo(() => {
    if (cycling) return cycling.items;
    const { token, items } = completions;
    if (value === "" || (items.length === 1 && items[0].value === token)) return [];
    return items;
  }, [cycling, completions, value]);
  const showPopup = focused && !dismissed && suggestions.length > 0;

  const edit = (text: string) => {
    setValue(text);
    setDismissed(false);
    setCycle(null);
    browsing.current = null;
  };

  const complete = () => {
    if (cycling) {
      const index = (cycling.index + 1) % cycling.items.length;
      const next = cycling.head + cycling.items[index].value;
      setValue(next);
      setCycle({ ...cycling, index, applied: next });
      return;
    }
    const { head, token, items } = completions;
    if (items.length === 0) return;
    const isCommand = head.trim() === "";
    if (items.length === 1) {
      setValue(head + items[0].value + (isCommand ? " " : ""));
      return;
    }
    const shared = commonPrefix(items.map((i) => i.value));
    if (shared.length > token.length) {
      setValue(head + shared);
      return;
    }
    const next = head + items[0].value; // nothing left to narrow: step through the candidates
    setValue(next);
    setCycle({ head, items, index: 0, applied: next });
    setDismissed(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // Tab completes only when there is something to complete; on an empty line it moves focus on as usual.
    if (e.key === "Tab" && !e.shiftKey && (cycling || value.trim() !== "")) {
      e.preventDefault();
      complete();
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      const all = loadHistory();
      if (all.length === 0) return;
      e.preventDefault();
      const b = browsing.current;
      if (e.key === "ArrowUp") {
        const index = b ? Math.max(0, b.index - 1) : all.length - 1;
        browsing.current = { index, draft: b ? b.draft : value };
        setValue(all[index]);
      } else if (b) {
        if (b.index + 1 >= all.length) {
          browsing.current = null;
          setValue(b.draft);
        } else {
          browsing.current = { ...b, index: b.index + 1 };
          setValue(all[b.index + 1]);
        }
      }
      setCycle(null);
    } else if (e.key === "Escape" && showPopup) {
      e.preventDefault();
      setDismissed(true);
    }
  };

  const choose = (item: Completion) => {
    const head = cycling?.head ?? completions.head;
    const text = head + item.value + (head.trim() === "" ? " " : "");
    edit(text);
    inputRef.current?.focus();
  };

  return (
    <form
      className="relative flex items-center gap-2 border-t px-3 py-2"
      style={{ borderColor: "var(--panel-border)" }}
      onSubmit={(e) => {
        e.preventDefault();
        const line = value.trim();
        if (!line) return;
        onRun(line);
        edit("");
      }}
    >
      {showPopup && (
        <div
          role="listbox"
          aria-label="Completions"
          className="absolute right-3 bottom-full left-3 z-10 mb-1 max-h-40 overflow-y-auto rounded-[var(--radius-sm)] border p-1"
          style={{ background: "var(--menu-bg)", borderColor: "var(--panel-border-strong)", boxShadow: "var(--shadow-pop)" }}
        >
          {suggestions.slice(0, 40).map((item, i) => (
            <div
              key={item.value}
              role="option"
              aria-selected={cycling?.index === i}
              className="flex cursor-pointer items-baseline gap-3 rounded-[6px] px-2 py-1"
              style={{ background: cycling?.index === i ? "var(--accent-soft)" : "transparent" }}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(item)}
            >
              <span className="mono" style={{ color: cycling?.index === i ? "var(--accent-ink)" : "var(--text)" }}>
                {item.label}
              </span>
              {item.detail && <span className="muted min-w-0 flex-1 truncate text-[11.5px]">{item.detail}</span>}
            </div>
          ))}
        </div>
      )}
      <TerminalSquare size={14} className="muted shrink-0" aria-hidden="true" />
      <input
        ref={inputRef}
        className="mono field !h-8 flex-1"
        aria-label="Console command"
        placeholder='command key=value …   e.g.  parameter.set node=/physics/parameters/U_in name=value value="3 [m/s]"'
        value={value}
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        onChange={(e) => edit(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      />
    </form>
  );
}

// ---------------------------------------------------------------------------------------------- view
/** The Console tab (results, errors, a command line) or, with `eventsOnly`, the Log tab. */
export default function ConsoleView({ eventsOnly }: { eventsOnly: boolean }) {
  const lines = useStore((s) => s.consoleLines);
  const commands = useStore((s) => s.commands);
  const execute = useStore((s) => s.execute);
  const log = useStore((s) => s.log);
  const shown = useMemo(() => lines.filter((l) => (eventsOnly ? l.kind === "event" : l.kind !== "event")), [lines, eventsOnly]);

  // Follow new output, unless the reader has scrolled up to look at something.
  const scroller = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [shown]);

  const run = (line: string) => {
    const parsed = parseCommandLine(line, commands);
    if (!parsed) return;
    follow.current = true;
    if ("error" in parsed) {
      remember(line);
      log("error", parsed.error);
      return;
    }
    // A line that carries a secret (a key, a token) is run but never kept in the history or in storage.
    if (!Object.keys(parsed.params).some(isSensitive)) remember(line);
    void execute(parsed.method, parsed.params).catch(() => undefined); // execute logs failures itself
  };

  return (
    <div className="flex h-full flex-col">
      <div
        ref={scroller}
        className="mono min-h-0 flex-1 overflow-y-auto px-3 py-2 leading-5"
        onScroll={(e) => {
          const el = e.currentTarget;
          follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
      >
        {shown.length === 0 && (
          <div className="muted">
            {eventsOnly ? "Model events appear here as they happen." : "Results and events appear here. Type a command below, e.g. parameter.list"}
          </div>
        )}
        {shown.map((l) => (
          <Entry key={l.id} line={l} />
        ))}
      </div>
      {!eventsOnly && <CommandInput commands={commands} onRun={run} />}
    </div>
  );
}
