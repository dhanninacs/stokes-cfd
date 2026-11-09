/**
 * The case card of the Properties panel: an imported OpenFOAM case (ADR-0029, T-313 and T-314).
 *
 * What the user needs to run a tutorial, in one place under the selected `foam_case` node:
 *
 * * **Status and recipe**: what the case is (`case.get`), the steps it runs, and the Allrun lines that
 *   are not run (`unparsed`, `refused`) with their reasons, so a `needs recipe` case says why.
 * * **Settings, file by file** (`case.files`, `case.settings`): every entry of a dictionary or field as it
 *   will be staged. Clicking a value edits it; Enter records it with `case.set` (one undo step) and the
 *   row then shows the copy's own value beside it with a Revert (`case.reset`). A known physical key shows
 *   its SI unit and takes a quantity in any unit. The server refuses what it must; the reason shows under
 *   the row.
 * * **Run** (`run.start case=`), its live state from the store's run list, and, once it has finished,
 *   **which written time** the Results show (`run.fields`, `run.time`).
 * * **Monitors**: the tables the case's own function objects wrote (`run.monitors`, `run.monitor`),
 *   each value column drawn against the first, the time.
 *
 * The node's import facts (`source`, `snapshot`, `dialect`, `solver`) are shown here as text and are not
 * offered as fields: they describe the copy, and changing one would only break the link to it.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronsUpDown, Play, RotateCcw } from "lucide-react";
import type { NodeSummary } from "../api/types";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import CaseFileEditor from "./CaseFileEditor";
import {
  depthOf,
  editableFiles,
  linePoints,
  recipeSummary,
  valueFor,
  visibleRows,
  type CaseFile,
  type MonitorTable,
  type SettingsRow,
} from "./caseCardModel";

/** The parameters the panel leaves out of its fields: the card shows them as text. */
const HIDDEN: ReadonlySet<string> = new Set(["source", "snapshot", "dialect", "solver"]);
const NOTHING: { card: ReactNode; hidden: ReadonlySet<string> } = { card: null, hidden: new Set() };

/** The card for a selected imported case, and the parameters the panel should not offer as fields. */
export function useCaseCard(node: NodeSummary | null): { card: ReactNode; hidden: ReadonlySet<string> } {
  if (node?.type !== "foam_case") return NOTHING;
  return { card: <CaseCard key={node.id} node={node} />, hidden: HIDDEN };
}

interface LineNote {
  line: number;
  text: string;
  why: string;
}

interface CaseInfo {
  status: string;
  solver: unknown;
  recipe: { steps?: { argv?: string[] }[]; unparsed?: LineNote[]; refused?: LineNote[] } | null;
  source: { kind?: string; tutorial?: string; path?: string } | null;
}

interface StartReply {
  run: string;
  state: string;
  adapted?: string[];
  skipped?: string[];
}

interface FieldsReply {
  time: number;
  times?: number[];
  surfaces: string;
}

const STATUS_WORD: Record<string, string> = {
  ready: "ready to run",
  needs_recipe: "needs recipe",
  unverified: "not checked against an install",
  missing: "copy missing from the project",
};

function solverText(solver: unknown): string {
  if (typeof solver === "string") return solver;
  if (solver && typeof solver === "object") return Object.entries(solver as Record<string, unknown>).map(([r, m]) => `${r}: ${String(m)}`).join(", ");
  return "unknown";
}

function rowKey(row: SettingsRow): string {
  return `${row.kind}:${row.path.join("\u0000")}`;
}

