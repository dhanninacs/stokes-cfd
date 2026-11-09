import { useCallback, useEffect, useId, useState } from "react";
import { BookOpen, ChevronsUpDown, FolderOpen } from "lucide-react";
import { host } from "./host";
import {
  SHOWN,
  categoriesOf,
  loadCatalogue,
  openTutorial,
  setRoot,
  statusTone,
  statusWord,
  totalOf,
  type CatalogueState,
  type Execute,
  type OpenOutcome,
} from "./tutorialCatalogue";

/**
 * The OpenFOAM tutorials the user's install ships, as a preview on the Start screen (ADR-0029).
 *
 * **A row opens its tutorial** in a new project of its own (`tutorial.open`), which replaces this
 * screen. A case the install cannot run still opens, so it can be read and edited; the status each
 * row shows is the server's judgement against the install's probe, shown as it came, including
 * `unverified` when there was no probe.
 *
 * **The folder comes from the native picker, never from typed text**, and the server validates it
 * (`tutorial.set_root`) before it is stored in the user settings.
 */
export default function TutorialsCard({
  execute,
  connected,
  className,
  onOpened,
}: {
  execute: Execute;
  connected: boolean;
  className: string;
  /** Told how an open went, so the screen can toast it. */
  onOpened: (outcome: OpenOutcome) => void;
}) {
  const [state, setState] = useState<CatalogueState>({ kind: "loading" });
  /** The tutorial being opened, so its row says so and no second open starts meanwhile. */
  const [opening, setOpening] = useState<string | null>(null);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const textId = useId();
  const categoryId = useId();

  const load = useCallback(
    async (filter: { category?: string; text?: string }) => {
      const next = await loadCatalogue(execute, filter);
      // The select lists every category, so it is filled only from an unfiltered reply.
      if (next.kind === "listed" && !filter.category && !filter.text?.trim()) setCategories(categoriesOf(next.reply));
      setState(next);
    },
    [execute],
  );

  // Typing waits a quarter second: each call walks the tutorial folder on the server.
  useEffect(() => {
    if (!connected) return;
    const timer = window.setTimeout(() => void load({ category, text }), text ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [connected, load, category, text]);

  const choose = async () => {
    setProblem(null);
    const start = state.kind === "listed" ? state.reply.root : "";
    const picked = await host.pickFolder("Choose the OpenFOAM tutorials folder", start);
    if (!picked) return;
    const refused = await setRoot(execute, picked);
    if (refused) {
      setProblem(refused);
      return;
    }
    setCategory("");
    setText("");
    setCategories([]);
    setState({ kind: "loading" });
    await load({});
  };

  const open = async (id: string) => {
    setProblem(null);
    setOpening(id);
    const outcome = await openTutorial(execute, id);
    setOpening(null);
    if (!outcome.ok) setProblem(outcome.text);
    onOpened(outcome);
  };

  const chooseButton = (label: string) => (
    <button
      type="button"
      className="btn no-drag"
      data-ws="start-tutorials-root"
      disabled={!connected || !host.canPickFolders}
      title={host.canPickFolders ? undefined : "The folder picker needs the desktop app."}
      onClick={() => void choose()}
    >
      <FolderOpen size={13} aria-hidden="true" />
      <span className="ml-1">{label}</span>
    </button>
  );

  return (
    <div className={`${className} min-w-0`} data-ws="start-tutorials">
      <h2 className="flex items-center gap-2 text-[13.5px] font-semibold">
        <BookOpen size={14} aria-hidden="true" /> Tutorials
      </h2>

      {state.kind === "loading" && <p className="muted text-[12px]">{connected ? "Reading the tutorial folder…" : "Waiting for the server."}</p>}

      {state.kind === "none" && (
        <>
          <p className="muted text-[12px]" title="Choose the tutorials folder your OpenFOAM install ships.">No tutorial folder yet.</p>
          <div>{chooseButton("Choose the OpenFOAM tutorials folder…")}</div>
        </>
      )}

      {state.kind === "error" && (
        <>
          <p className="text-[12px]" style={{ color: "var(--danger)" }} role="alert">
            {state.message}
          </p>
          <div>{chooseButton("Choose the OpenFOAM tutorials folder…")}</div>
        </>
      )}

      {state.kind === "listed" && (
        <>
          <p className="muted text-[12px] tabular-nums">
            {totalOf(state.reply.totals)} cases, {state.reply.totals.ready ?? 0} ready on this install
            {!state.reply.probed && " (the install was not probed, so none is verified)"}
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <label htmlFor={textId} className="flex min-w-[160px] flex-1 flex-col text-[11.5px]">
              <span className="muted">Filter</span>
              <input
                id={textId}
                className="field mt-1"
                type="search"
                value={text}
                maxLength={120}
                placeholder="Name, solver or status"
                onChange={(e) => setText(e.target.value)}
              />
            </label>
            {categories.length > 0 && (
              <label htmlFor={categoryId} className="flex min-w-[160px] flex-1 flex-col text-[11.5px]">
                <span className="muted">Category</span>
                <div className="select-wrap mt-1">
                  <select id={categoryId} className="field" value={category} onChange={(e) => setCategory(e.target.value)}>
                    <option value="">All categories</option>
                    {categories.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                  <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
                </div>
              </label>
            )}
          </div>
          <ul className="m-0 max-h-[260px] list-none overflow-y-auto overflow-x-hidden p-0" aria-label="Tutorial cases" tabIndex={0}>
            {state.reply.cases.slice(0, SHOWN).map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className="flex w-full min-w-0 items-center gap-2 py-0.5 text-left text-[12.5px] no-drag"
                  data-ws="start-tutorial"
                  disabled={!connected || opening !== null}
                  title={`Open ${c.id} in a new project${c.why ? ` - ${c.why}` : ""}`}
                  onClick={() => void open(c.id)}
                >
                  <span className="min-w-0 flex-1 truncate">{c.id.split("/").pop()}</span>
                  <span className="muted hidden shrink-0 truncate text-[11px] sm:inline">{c.category}</span>
                  <span className="muted flex shrink-0 items-center gap-1 text-[11px]">
                    <span
                      aria-hidden="true"
                      className="inline-block h-[7px] w-[7px] rounded-full"
                      style={{ background: statusTone(c.status) === "muted" ? "var(--text-muted)" : `var(--${statusTone(c.status)})` }}
                    />
                    {opening === c.id ? "opening…" : statusWord(c.status)}
                  </span>
                </button>
              </li>
            ))}
            {state.reply.cases.length === 0 && <li className="muted text-[12px]">No case matches.</li>}
          </ul>
          {state.reply.cases.length > SHOWN && (
            <p className="muted text-[11.5px] tabular-nums">
              Showing {SHOWN} of {state.reply.cases.length}; narrow the filter to see the rest.
            </p>
          )}
          <div>{chooseButton("Change folder…")}</div>
        </>
      )}

      {problem && (
        <p className="text-[12px]" style={{ color: "var(--danger)" }} role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}
