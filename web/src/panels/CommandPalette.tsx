import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CornerDownLeft, Search } from "lucide-react";
import type { CommandSchema } from "../api/types";
import { useStore } from "../state/store";
import { Kbd } from "../components/ui";
import { ResetDialog } from "../workspace/Customise";
import { useWorkspace } from "../workspace/store";
import { rankCommands } from "./fuzzy";
import { commandItems, uiItems, type PaletteItem, type UiItem } from "./paletteItems";
import { buildParams, initialValues, ParamField, rememberValues, type FormValues } from "./PaletteForm";
import { readStrings, writeJson } from "./storage";

// ---------------------------------------------------------------------------------------------- storage
const RECENT_KEY = "palette.recent";
const RECENT_LIMIT = 5;

// ---------------------------------------------------------------------------------------------- list
type Entry =
  | { kind: "header"; title: string }
  | { kind: "item"; item: PaletteItem; nameIndices: number[]; descriptionIndices: number[]; flat: number };

/** How a matched run of letters is drawn: the name in the accent, the description in strong ink. */
type HighlightTone = "name" | "description";

/**
 * `text` with the matched letters picked out, but only the runs of two or more letters in a row. A
 * lone matched letter is noise (the "t" in "types" and the "e" in "Registered" for a query like
 * "ste"); a run of letters is what the eye reads as a match.
 */
function Highlight({ text, indices, tone }: { text: string; indices: number[]; tone: HighlightTone }) {
  const hit = new Set(indices);
  const runs: [number, number][] = []; // [start, end) of each run of matched letters
  for (let i = 0; i < text.length; ) {
    if (!hit.has(i)) {
      i++;
      continue;
    }
    let end = i + 1;
    while (hit.has(end)) end++;
    if (end - i >= 2) runs.push([i, end]);
    i = end;
  }
  if (runs.length === 0) return <>{text}</>;
  const parts: ReactNode[] = [];
  let at = 0;
  for (const [start, end] of runs) {
    if (start > at) parts.push(text.slice(at, start));
    parts.push(
      <span key={start} className="font-semibold" style={{ color: tone === "name" ? "var(--accent-ink)" : "var(--text)" }}>
        {text.slice(start, end)}
      </span>,
    );
    at = end;
  }
  if (at < text.length) parts.push(text.slice(at));
  return <>{parts}</>;
}

/** The sections of the list while nothing is typed: the API commands first, as the palette always was. */
const SECTIONS: { title: string; has(item: PaletteItem): boolean }[] = [
  { title: "Commands", has: (item) => item.kind === "command" },
  { title: "Actions", has: (item) => item.kind === "ui" && item.section === "actions" },
  { title: "Layout", has: (item) => item.kind === "ui" && item.section === "layout" },
];

function buildEntries(items: readonly PaletteItem[], query: string, recent: readonly string[]): Entry[] {
  const entries: Entry[] = [];
  let flat = 0;
  const row = (item: PaletteItem, nameIndices: number[] = [], descriptionIndices: number[] = []): Entry => ({
    kind: "item",
    item,
    nameIndices,
    descriptionIndices,
    flat: flat++,
  });
  if (query.trim()) {
    for (const r of rankCommands(items, query, recent, (item) => item.key)) entries.push(row(r.command, r.nameIndices, r.descriptionIndices));
    return entries;
  }
  const recents = recent.map((key) => items.find((item) => item.key === key)).filter((item): item is PaletteItem => item !== undefined);
  if (recents.length > 0) {
    entries.push({ kind: "header", title: "Recent" });
    for (const item of recents) entries.push(row(item));
  }
  for (const section of SECTIONS) {
    const rest = items.filter((item) => section.has(item) && !recents.includes(item));
    if (rest.length === 0) continue;
    entries.push({ kind: "header", title: section.title });
    for (const item of rest) entries.push(row(item));
  }
  return entries;
}

// ---------------------------------------------------------------------------------------------- palette
/**
 * The palette (Ctrl K). It lists the API commands, the window's own actions with their current chords,
 * "Customise workspace" and "Reset workspace", and "Move <panel> to <place>" (paletteItems.ts). Reset asks
 * first, in the dialog of Customise mode, which the palette hosts so that it can open without the mode.
 */
