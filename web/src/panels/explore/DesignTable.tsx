/**
 * The Design Table panel (ADR-0016 point 5): one row per design point, a column for each variable and each
 * response in the project's display units, a status dot, and a toolbar that runs the table commands
 * (Generate, Update all, Update selected, Add point, Clear, Export CSV). Rows are selected with a click,
 * Shift-click (a range) and Ctrl-click (one more), or with the arrow keys; the selection is the store's,
 * so the plots show it too. Beyond 200 rows only the rows in view are drawn.
 *
 * The numbers are converted for display here (`format.ts`); the table itself holds SI. A failed point
 * shows its error on hover. Nothing in this file edits a model: every button is a `study.*` command.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type ReactNode, type UIEvent } from "react";
import { ChevronDown, Download, Eraser, FlaskConical, Play, Plus, SkipForward, Table2 } from "lucide-react";
import { Button, Panel, StatusDot } from "../../components/ui";
import { Menu, type MenuItemSpec } from "../../components/Menu";
import { Tooltip } from "../../components/Tooltip";
import { findNode, useStore } from "../../state/store";
import { clearTable, exportTable, generateTable, updateTable } from "./commands";
import type { DataColumn, Dataset } from "./dataset";
import { Empty } from "./Empty";
import { openAddPoint, openNewStudy } from "./forms";
import { formatCell, statusText } from "./format";
import { useElementSize } from "./plotKit";
import { useExplore, useExploreFeed } from "./store";
import { listStudies } from "./study";
import type { PointRow } from "./types";

const ROW_HEIGHT = 26;
/** Up to this many rows are all drawn; beyond it only the rows in view and a few around them. */
const WINDOW_FROM = 200;
const OVERSCAN = 8;
const COLUMN_MIN = 92;

function dotStatus(status: PointRow["status"]): string {
  return status === "done" ? "ok" : status === "failed" ? "failed" : status === "running" ? "run" : "new";
}

function cellText(column: DataColumn, position: number): string {
  const value = column.values[position];
  if (!Number.isFinite(value)) return "";
  return column.categories?.get(value) ?? formatCell(value);
}

interface RowProps {
  row: PointRow;
  position: number;
  selected: boolean;
  dataset: Dataset;
  onPick: (e: MouseEvent, row: PointRow) => void;
}

const Row = memo(function Row({ row, position, selected, dataset, onPick }: RowProps) {
  const failed = row.status === "failed";
  const dot = (
    <span className="dt-status" role="img" aria-label={failed && row.error ? `failed: ${row.error}` : row.status}>
      {row.status === "running" ? <span className="dot dt-run" aria-hidden="true" /> : <StatusDot status={dotStatus(row.status)} />}
    </span>
  );
  return (
    <div
      role="row"
      id={`dt-row-${row.index}`}
      aria-rowindex={position + 2}
      aria-selected={selected}
      className="dt-row"
      data-status={row.status}
      style={{ top: position * ROW_HEIGHT }}
      onMouseDown={(e) => {
        // A shift-click must not start a text selection.
        if (e.shiftKey) e.preventDefault();
      }}
      onClick={(e) => onPick(e, row)}
    >
      <span role="gridcell" className="dt-cell dt-index">
        {row.index}
      </span>
      {failed && row.error ? (
        <Tooltip label={row.error} side="top" describe={false}>
          {dot}
        </Tooltip>
      ) : (
        dot
      )}
      {dataset.inputs.map((c) => (
        <span key={c.name} role="gridcell" className="dt-cell dt-num">
          {cellText(c, position)}
        </span>
      ))}
      {dataset.outputs.map((c, i) => (
        <span key={c.name} role="gridcell" className={`dt-cell dt-num ${i === 0 ? "dt-split" : ""}`}>
          {cellText(c, position)}
        </span>
      ))}
    </div>
  );
});