function CaseCard({ node }: { node: NodeSummary }) {
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const runs = useStore((s) => s.run.runs);
  // **The node's edits, as a key**: a `case.set` from anywhere (this card, the console, an undo) changes
  // the node's overrides, and the card reads the settings again when they change.
  const overridesKey = JSON.stringify([
    (node.params as Record<string, unknown>).overrides ?? [],
    (node.params as Record<string, unknown>).file_edits ?? [],
  ]);

  const [info, setInfo] = useState<CaseInfo | null>(null);
  const [files, setFiles] = useState<CaseFile[]>([]);
  const [file, setFile] = useState("");
  const [textOpen, setTextOpen] = useState(false);
  const [rows, setRows] = useState<SettingsRow[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [rowError, setRowError] = useState<{ key: string; text: string } | null>(null);
  const [started, setStarted] = useState<StartReply | null>(null);
  const [fields, setFields] = useState<FieldsReply | null>(null);
  const [monitors, setMonitors] = useState<string[]>([]);
  const [monitor, setMonitor] = useState("");
  const [table, setTable] = useState<MonitorTable | null>(null);

  useEffect(() => {
    void execute("case.get", { node: node.id }, { silent: true })
      .then((reply) => setInfo(reply as CaseInfo))
      .catch((err: unknown) => setProblem(err instanceof Error ? err.message : String(err)));
    void execute("case.files", { node: node.id }, { silent: true })
      .then((reply) => {
        const offered = editableFiles(Array.isArray(reply) ? (reply as CaseFile[]) : []);
        setFiles(offered);
        setFile((current) => current || offered[0]?.path || "");
      })
      .catch(() => setFiles([]));
  }, [execute, node.id, overridesKey]);

  const loadRows = useCallback(() => {
    if (!file) return;
    void execute("case.settings", { node: node.id, file }, { silent: true })
      .then((reply) => setRows(visibleRows(((reply as { rows?: SettingsRow[] }).rows ?? []) as SettingsRow[])))
      .catch((err: unknown) => {
        setRows([]);
        setProblem(err instanceof Error ? err.message : String(err));
      });
  }, [execute, node.id, file]);

  useEffect(() => {
    setProblem(null);
    loadRows();
  }, [loadRows, overridesKey]);

  const commit = async (row: SettingsRow) => {
    const key = rowKey(row);
    const value = valueFor(row, draft);
    if ("problem" in value) {
      setRowError({ key, text: value.problem });
      return;
    }
    try {
      await execute("case.set", { node: node.id, file, path: row.path, value: value.value }, { silent: true });
      setEditing(null);
      setRowError(null);
    } catch (err) {
      setRowError({ key, text: err instanceof Error ? err.message : String(err) });
    }
  };

  const revert = async (row: SettingsRow) => {
    try {
      await execute("case.reset", { node: node.id, file, path: row.path }, { silent: true });
    } catch (err) {
      setRowError({ key: rowKey(row), text: err instanceof Error ? err.message : String(err) });
    }
  };

  const run = async () => {
    try {
      const reply = (await execute("run.start", { case: node.id })) as StartReply;
      setStarted(reply);
      setFields(null);
      const changed = [...(reply.adapted ?? []), ...(reply.skipped ?? [])];
      pushToast(reply.state === "skipped" ? "error" : "ok", reply.state === "skipped" ? "No OpenFOAM install to run on: the run was recorded as skipped." : `Running ${node.name}${changed.length ? ` (${changed.length} change${changed.length === 1 ? "" : "s"} to the recipe, listed below)` : ""}`);
    } catch {
      /* execute has toasted the reason */
    }
  };

  const live = useMemo(() => (started ? runs.find((r) => r.id === started.run) ?? null : null), [runs, started]);
  const state = live?.state ?? started?.state ?? null;
  const finished = state === "done" || state === "done_unconverged";

  // The run's fields, once it has finished, and again whenever its record changes (a new time landed).
  useEffect(() => {
    if (!started || !finished) return;
    void execute("run.fields", { run: started.run }, { silent: true })
      .then((reply) => setFields(reply as FieldsReply))
      .catch(() => setFields(null));
  }, [execute, started, finished, live]);

  // The run's monitor tables: listed whenever its record changes (a running case writes more).
  useEffect(() => {
    if (!started || state === "skipped") return;
    void execute("run.monitors", { run: started.run }, { silent: true })
      .then((reply) => setMonitors(((reply as { monitors?: { name: string }[] }).monitors ?? []).map((m) => m.name)))
      .catch(() => setMonitors([]));
  }, [execute, started, state, live]);

  useEffect(() => {
    if (!started || !monitor) {
      setTable(null);
      return;
    }
    void execute("run.monitor", { run: started.run, name: monitor }, { silent: true })
      .then((reply) => setTable(reply as MonitorTable))
      .catch(() => setTable(null));
  }, [execute, started, monitor, live]);

  const showTime = async (time: number) => {
    if (!started) return;
    try {
      await execute("run.time", { run: started.run, time }, { silent: true });
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : String(err));
    }
  };

  const notes = [...(info?.recipe?.unparsed ?? []), ...(info?.recipe?.refused ?? [])];
  return (
    <div className="sc-card" role="group" aria-label="OpenFOAM case" data-ws="case-card">
      <div className="sc-table">
        <span className="muted">Case</span>
        <span>{info ? STATUS_WORD[info.status] ?? info.status : "reading…"}</span>
      </div>
      {info && (
        <div className="flex flex-col gap-1 text-[12px]">
          <div>
            <span className="muted">From </span>
            <span className="mono">{info.source?.tutorial ?? info.source?.path ?? "?"}</span>
          </div>
          <div>
            <span className="muted">Solver </span>
            <span className="mono">{solverText(info.solver)}</span>
          </div>
          <div>
            <span className="muted">Runs </span>
            <span className="mono">{recipeSummary(info.recipe)}</span>
          </div>
          {notes.length > 0 && (
            <details>
              <summary className="muted cursor-pointer select-none">
                {notes.length} Allrun line{notes.length === 1 ? " is" : "s are"} not run
              </summary>
              <ul className="m-0 mt-1 list-none p-0">
                {notes.map((n) => (
                  <li key={`${n.line}-${n.text}`} className="py-0.5">
                    <span className="mono">
                      {n.line}: {n.text}
                    </span>
                    <span className="muted"> - {n.why}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      <section className="sc-sec" aria-label="Run">
        <div className="sc-head">
          <span className="section-title">Run</span>
          <span className="sc-count muted">{state ? state.replaceAll("_", " ") : ""}</span>
          <Button className="ws-small sc-add" onClick={() => void run()} disabled={state === "running" || state === "queued"}>
            <Play size={13} /> Run case
          </Button>
        </div>
        {live?.step && state === "running" && <p className="sc-empty muted">{live.step}</p>}
        {started && [...(started.adapted ?? []), ...(started.skipped ?? [])].length > 0 && (
          <ul className="m-0 list-none p-0 text-[11.5px]">
            {[...(started.adapted ?? []), ...(started.skipped ?? [])].map((line) => (
              <li key={line} className="muted py-0.5">
                {line}
              </li>
            ))}
          </ul>
        )}
        {finished && fields && fields.surfaces === "ok" && (fields.times?.length ?? 0) > 1 && (
          <label className="flex items-center gap-2 text-[12px]">
            <span className="muted">Results at</span>
            <span className="select-wrap">
              <select className="field" value={String(fields.time)} onChange={(e) => void showTime(Number(e.target.value))} aria-label="Results time">
                {(fields.times ?? []).map((t) => (
                  <option key={t} value={String(t)}>
                    {t} s
                  </option>
                ))}
              </select>
              <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
            </span>
          </label>
        )}
        {finished && fields && fields.surfaces !== "ok" && <p className="sc-empty muted">The run wrote no fields to show.</p>}
      </section>

      {monitors.length > 0 && (
        <section className="sc-sec" aria-label="Monitors">
          <div className="sc-head">
            <span className="section-title">Monitors</span>
            <span className="select-wrap ml-auto">
              <select className="field" value={monitor} onChange={(e) => setMonitor(e.target.value)} aria-label="Monitor">
                <option value="">Choose a table…</option>
                {monitors.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
            </span>
          </div>
          {table && table.columns.length > 1 && (
            <ul className="m-0 list-none p-0 text-[11.5px]">
              {table.columns.slice(1, 7).map((name) => {
                const y = table.values[name] ?? [];
                const last = y.length ? y[y.length - 1] : null;
                return (
                  <li key={name} className="flex items-center gap-2 py-0.5">
                    <span className="mono w-[96px] shrink-0 truncate" title={name}>
                      {name}
                    </span>
                    <svg width="140" height="28" viewBox="-1 -1 142 30" aria-label={`${name} against ${table.columns[0]}`} role="img">
                      <polyline fill="none" stroke="var(--accent)" strokeWidth="1.2" points={linePoints(table.values[table.columns[0]] ?? [], y, 140, 28)} />
                    </svg>
                    <span className="mono muted">{last === null ? "" : last.toPrecision(4)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      <section className="sc-sec" aria-label="Settings">
        <div className="sc-head">
          <span className="section-title">Settings</span>
          <span className="select-wrap ml-auto">
            <select className="field" value={file} onChange={(e) => setFile(e.target.value)} aria-label="Case file">
              {files.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.path}
                </option>
              ))}
            </select>
            <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
          </span>
          <Button onClick={() => setTextOpen(true)} disabled={file === ""} data-ws="case-file-edit" title="Edit the text of this file">
            Edit text
          </Button>
        </div>
        {textOpen && <CaseFileEditor node={node.id} file={file} onClose={() => setTextOpen(false)} />}
        {problem && (
          <p className="text-[12px]" style={{ color: "var(--danger)" }} role="alert">
            {problem}
          </p>
        )}
        {rows === null && <p className="sc-empty muted">Reading…</p>}
        {rows !== null && rows.length === 0 && !problem && <p className="sc-empty muted">Nothing to edit in this file.</p>}
        {rows !== null && rows.length > 0 && (
          <ul className="m-0 max-h-[360px] list-none overflow-y-auto p-0 text-[12px]" aria-label={`Settings of ${file}`}>
            {rows.map((row, index) => {
              const key = rowKey(row);
              const indent = { paddingLeft: `${depthOf(row) * 12}px` };
              const name = row.path[row.path.length - 1] ?? "";
              if (row.kind !== "entry") {
                return (
                  <li key={`${key}-${index}`} className="muted py-0.5" style={indent}>
                    {row.kind === "block" ? name : <span className="mono">{row.text}</span>}
                  </li>
                );
              }
              const open = editing === key;
              return (
                <li key={`${key}-${index}`} className="py-0.5" style={indent}>
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 shrink-0 truncate" title={row.path.join(" / ")}>
                      {row.overridden && <span aria-label="edited" title="Edited" style={{ color: "var(--accent)" }}>● </span>}
                      {name}
                    </span>
                    {open ? (
                      <input
                        className="field mono min-w-0 flex-1"
                        value={draft}
                        autoFocus
                        aria-label={`New value for ${name}`}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void commit(row);
                          if (e.key === "Escape") {
                            setEditing(null);
                            setRowError(null);
                          }
                        }}
                        onBlur={() => setEditing((current) => (current === key ? null : current))}
                      />
                    ) : (
                      <button
                        type="button"
                        className="mono min-w-0 flex-1 truncate text-left no-drag"
                        title={row.truncated ? "Long value: shown cut short" : `Edit ${name}`}
                        disabled={row.truncated}
                        onClick={() => {
                          setEditing(key);
                          setDraft(row.text);
                          setRowError(null);
                        }}
                      >
                        {row.text}
                        {row.unit && <span className="muted"> {row.unit}</span>}
                      </button>
                    )}
                    {row.overridden && !open && (
                      <button type="button" className="muted shrink-0 no-drag" title={`Revert to ${row.original ?? "the copy's value"}`} aria-label={`Revert ${name}`} onClick={() => void revert(row)}>
                        <RotateCcw size={12} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                  {row.overridden && row.original != null && !open && <div className="muted pl-3 text-[11px]">was {row.original}</div>}
                  {rowError?.key === key && (
                    <div className="text-[11.5px]" style={{ color: "var(--danger)" }} role="alert">
                      {rowError.text}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
