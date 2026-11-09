import { useEffect, useMemo, useRef, useState, type ComponentType, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { ChevronDown, ChevronUp, Eraser } from "lucide-react";
import { Button, IconButton, Panel, Segmented } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { SplitHandle } from "../components/SplitHandle";
import { useStore, type DrawerTab } from "../state/store";
import { useWorkspace } from "../workspace/store";
import ConsoleView from "./ConsoleView";
import { SETUP_DOT, setupReadout } from "./setupStatus";
import Residuals from "./Residuals";
import JobLogView from "./JobLogView";
import { findMeshNodes, readResult } from "./meshPanelModel";
import { MESH_DOT, meshReadout } from "./meshReadout";
import { readCase } from "../guided/guidedTree";
import { clearJobLog, setLogView } from "./runControl";
import { RUN_DOT, liveLatest, runReadout, type LogView } from "./runPanelModel";
import "./runPanel.css";

const TAB_LABELS: Record<DrawerTab, string> = {
  console: "Console",
  log: "Log",
  residuals: "Residuals",
  reports: "Reports",
};
const ALL_TABS: readonly DrawerTab[] = ["console", "log", "residuals", "reports"];

const STRIP = 36;
const KEY_STEP = 16;

/** The output tabs a placement shows, in order (`tabs` absent: all four); unknown tab ids are skipped. */
export function outputTabs(tabs: readonly string[] | undefined): DrawerTab[] {
  if (!tabs) return [...ALL_TABS];
  const known = tabs.filter((t): t is DrawerTab => (ALL_TABS as readonly string[]).includes(t));
  return known.length ? known : [...ALL_TABS];
}

/** The output entry's `tabs`, wherever the workspace placed the panel. */
function useOutputTabs(): DrawerTab[] {
  const tabs = useWorkspace((s) => {
    const layout = s.document.layout;
    for (const slot of [layout.bottom, layout.left, layout.right, layout.float]) {
      const entry = slot.panels.find((p) => p.id === "output");
      if (entry) return entry.tabs;
    }
    return undefined;
  });
  return outputTabs(tabs);
}

/** The tab the output panel shows: the store's, unless the placement leaves that one out. */
function shownTab(tab: DrawerTab, tabs: readonly DrawerTab[]): DrawerTab {
  return tabs.includes(tab) ? tab : tabs[0];
}

function ErrorCount() {
  const errors = useStore((s) => s.consoleLines.filter((l) => l.kind === "error").length);
  if (errors === 0) return null;
  return (
    // A status dot and a count, not a filled badge (DESIGN.md section 3b).
    <span className="ml-1.5 inline-flex items-center gap-1 text-[11px] font-semibold" style={{ color: "var(--danger)" }}>
      <span className="dot failed" aria-hidden="true" />
      <span aria-hidden="true">{errors}</span>
      <span className="sr-only">
        {errors} error{errors === 1 ? "" : "s"}
      </span>
    </span>
  );
}

function TabButton({ tab, selected, onPick }: { tab: DrawerTab; selected: boolean; onPick: (tab: DrawerTab) => void }) {
  return (
    <button type="button" role="tab" aria-selected={selected} className={`tab ${selected ? "active" : ""}`} onClick={() => onPick(tab)}>
      {TAB_LABELS[tab]}
      {tab === "console" && <ErrorCount />}
    </button>
  );
}

function ClearButton({ tab }: { tab: DrawerTab }) {
  const clearConsole = useStore((s) => s.clearConsole);
  const logView = useStore((s) => s.run.logView);
  const job = tab === "log" && logView === "job";
  return (
    <Tooltip label={job ? "Clear the job log" : tab === "log" ? "Clear the log" : "Clear the console"} side="top" describe={false}>
      <Button variant="ghost" className="!h-7 !gap-1.5 !px-2.5 text-[12px]" onClick={() => (job ? clearJobLog() : clearConsole(tab === "log" ? ["event"] : ["command", "result", "error", "info"]))}>
        <Eraser size={13} /> Clear
      </Button>
    </Tooltip>
  );
}

const LOG_VIEWS: { value: LogView; label: string }[] = [
  { value: "events", label: "Events" },
  { value: "job", label: "Job" },
];

/**
 * The Log tab (T-217): Events, the model's own events (what the tab always showed), or Job, the
 * solver's and the mesher's lines. The choice is the store's `run.logView`, so it survives a tab switch.
 */
function LogBody() {
  const view = useStore((s) => s.run.logView);
  return (
    <div className="flex h-full flex-col">
      <div className="log-views">
        <Segmented<LogView> label="Log view" value={view} options={LOG_VIEWS} onChange={setLogView} />
      </div>
      <div className="min-h-0 flex-1">{view === "job" ? <JobLogView /> : <ConsoleView eventsOnly />}</div>
    </div>
  );
}

/** What one output tab shows. */
export function OutputBody({ tab }: { tab: DrawerTab }) {
  if (tab === "console") return <ConsoleView eventsOnly={false} />;
  if (tab === "log") return <LogBody />;
  if (tab === "residuals") return <Residuals />;
  return <div className="muted grid h-full place-items-center">Reports appear here after the first run.</div>;
}

/**
 * The output panel outside the drawer (a side slot or a float card): a glass panel with its own tab
 * strip over the content. Choosing a tab here does not open the bottom drawer.
 */
/**
 * The set-up readiness readout (ADR-0022 §11.1, T-049): a small dot in the state's colour and one
 * muted line, at the right end of the drawer's strip. It is text, not a badge, and clicking it shows
 * the findings — the conditions list proper is T-048, so this reports the same thing the readout has,
 * which is the `next` sentence and the findings' suggestions.
 *
 * It renders nothing until the first `setup.check` answer, so a server that does not answer (or a
 * project that has not loaded) leaves the strip as it was rather than claiming "ready".
 */
function SetupStatusReadout() {
  const setup = useStore((s) => s.setup);
  // An imported case has no CAD set-up to check: its dictionaries are its set-up, so this readout
  // (bodies, materials, boundaries) would only tell a tutorial user to do things that do not apply.
  const isCase = useStore((s) => readCase(s.tree) !== null);
  const [open, setOpen] = useState(false);
  const readout = isCase ? null : setupReadout(setup);
  if (readout === null) return null;
  return (
    <>
      <button
        type="button"
        className="setup-readout"
        data-ws="setup-readout"
        aria-label={`Set-up status: ${readout.text}`}
        title={readout.detail}
        onClick={() => setOpen((was) => !was)}
      >
        <span className="setup-dot" style={{ background: SETUP_DOT[readout.state] }} aria-hidden="true" />
        <span className="muted">{readout.text}</span>
      </button>
      {open && setup !== null && (
        <div className="setup-popover glass" role="status">
          {setup.findings.length === 0 ? (
            <p className="muted m-0 text-[12px]">{readout.detail}</p>
          ) : (
            <ul className="m-0 list-none p-0 text-[12px]">
              {setup.findings.map((finding) => (
                <li key={finding.code} className="py-0.5">
                  {finding.suggestion ?? finding.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}

/**
 * The Mesh and Run readouts (T-217), beside the set-up one and in its shape: a 7 px dot in the state's
 * colour and one muted line, text and not a badge. Each renders nothing until it has something to
 * say. Clicking the mesh readout goes to the Mesh stage, the run readout to the Residuals tab.
 */
function MeshStatusReadout() {
  const tree = useStore((s) => s.tree);
  const live = useStore((s) => s.mesh);
  const setStage = useStore((s) => s.setStage);
  // A case's mesh is built by its own steps and reported by the Run readout ("Mesh · built"); the
  // CAD mesher's empty result here read "0 cells · quality not met".
  const readout = useMemo(() => (readCase(tree) !== null ? null : meshReadout(readResult(findMeshNodes(tree).result), live)), [tree, live]);
  if (readout === null) return null;
  return (
    <button type="button" className="setup-readout compact" data-ws="mesh-readout" aria-label={`Mesh status: ${readout.text}`} title={readout.detail} onClick={() => setStage("Mesh")}>
      <span className="setup-dot" style={{ background: MESH_DOT[readout.state] }} aria-hidden="true" />
      <span className="muted">{readout.text}</span>
    </button>
  );
}

function RunStatusReadout() {
  const live = useStore((s) => s.run);
  const isCase = useStore((s) => readCase(s.tree) !== null);
  const setDrawerTab = useStore((s) => s.setDrawerTab);
  const readout = useMemo(() => runReadout(live.runs[0] ?? null, live.check, liveLatest(live), isCase), [live, isCase]);
  if (readout === null) return null;
  return (
    <button type="button" className="setup-readout compact" data-ws="run-readout" aria-label={`Run status: ${readout.text}`} title={readout.detail} onClick={() => setDrawerTab("residuals")}>
      <span className="setup-dot" style={{ background: RUN_DOT[readout.state] }} aria-hidden="true" />
      <span className="muted">{readout.text}</span>
    </button>
  );
}

export function OutputPanel() {
  const tabs = useOutputTabs();
  const tab = shownTab(useStore((s) => s.drawerTab), tabs);
  const pick = (next: DrawerTab) => useStore.setState({ drawerTab: next });
  return (
    <Panel className="flex flex-col overflow-hidden">
      <div role="tablist" aria-label="Output" className="flex h-9 shrink-0 items-center border-b px-2" style={{ borderColor: "var(--panel-border)" }}>
        {tabs.map((t) => (
          <TabButton key={t} tab={t} selected={tab === t} onPick={pick} />
        ))}
        <div className="ml-auto flex items-center gap-1">{(tab === "console" || tab === "log") && <ClearButton tab={tab} />}</div>
      </div>
      <div role="tabpanel" aria-label={TAB_LABELS[tab]} className="min-h-0 flex-1">
        <OutputBody tab={tab} />
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------- the drawer
/** A panel of the bottom slot, resolved by the layout engine from the registry. */
export interface DrawerPanel {
  id: string;
  title: string;
  component: ComponentType;
  /** In a split: the panel's width in px; panels without one share the rest. */
  size?: number;
}

interface BottomDrawerProps {
  panels: readonly DrawerPanel[];
  mode: "tabs" | "split";
  /** Expanded height from the workspace (`layout.bottom.size`), clamped to the panels' limits. */
  height: number;
  min: number;
  max: number;
  /** A gesture ended (pointer up, a key, a double click): write the size back. */
  onCommit: (height: number) => void;
  onReset: () => void;
  /** A split handle between two panels was let go: panel index and its new width. */
  onCommitPanel: (index: number, width: number) => void;
}

/**
 * The bottom slot: a 36 px strip with tabs that expands to the slot's height, with a drag handle on
 * its top edge. With the output panel alone it is the output drawer it always was (its tabs are the
 * strip); other panels add a tab each, or in split mode sit side by side. The open state is the
 * project store's `drawerOpen`, which the layout engine keeps in step with `layout.bottom.collapsed`.
 */
export default function BottomDrawer({ panels, mode, height: stored, min, max, onCommit, onReset, onCommitPanel }: BottomDrawerProps) {
  const open = useStore((s) => s.drawerOpen);
  const toggle = useStore((s) => s.toggleDrawer);
  const drawerTab = useStore((s) => s.drawerTab);
  const setDrawerTab = useStore((s) => s.setDrawerTab);
  const activeId = useWorkspace((s) => s.activeTab.bottom);
  const setActiveTab = useWorkspace((s) => s.setActiveTab);
  const outputTabsShown = useOutputTabs();

  const hasOutput = panels.some((p) => p.id === "output");
  const active = panels.some((p) => p.id === activeId) ? (activeId as string) : (panels[0]?.id ?? "");
  const tab = shownTab(drawerTab, outputTabsShown);
  const split = mode === "split" && panels.length > 1;

  // Something chose an output tab (a toast's Console button, the palette): show the output panel.
  useEffect(() => {
    if (!hasOutput) return;
    return useStore.subscribe((state, previous) => {
      if (state.drawerTab !== previous.drawerTab) useWorkspace.getState().setActiveTab("bottom", "output");
    });
  }, [hasOutput]);

  // The height follows the pointer during a drag; the workspace gets it when the gesture ends.
  const [draft, setDraft] = useState<number | null>(null);
  const height = draft ?? stored;
  const drag = useRef<{ startY: number; startHeight: number; last: number } | null>(null);
  const clamp = (px: number) => Math.min(max, Math.max(min, Math.round(px)));

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { startY: e.clientY, startHeight: height, last: height };
    setDraft(height);
    document.body.style.cursor = "ns-resize";
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    d.last = clamp(d.startHeight + (d.startY - e.clientY));
    setDraft(d.last);
  };
  const endDrag = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    document.body.style.cursor = "";
    if (d.last !== d.startHeight) onCommit(d.last);
    setDraft(null);
  };
  const onHandleKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? KEY_STEP * 4 : KEY_STEP;
    if (e.key === "ArrowUp") onCommit(clamp(height + step));
    else if (e.key === "ArrowDown") onCommit(clamp(height - step));
    else if (e.key === "Home") onCommit(min);
    else if (e.key === "End") onCommit(max);
    else return;
    e.preventDefault();
  };

  // A drag interrupted by the drawer closing or the component going away must not leave the cursor behind.
  useEffect(() => {
    return () => {
      document.body.style.cursor = "";
    };
  }, []);

  const pickOutput = (next: DrawerTab) => {
    setActiveTab("bottom", "output");
    setDrawerTab(next);
  };
  const pickPanel = (id: string) => {
    setActiveTab("bottom", id);
    toggle(true);
  };

  const dragging = draft !== null;
  const outputActive = hasOutput && (split || active === "output");
  const clearable = open && outputActive && (tab === "console" || tab === "log");
  return (
    <section
      aria-label="Output drawer"
      className="relative z-20 shrink-0 border-t transition-[height] duration-[180ms] ease-out motion-reduce:transition-none"
      style={{
        background: "var(--panel-solid)",
        borderColor: "var(--panel-border)",
        height: open ? height : STRIP,
        maxHeight: open ? "calc(100vh - 168px)" : undefined, // the viewport keeps at least 120 px on a short window
        transitionDuration: dragging ? "0ms" : undefined,
      }}
      data-ws-slot="bottom"
    >
      {open && (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize the output drawer"
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={height}
          tabIndex={0}
          // The accent bar below is the focus and drag indication; the global ring would be a band across the window.
          style={{ outline: "none", boxShadow: "none" }}
          className="group no-drag absolute inset-x-0 -top-[3px] z-10 h-[7px] cursor-ns-resize touch-none"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onLostPointerCapture={endDrag}
          onDoubleClick={onReset}
          onKeyDown={onHandleKey}
        >
          <div
            className={`mx-auto mt-[2px] h-[3px] w-14 rounded-full transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none ${dragging ? "opacity-100" : "opacity-0"}`}
            style={{ background: "var(--accent)" }}
          />
        </div>
      )}
      <div role="tablist" aria-label="Output" className="flex h-9 items-center px-2">
        {panels.map((p) =>
          p.id === "output" ? (
            outputTabsShown.map((t) => <TabButton key={`output-${t}`} tab={t} selected={open && outputActive && tab === t} onPick={pickOutput} />)
          ) : (
            <button
              key={p.id}
              type="button"
              role="tab"
              aria-selected={open && (split || active === p.id)}
              className={`tab ${open && !split && active === p.id ? "active" : ""}`}
              onClick={() => pickPanel(p.id)}
              data-ws-panel-tab={p.id}
            >
              {p.title}
            </button>
          ),
        )}
        <div className="ml-auto flex items-center gap-1">
          <MeshStatusReadout />
          <RunStatusReadout />
          <SetupStatusReadout />
          {clearable && <ClearButton tab={tab} />}
          <IconButton label={open ? "Collapse the output drawer" : "Expand the output drawer"} aria-expanded={open} onClick={() => toggle()}>
            {open ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
          </IconButton>
        </div>
      </div>
      {split ? (
        <DrawerSplit panels={panels} open={open} tab={tab} onCommitPanel={onCommitPanel} />
      ) : (
        <>
          {open && outputActive && (
            <div role="tabpanel" className="h-[calc(100%-36px)]" data-ws-panel="output">
              <OutputBody tab={tab} />
            </div>
          )}
          {panels
            .filter((p) => p.id !== "output")
            .map((p) => {
              const Body = p.component;
              // Mounted while folded, so the panel keeps its state and its window listeners.
              return (
                <div key={p.id} role="tabpanel" aria-label={p.title} className="drawer-panel h-[calc(100%-36px)]" hidden={!open || active !== p.id} data-ws-panel={p.id}>
                  <Body />
                </div>
              );
            })}
        </>
      )}
    </section>
  );
}

/** Split mode: the panels side by side, with a handle between neighbours. */
function DrawerSplit({ panels, open, tab, onCommitPanel }: { panels: readonly DrawerPanel[]; open: boolean; tab: DrawerTab; onCommitPanel: (index: number, width: number) => void }) {
  const [drafts, setDrafts] = useState<Record<number, number>>({});
  const row = useRef<HTMLDivElement>(null);
  return (
    <div ref={row} className="drawer-split h-[calc(100%-36px)]" hidden={!open}>
      {panels.map((p, i) => {
        const Body = p.component;
        const width = drafts[i] ?? p.size;
        return (
          <div key={p.id} className="drawer-cell" style={width !== undefined && i < panels.length - 1 ? { flex: `0 0 ${width}px` } : undefined} data-ws-panel={p.id}>
            {i > 0 && (
              <SplitHandle
                orientation="vertical"
                label={`Resize ${panels[i - 1].title}`}
                value={drafts[i - 1] ?? panels[i - 1].size ?? cellWidth(row.current, i - 1)}
                min={160}
                max={Math.max(160, (row.current?.clientWidth ?? 800) - 160)}
                onChange={(w) => setDrafts((d) => ({ ...d, [i - 1]: w }))}
                onCommit={(w) => {
                  setDrafts((d) => {
                    const { [i - 1]: _gone, ...rest } = d;
                    void _gone;
                    return rest;
                  });
                  onCommitPanel(i - 1, w);
                }}
              />
            )}
            {p.id === "output" ? open && <OutputBody tab={tab} /> : <Body />}
          </div>
        );
      })}
    </div>
  );
}

function cellWidth(row: HTMLElement | null, index: number): number {
  const cell = row?.children[index] as HTMLElement | undefined;
  return Math.round(cell?.getBoundingClientRect().width ?? 320);
}
