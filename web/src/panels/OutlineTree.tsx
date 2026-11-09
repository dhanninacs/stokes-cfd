/**
 * The outline tree, drawn from `flattenOutline`'s lines (G-306).
 *
 * **A flat list, not a recursive component**: the model already decided what is open, what matches the
 * filter and what each row's state is, so this file renders and nothing else. That is what makes the
 * behaviours testable without a DOM - the folding and filtering are theirs, not this component's.
 *
 * **Selection and opening are the store's**, exactly as the groups tree uses them: `select` for a node,
 * `setVirtualRow` for a row that is not a node, and `toggleExpanded` for folding. **A reference row is not
 * inert** - it scrolls to the row that owns its content and opens it.
 */
import { useEffect, useMemo, useRef } from "react";
import { ChevronRight } from "lucide-react";
import type { NodeSummary } from "../api/types";
import { useStore } from "../state/store";
import { clickAction, flattenOutline, type OutlineLine, type OutlineProjection, type OutlineStage } from "./outlineModel";
import { outlineStateAdapter } from "./outlineStatus";

/** The dot colour for a state, using the design's own tokens - no new colours. */
const STATE_TONE: Readonly<Record<string, string>> = {
  todo: "warn",
  failed: "error",
  stale: "warn",
  running: "ok",
  ready: "ok",
  done: "ok",
  idle: "muted",
};

export interface OutlineTreeProps {
  projection: OutlineProjection;
  /** The decorated tree, for the adapter's findings and the node lookup. */
  root: NodeSummary | null;
  /** True when the mesh result is out of date, from the mesh panel's own read. */
  meshStale: boolean;
  /** True when the results are on screen. */
  resultsShown: boolean;
}

export default function OutlineTree({ projection, root, meshStale, resultsShown }: OutlineTreeProps) {
  const expandedRecord = useStore((s) => s.expanded);
  const filter = useStore((s) => s.treeFilter);
  const selectedId = useStore((s) => s.selectedId);
  const setup = useStore((s) => s.setup);
  const run = useStore((s) => s.run);
  const select = useStore((s) => s.select);
  const toggleExpanded = useStore((s) => s.toggleExpanded);
  const listRef = useRef<HTMLDivElement>(null);

  const expanded = useMemo(
    () => new Set(Object.entries(expandedRecord).filter(([, open]) => open).map(([id]) => id)),
    [expandedRecord],
  );

  const adapter = useMemo(
    () =>
      outlineStateAdapter({
        setup,
        run: run.activeRun !== null ? { id: run.activeRun, state: run.runs.find((r) => r.id === run.activeRun)?.state ?? "" } : null,
        meshStale,
        hasMesh: (root?.children ?? []).some((g) => g.path === "/mesh/results"),
        resultsShown,
      }),
    [setup, run, meshStale, root, resultsShown],
  );

  const states = useMemo(() => {
    const out = new Map<string, { state: import("./outlineModel").OutlineState; text: string }>();
    for (const section of projection.sections) {
      const stageState = adapter.stateOf(section.id);
      if (stageState !== "idle") out.set(section.id, { state: stageState, text: "" });
    }
    return out;
  }, [projection, adapter]);

  const lines = useMemo(
    () => flattenOutline(projection, { expanded, rowValue: adapter.valueOf, states, filter }),
    [projection, expanded, adapter, states, filter],
  );

  // **A reference row goes to its owner**: the owner is opened, selected and scrolled to, so the click
  // has a destination rather than being decoration.
  useEffect(() => {
    listRef.current?.querySelector('[data-current="true"]')?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  const goTo = (line: OutlineLine): void => {
    const action = clickAction(line);
    if (action.kind === "ref") {
      toggleExpanded(action.target);
      select(null);
      requestAnimationFrame(() => listRef.current?.querySelector(`[data-line="${action.target}"]`)?.scrollIntoView({ block: "center" }));
      return;
    }
    if (action.kind === "fold") toggleExpanded(action.target);
    if (action.kind === "select") select(action.target);
  };

  return (
    <div className="outline-tree" role="tree" aria-label="Outline" ref={listRef} data-ws="outline-tree">
      {lines.map((line) => (
        <div
          key={line.id}
          role="treeitem"
          aria-level={line.depth + 1}
          aria-expanded={line.foldable ? line.open : undefined}
          aria-selected={line.id === selectedId}
          tabIndex={0}
          data-line={line.id}
          data-depth={line.depth}
          data-stage={line.stage satisfies OutlineStage}
          data-current={line.id === selectedId}
          className={`outline-row${line.id === selectedId ? " is-selected" : ""}${line.refScope !== null ? " is-ref" : ""}`}
          style={{ paddingLeft: `${8 + line.depth * 14}px` }}
          onClick={() => goTo(line)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              goTo(line);
            }
          }}
        >
          {line.foldable ? (
            <ChevronRight size={12} aria-hidden="true" className={`outline-caret${line.open ? " is-open" : ""}`} />
          ) : (
            <span className="outline-caret-spacer" aria-hidden="true" />
          )}
          <span className="outline-label">{line.label}</span>
          {line.value !== "" && <span className="outline-value muted">{line.value}</span>}
          {line.stateText !== "" && (
            <span className={`outline-state tone-${STATE_TONE[line.state] ?? "muted"}`} data-state={line.state}>
              {line.stateText}
            </span>
          )}
          {line.refScope !== null && <span className="outline-ref-hint muted">shown above</span>}
        </div>
      ))}
      {lines.length === 0 && <p className="muted mesh-hint">Nothing matches the filter.</p>}
    </div>
  );
}