function Head({ dataset }: { dataset: Dataset }) {
  const header = (c: DataColumn, split: boolean) => (
    <Tooltip key={c.name} label={c.name === c.title ? c.name : `${c.title} (${c.name})`} side="top" describe={false}>
      <span role="columnheader" className={`dt-cell dt-th ${split ? "dt-split" : ""}`}>
        <span className="dt-th-name">{c.title}</span>
        {c.unit && <span className="dt-th-unit">{c.unit}</span>}
      </span>
    </Tooltip>
  );
  return (
    <div role="row" aria-rowindex={1} className="dt-row dt-head">
      <span role="columnheader" className="dt-cell dt-index dt-th">
        #
      </span>
      <span role="columnheader" className="dt-status dt-th">
        <span className="sr-only">Status</span>
      </span>
      {dataset.inputs.map((c) => header(c, false))}
      {dataset.outputs.map((c, i) => header(c, i === 0))}
    </div>
  );
}

interface ToolProps {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  /** Why the button does nothing right now; the tooltip says it. */
  why?: string;
  title?: string;
}

function Tool({ label, icon, onClick, disabled, why, title }: ToolProps) {
  return (
    <Tooltip label={disabled && why ? why : (title ?? label)} side="top" describe={false}>
      <Button className="ws-small dt-tool" aria-disabled={disabled || undefined} onClick={() => !disabled && onClick()}>
        {icon}
        <span>{label}</span>
      </Button>
    </Tooltip>
  );
}