export default function CommandPalette() {
  const [resetting, setResetting] = useState(false);
  const hasProject = useWorkspace((s) => s.files.project?.exists === true);
  const reset = useCallback(() => setResetting(true), []);
  return (
    <>
      <Palette onReset={reset} />
      <ResetDialog open={resetting} hasProject={hasProject} onClose={() => setResetting(false)} />
    </>
  );
}

function Palette({ onReset }: { onReset: () => void }) {
  const open = useStore((s) => s.paletteOpen);
  const setOpen = useStore((s) => s.setPalette);
  const commands = useStore((s) => s.commands);
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const setDrawerTab = useStore((s) => s.setDrawerTab);
  // What the window's entries show comes from these: the chords, and the places a panel can still go to.
  const shortcuts = useWorkspace((s) => s.document.shortcuts);
  const layout = useWorkspace((s) => s.document.layout);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [picked, setPicked] = useState<CommandSchema | null>(null);
  const [values, setValues] = useState<FormValues>({});
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [recent, setRecent] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    setQuery("");
    setCursor(0);
    setPicked(null);
    setValues({});
    setTouched(new Set());
    setErrors({});
    setRecent(readStrings(RECENT_KEY, RECENT_LIMIT));
    const focus = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => {
      window.clearTimeout(focus);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [open]);

  // Rebuilt each time the palette opens (an action may have gained or lost its handler since).
  const items = useMemo(() => [...commandItems(commands), ...uiItems(onReset)], [commands, onReset, open, shortcuts, layout]);
  const entries = useMemo(() => buildEntries(items, query, recent), [items, query, recent]);
  const rows = useMemo(() => entries.filter((e): e is Extract<Entry, { kind: "item" }> => e.kind === "item"), [entries]);
  const active = Math.min(cursor, Math.max(rows.length - 1, 0));

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [active, entries]);

  if (!open) return null;

  const remember = (key: string) => {
    const next = [key, ...recent.filter((k) => k !== key)].slice(0, RECENT_LIMIT);
    writeJson(RECENT_KEY, next);
    setRecent(next);
  };

  const run = async (command: CommandSchema, params: Record<string, unknown>) => {
    remember(command.name);
    setOpen(false);
    setDrawerTab("console");
    try {
      await execute(command.name, params);
      pushToast("ok", `${command.name} done`);
    } catch {
      /* logged by execute */
    }
  };

  /** An entry of the window's own: it runs once the palette has closed and given the focus back, so what it opens keeps the focus. */
  const runUi = (item: UiItem) => {
    remember(item.key);
    setOpen(false);
    window.setTimeout(item.run, 0);
  };

  const openForm = (command: CommandSchema) => {
    setPicked(command);
    setValues(initialValues(command));
    setTouched(new Set());
    setErrors({});
  };

  const choose = (item: PaletteItem, withOptions = false) => {
    if (item.kind === "ui") return runUi(item);
    const command = item.command;
    const hasParams = Object.keys(command.input_schema.properties).length > 0;
    if (command.input_schema.required.length > 0 || (withOptions && hasParams)) openForm(command);
    else void run(command, {});
  };

  const submitForm = () => {
    if (!picked) return;
    const built = buildParams(picked, values, touched);
    if ("errors" in built) {
      setErrors(built.errors);
      return;
    }
    rememberValues(picked.name, values);
    void run(picked, built.params);
  };

  const onSearchKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return; // an input method is still composing
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor(Math.min(active + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor(Math.max(active - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (rows[active]) choose(rows[active].item, e.shiftKey);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  // Esc steps back from the form to the list, then closes; Tab stays inside the dialog.
  const onDialogKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape" && picked) {
      e.preventDefault();
      setPicked(null);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    } else if (e.key === "Tab") {
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>('input, button, select, textarea, [tabindex]:not([tabindex="-1"])') ?? [])].filter(
        (el) => !(el as HTMLInputElement).disabled,
      );
      if (focusable.length === 0) return e.preventDefault();
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  const fields = picked ? Object.entries(picked.input_schema.properties) : [];
  return createPortal(
    <div
      className="scrim"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="dialog glass !w-[min(560px,calc(100vw-32px))] !p-0 overflow-hidden"
        onKeyDown={onDialogKey}
      >
        {!picked ? (
          <>
            <div className="flex items-center gap-2 border-b px-3" style={{ borderColor: "var(--panel-border)" }}>
              <Search size={15} className="muted" aria-hidden="true" />
              <input
                ref={inputRef}
                className="h-11 flex-1 bg-transparent text-[14px] outline-none"
                placeholder="Run a command…"
                aria-label="Search commands"
                aria-controls="palette-list"
                aria-activedescendant={rows[active] ? `palette-${rows[active].item.key}` : undefined}
                autoComplete="off"
                spellCheck={false}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setCursor(0);
                }}
                onKeyDown={onSearchKey}
              />
              <Kbd>Esc</Kbd>
            </div>
            <div ref={listRef} id="palette-list" role="listbox" className="max-h-[360px] overflow-y-auto py-1">
              {entries.map((entry) =>
                entry.kind === "header" ? (
                  <div key={`h-${entry.title}`} role="presentation" className="section-title px-4 pt-2 pb-1">
                    {entry.title}
                  </div>
                ) : (
                  <div
                    key={entry.item.key}
                    id={`palette-${entry.item.key}`}
                    role="option"
                    aria-selected={entry.flat === active}
                    data-active={entry.flat === active}
                    className="mx-1 flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2"
                    style={{ background: entry.flat === active ? "var(--accent-soft)" : "transparent" }}
                    onMouseMove={() => setCursor(entry.flat)}
                    onClick={() => choose(entry.item)}
                  >
                    {/* A command's name is an identifier and never shortens; a window entry has a title, which gives way to the end only after its description has. */}
                    <span className={entry.item.kind === "command" ? "mono shrink-0" : "min-w-0 truncate"} style={{ color: entry.flat === active ? "var(--accent-ink)" : "var(--text)" }}>
                      <Highlight text={entry.item.name} indices={entry.nameIndices} tone="name" />
                    </span>
                    <span className="muted min-w-0 flex-1 truncate text-[12px]">
                      {/* The description is picked out only when the name had no match: both at once is a scatter of letters. */}
                      <Highlight text={entry.item.description} indices={entry.nameIndices.length === 0 ? entry.descriptionIndices : []} tone="description" />
                    </span>
                    {entry.item.kind === "command" && entry.item.command.feature && <span className="kbd shrink-0">pro</span>}
                    {entry.item.kind === "ui" && entry.item.chord && <span className="kbd shrink-0">{entry.item.chord}</span>}
                    {entry.flat === active && <CornerDownLeft size={13} className="muted shrink-0" aria-hidden="true" />}
                  </div>
                ),
              )}
              {rows.length === 0 && <div className="muted px-4 py-6 text-center">No entry matches “{query.trim()}”.</div>}
              {commands.length === 0 && !query.trim() && <div className="muted px-4 py-2 text-[12px]">Waiting for the server…</div>}
            </div>
            <div className="muted flex items-center gap-3 border-t px-4 py-1.5 text-[11.5px]" style={{ borderColor: "var(--panel-border)" }}>
              <span className="flex items-center gap-1">
                <Kbd>↑</Kbd>
                <Kbd>↓</Kbd> move
              </span>
              <span className="flex items-center gap-1">
                <Kbd>Enter</Kbd> run
              </span>
              <span className="flex items-center gap-1">
                <Kbd>Shift Enter</Kbd> options
              </span>
            </div>
          </>
        ) : (
          <form
            className="space-y-3 p-4"
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              submitForm();
            }}
          >
            <div>
              <div className="mono text-[14px]" style={{ color: "var(--accent-ink)" }}>
                {picked.name}
                {picked.feature && <span className="kbd ml-2">pro</span>}
              </div>
              <div className="muted text-[12.5px]">{picked.description}</div>
            </div>
            {fields.map(([name, schema], i) => (
              <ParamField
                key={`${picked.name}:${name}`}
                name={name}
                schema={schema}
                required={picked.input_schema.required.includes(name)}
                value={values[name] ?? ""}
                error={errors[name]}
                autoFocus={i === 0}
                onChange={(value) => {
                  setValues((v) => ({ ...v, [name]: value }));
                  setTouched((t) => new Set(t).add(name));
                  setErrors((er) => {
                    if (!(name in er)) return er;
                    const { [name]: _dropped, ...rest } = er;
                    return rest;
                  });
                }}
              />
            ))}
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setPicked(null);
                  window.setTimeout(() => inputRef.current?.focus(), 0);
                }}
              >
                Back
              </button>
              <button type="submit" className="btn primary">
                Run
              </button>
            </div>
          </form>
        )}
      </div>
    </div>,
    document.body,
  );
}