export default function DesignTable() {
  useExploreFeed();
  const tree = useStore((s) => s.tree);
  const hasProject = useStore((s) => s.project !== null);
  const select = useStore((s) => s.select);
  const studyId = useExplore((s) => s.studyId);
  const results = useExplore((s) => s.results);
  const dataset = useExplore((s) => s.dataset);
  const selected = useExplore((s) => s.selected);
  const updating = useExplore((s) => s.updating > 0);
  const error = useExplore((s) => s.error);

  const studies = useMemo(() => listStudies(tree), [tree]);
  const study = findNode(tree, studyId);
  const table = useRef<HTMLDivElement>(null); // the grid: it takes the keyboard
  const head = useRef<HTMLDivElement>(null); // the header row, which follows the horizontal scroll of the rows
  const scroller = useRef<HTMLDivElement>(null);
  const size = useElementSize(scroller);
  const [scrollTop, setScrollTop] = useState(0);
  const cursor = useRef<number | null>(null); // table position of the row the arrow keys move from

  const [menu, setMenu] = useState<"clear" | "study" | null>(null);
  const clearButton = useRef<HTMLButtonElement>(null);
  const studyButton = useRef<HTMLButtonElement>(null);

  const rows = dataset?.rows ?? [];
  const total = rows.length;
  const columns = (dataset?.inputs.length ?? 0) + (dataset?.outputs.length ?? 0);
  const counts = results?.counts;
  const running = updating || (counts?.running ?? 0) > 0 || results?.status === "running";

  // ------------------------------------------------------------------ selection
  const pick = useCallback((e: MouseEvent, row: PointRow) => {
    const explore = useExplore.getState();
    const more = e.ctrlKey || e.metaKey;
    if (e.shiftKey) explore.selectRange(row.index, more);
    else if (more) explore.toggle(row.index);
    else explore.setSelection([row.index], row.index);
    cursor.current = useExplore.getState().dataset?.position.get(row.index) ?? null;
    table.current?.focus({ preventScroll: true });
  }, []);

  const reveal = useCallback((position: number) => {
    const el = scroller.current;
    if (!el) return;
    const top = position * ROW_HEIGHT;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + ROW_HEIGHT > el.scrollTop + el.clientHeight) el.scrollTop = top + ROW_HEIGHT - el.clientHeight;
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!dataset || total === 0) return;
    const explore = useExplore.getState();
    const move = (to: number) => {
      const position = Math.max(0, Math.min(total - 1, to));
      const index = rows[position].index;
      cursor.current = position;
      if (e.shiftKey) explore.selectRange(index, false);
      else explore.setSelection([index], index);
      reveal(position);
    };
    const from = cursor.current ?? (explore.anchor !== null ? (dataset.position.get(explore.anchor) ?? -1) : -1);
    switch (e.key) {
      case "ArrowDown":
        move(from + 1);
        break;
      case "ArrowUp":
        move(from < 0 ? 0 : from - 1);
        break;
      case "Home":
        move(0);
        break;
      case "End":
        move(total - 1);
        break;
      case "PageDown":
        move(from + Math.max(1, Math.floor(size.height / ROW_HEIGHT) - 2));
        break;
      case "PageUp":
        move(from - Math.max(1, Math.floor(size.height / ROW_HEIGHT) - 2));
        break;
      case "a":
      case "A":
        if (!(e.ctrlKey || e.metaKey)) return;
        explore.setSelection(rows.map((r) => r.index), explore.anchor);
        break;
      case "Escape":
        if (explore.selected.size === 0) return; // the global Escape clears the tree selection instead
        explore.clearSelection();
        break;
      default:
        return;
    }
    e.preventDefault();
  };

  // ------------------------------------------------------------------ windowing
  const windowed = total > WINDOW_FROM;
  const start = windowed ? Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN) : 0;
  const end = windowed ? Math.min(total, Math.ceil((scrollTop + Math.max(size.height, 200)) / ROW_HEIGHT) + OVERSCAN) : total;

  // The scroll position drives which rows are drawn; one update per frame is enough.
  const frame = useRef(0);
  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (head.current) head.current.style.transform = `translateX(${-el.scrollLeft}px)`;
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      setScrollTop(el.scrollTop);
    });
  };
  useEffect(
    () => () => {
      if (frame.current) cancelAnimationFrame(frame.current);
    },
    [],
  );

  // ------------------------------------------------------------------ toolbar
  const selectedList = useMemo(() => [...selected], [selected]);
  const clearItems: MenuItemSpec[] = [
    { id: "reset-selected", label: "Reset selected to pending", disabled: selected.size === 0, onSelect: () => void clearTable(selectedList, false) },
    { id: "reset-all", label: "Reset every point to pending", onSelect: () => void clearTable(null, false) },
    { type: "separator", id: "sep" },
    { id: "remove-selected", label: "Remove selected points", disabled: selected.size === 0, onSelect: () => void clearTable(selectedList, true) },
    { id: "remove-all", label: "Remove every point", onSelect: () => void clearTable(null, true) },
  ];
  const studyItems: MenuItemSpec[] = studies.map((s) => ({ id: s.id, label: s.name, radio: true, checked: s.id === studyId, onSelect: () => select(s.id) }));

  const hasVariables = (dataset?.inputs.length ?? 0) > 0;
  // What "Update all" would evaluate: a point that is not done, or one done before a response was added.
  const needsUpdate = useMemo(() => {
    if (!dataset) return false;
    for (let i = 0; i < dataset.rows.length; i++) {
      if (dataset.rows[i].status !== "done") return true;
      for (const column of dataset.outputs) if (!Number.isFinite(column.values[i])) return true;
    }
    return false;
  }, [dataset]);

  // ------------------------------------------------------------------ body
  let body: ReactNode;
  if (!hasProject || (!study && studies.length === 0)) {
    body = (
      <Empty
        action={
          hasProject && (
            <Button className="ws-small" onClick={openNewStudy}>
              <Plus size={14} /> New study…
            </Button>
          )
        }
      >
        {hasProject ? "No study yet. Create one from the Explore stage toolbar." : "Open a project to explore it."}
      </Empty>
    );
  } else if (!results || !dataset) {
    body = <Empty>{error ?? "Loading the design table…"}</Empty>;
  } else if (total === 0) {
    body = (
      <Empty
        tip="Generate the table from the study's variables, or add a point by hand."
        action={
          <Button className="ws-small" onClick={() => void generateTable()}>
            <Table2 size={14} /> Generate
          </Button>
        }
      >
        No design points
      </Empty>
    );
  } else {
    const visible = [];
    for (let i = start; i < end; i++) {
      visible.push(<Row key={rows[i].index} row={rows[i]} position={i} selected={selected.has(rows[i].index)} dataset={dataset} onPick={pick} />);
    }
    const wide = 70 + columns * COLUMN_MIN;
    body = (
      <div
        ref={table}
        className="dt-table"
        role="grid"
        tabIndex={0}
        aria-label={`Design table of ${results.name}`}
        aria-rowcount={total + 1}
        aria-colcount={columns + 2}
        aria-multiselectable="true"
        data-selected-count={selected.size}
        onKeyDown={onKeyDown}
        style={{ "--dt-cols": columns } as CSSProperties}
      >
        <div className="dt-headwrap">
          <div ref={head} style={{ minWidth: wide }}>
            <Head dataset={dataset} />
          </div>
        </div>
        <div ref={scroller} className="dt-scroll" onScroll={onScroll}>
          <div className="dt-grid" style={{ minWidth: wide }}>
            <div className="dt-body" role="rowgroup" style={{ height: total * ROW_HEIGHT }}>
              {visible}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <Panel className="flex flex-col overflow-hidden">
      <div className="dt-panel">
        <div className="dt-toolbar" role="toolbar" aria-label="Design table">
          {studies.length > 1 ? (
            <>
              <Tooltip label="Choose the study" side="top" describe={false}>
                <button ref={studyButton} type="button" className="dt-study dt-study-pick no-drag" aria-haspopup="menu" aria-expanded={menu === "study"} onClick={() => setMenu(menu === "study" ? null : "study")}>
                  <FlaskConical size={14} aria-hidden="true" />
                  <span className="truncate">{study?.name ?? "Study"}</span>
                  <ChevronDown size={13} aria-hidden="true" />
                </button>
              </Tooltip>
              <Menu open={menu === "study"} anchor={studyButton} onClose={() => setMenu(null)} items={studyItems} label="Studies" />
            </>
          ) : (
            <span className="dt-study">
              <FlaskConical size={14} aria-hidden="true" />
              <span className="truncate">{study?.name ?? "No study"}</span>
            </span>
          )}
          <span className="dt-divider" aria-hidden="true" />
          <Tool label="Generate" icon={<Table2 size={14} />} disabled={!study || running} why={study ? "Wait for the update to finish" : "Create a study first"} title="Fill the table from the study's variables (study.generate)" onClick={() => void generateTable()} />
          <Tool
            label="Update all"
            icon={<Play size={14} />}
            disabled={!study || running || total === 0 || !needsUpdate}
            why={!study ? "Create a study first" : running ? "An update is running" : total === 0 ? "Generate the table first" : "Every point is done"}
            title="Evaluate every point that is not done (study.update)"
            onClick={() => void updateTable("all")}
          />
          <Tool
            label="Update selected"
            icon={<SkipForward size={14} />}
            disabled={!study || running || selected.size === 0}
            why={running ? "An update is running" : "Select rows in the table first"}
            title="Evaluate the selected points again (study.update)"
            onClick={() => void updateTable(selectedList)}
          />
          <Tool label="Add point" icon={<Plus size={14} />} disabled={!study || !hasVariables} why={study ? "The study has no variables" : "Create a study first"} title="Add a design point by hand (study.add_point)" onClick={openAddPoint} />
          <Tooltip label={total === 0 ? "There are no points to clear" : "Reset or remove points (study.clear)"} side="top" describe={false}>
            <Button ref={clearButton} className="ws-small dt-tool" aria-haspopup="menu" aria-expanded={menu === "clear"} aria-disabled={total === 0 || undefined} onClick={() => total > 0 && setMenu(menu === "clear" ? null : "clear")}>
              <Eraser size={14} />
              <span>Clear</span>
              <ChevronDown size={13} aria-hidden="true" />
            </Button>
          </Tooltip>
          <Menu open={menu === "clear"} anchor={clearButton} onClose={() => setMenu(null)} items={clearItems} label="Clear points" />
          <Tool label="Export CSV" icon={<Download size={14} />} disabled={!study || total === 0} why={study ? "There are no points to export" : "Create a study first"} title="Save the table as CSV in SI units, or copy it when this window has no save dialog (study.export)" onClick={() => void exportTable()} />
          <span className="dt-grow" />
          <span className="dt-status-text muted" aria-live="polite">
            {running ? "Updating…" : counts ? statusText(counts, total) : ""}
          </span>
        </div>
        <div className="dt-content">{body}</div>
      </div>
    </Panel>
  );
}
