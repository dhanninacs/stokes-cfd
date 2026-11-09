/**
 * The Explore store: the study the panels show, its design table and Pareto set, the rows the user
 * selected, and what is running. A zustand store of its own: the project store holds the tree and the
 * selection, this one holds what `study.results` and `study.pareto` answered (ADR-0013: the front end
 * renders what queries and events return and keeps no model).
 *
 * Which study: the one that is selected or holds the selected node; when the selection is elsewhere
 * the last study shown stays while it exists, else the first study (`chooseStudy`).
 *
 * Refreshing: the table is fetched when the study changes and again after `study.generated`,
 * `study.updated`, `study.cleared`, a change to a node under /studies, an undo or redo, a change of
 * display units, a reconnect and a project that opens or closes. A burst of events is answered by one
 * fetch. `study.point` events patch their row in place, once per frame, so a long update fills the table
 * as it goes without a fetch per point. Nothing is fetched while no Explore panel is mounted
 * (`useExploreFeed`); the first one to mount brings the table up to date.
 */
import { useEffect } from "react";
import { create } from "zustand";
import { StokesApiError, type StokesClient } from "../../api/client";
import { findNode, useStore } from "../../state/store";
import { buildDataset, type Dataset } from "./dataset";
import { loadConversions, type UnitPair } from "./conversions";
import { categoryLabels, chooseStudy, groupNodes, studyContaining } from "./study";
import { countRows, readPareto, readPointEvent, readResults, type ParetoResult, type PointEvent, type PointRow, type StudyResults } from "./types";

export interface ExploreState {
  /** The study node the panels show; null when the project has none. */
  studyId: string | null;
  results: StudyResults | null;
  dataset: Dataset | null;
  /** The Pareto set; null while the study has no objectives. */
  pareto: ParetoResult | null;
  /** Why there is no Pareto set although the study has objectives. */
  paretoError: string | null;
  loading: boolean;
  error: string | null;
  /** Calls of ours that block until the server finishes (`study.update`) and have not returned. */
  updating: number;
  /** Selected design points by index, shared by the table and every plot. */
  selected: ReadonlySet<number>;
  /** The point a shift-click range starts from. */
  anchor: number | null;

  refresh(): Promise<void>;
  /** Replace the selection; `anchor` is the point a later range extends from. */
  setSelection(indices: Iterable<number>, anchor?: number | null): void;
  clearSelection(): void;
  /** Add or remove one point. */
  toggle(index: number): void;
  /** Select the points from the anchor to `index` in table order; `additive` keeps the rest of the selection. */
  selectRange(index: number, additive: boolean): void;
  beginUpdate(): void;
  endUpdate(): void;
}

const NO_SELECTION: ReadonlySet<number> = new Set();

export const useExplore = create<ExploreState>((set, get) => ({
  studyId: null,
  results: null,
  dataset: null,
  pareto: null,
  paretoError: null,
  loading: false,
  error: null,
  updating: 0,
  selected: NO_SELECTION,
  anchor: null,

  refresh: () => refresh(),

  setSelection(indices, anchor) {
    const next = new Set(indices);
    set({ selected: next.size === 0 ? NO_SELECTION : next, ...(anchor !== undefined ? { anchor } : {}) });
  },
  clearSelection() {
    if (get().selected.size > 0 || get().anchor !== null) set({ selected: NO_SELECTION, anchor: null });
  },
  toggle(index) {
    const next = new Set(get().selected);
    if (!next.delete(index)) next.add(index);
    set({ selected: next.size === 0 ? NO_SELECTION : next, anchor: index });
  },
  selectRange(index, additive) {
    const { dataset, anchor, selected } = get();
    if (!dataset) return;
    const to = dataset.position.get(index);
    const from = anchor === null ? undefined : dataset.position.get(anchor);
    if (to === undefined || from === undefined) {
      set({ selected: new Set([index]), anchor: index });
      return;
    }
    const next = additive ? new Set(selected) : new Set<number>();
    for (let i = Math.min(from, to); i <= Math.max(from, to); i++) next.add(dataset.rows[i].index);
    set({ selected: next });
  },
  beginUpdate: () => set((s) => ({ updating: s.updating + 1 })),
  endUpdate: () => set((s) => ({ updating: Math.max(0, s.updating - 1) })),
}));

// ---------------------------------------------------------------------------------------------- feeding
let active = 0;
/** Something changed while no panel was mounted: the next one to mount fetches. */
let stale = true;
/** The last study the user pointed at, which stays while the selection is somewhere else. */
let remembered: string | null = null;
let ticket = 0;

/** Keeps the store current while the calling panel is mounted. Every Explore panel calls it. */
export function useExploreFeed(): void {
  useEffect(() => {
    active++;
    if (stale || useExplore.getState().results === null) refreshSoon(0);
    return () => {
      active--;
    };
  }, []);
}

let timer: number | undefined;
let since = 0;
const REFRESH_DELAY = 40;
const REFRESH_MAX_WAIT = 300;

/** One fetch answers a burst of events: it waits for a quiet moment, but never longer than REFRESH_MAX_WAIT. */
function refreshSoon(delay = REFRESH_DELAY): void {
  if (active === 0) {
    stale = true;
    return;
  }
  const now = Date.now();
  if (since === 0) since = now;
  window.clearTimeout(timer);
  timer = window.setTimeout(
    () => {
      since = 0;
      void refresh();
    },
    Math.max(0, Math.min(delay, since + REFRESH_MAX_WAIT - now)),
  );
}

function client(): StokesClient | null {
  return useStore.getState().client;
}

function cleared(studyId: string | null): Partial<ExploreState> {
  return { studyId, results: null, dataset: null, pareto: null, paretoError: null, error: null, loading: false, selected: NO_SELECTION, anchor: null };
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** The selection that survives a new table: only points that are still in it. */
function pruned(selected: ReadonlySet<number>, anchor: number | null, dataset: Dataset): Pick<ExploreState, "selected" | "anchor"> {
  if (selected.size === 0) return { selected, anchor: anchor !== null && dataset.position.has(anchor) ? anchor : null };
  const kept = new Set<number>();
  for (const index of selected) if (dataset.position.has(index)) kept.add(index);
  return {
    selected: kept.size === selected.size ? selected : kept.size === 0 ? NO_SELECTION : kept,
    anchor: anchor !== null && dataset.position.has(anchor) ? anchor : null,
  };
}

/** The SI-to-display pairs a table's columns need, for `loadConversions`. */
function unitPairsOf(results: StudyResults): UnitPair[] {
  return [...results.variables, ...results.responses].map((column) => ({
    si: column.unit,
    display: column.displayUnit,
  }));
}

async function refresh(): Promise<void> {
  const c = client();
  const id = useExplore.getState().studyId;
  if (!c || !id) return;
  const mine = ++ticket;
  stale = false;
  if (useExplore.getState().results === null) useExplore.setState({ loading: true });
  try {
    const raw = await c.call("study.results", { study: id });
    if (mine !== ticket) return;
    const results = readResults(raw);
    if (!results) throw new Error("study.results sent something that is not a table");
    const study = findNode(useStore.getState().tree, id);
    // Ask the server for the conversions the columns need before the table is built, so the numbers
    // are in display units on the first paint. Each pair is fetched once (conversions.ts).
    await loadConversions(c, unitPairsOf(results));
    if (mine !== ticket) return;
    const dataset = buildDataset(results, study ? categoryLabels(study) : {});
    const now = useExplore.getState();
    useExplore.setState({ results, dataset, loading: false, error: null, ...pruned(now.selected, now.anchor, dataset) });
    await loadPareto(c, id, mine);
  } catch (err) {
    if (mine !== ticket) return;
    // The study was removed (or its project closed); the tree refresh that follows names the next one.
    if (err instanceof StokesApiError && (err.code === "model.unknown_node" || err.code === "api.no_project")) {
      useExplore.setState(cleared(useExplore.getState().studyId));
      return;
    }
    useExplore.setState({ loading: false, error: message(err) });
  }
}

/** The Pareto set, when the study has objectives; its failure is a note on the plot, not an error of the table. */
async function loadPareto(c: StokesClient, id: string, mine: number): Promise<void> {
  const study = findNode(useStore.getState().tree, id);
  if (!study || groupNodes(study, "objectives").length === 0) {
    useExplore.setState({ pareto: null, paretoError: null });
    return;
  }
  try {
    const pareto = readPareto(await c.call("study.pareto", { study: id }));
    if (mine !== ticket) return;
    useExplore.setState({ pareto, paretoError: pareto ? null : "study.pareto sent something that is not a Pareto set" });
  } catch (err) {
    if (mine !== ticket) return;
    useExplore.setState({ pareto: null, paretoError: message(err) });
  }
}

// ---------------------------------------------------------------------------------------------- study choice
/** A fingerprint of the study's definition nodes: when it moves, the columns or the objectives did. */
function definitionKey(id: string | null): string {
  const study = findNode(useStore.getState().tree, id);
  if (!study) return "";
  const parts: string[] = [];
  for (const group of ["variables", "responses", "objectives", "constraints"] as const) {
    parts.push(group, ...groupNodes(study, group).map((n) => `${n.id}:${n.name}:${String(n.params.direction ?? n.params.domain ?? "")}`));
  }
  return parts.join("|");
}

let lastDefinition = "";

/** Follow the project store's tree and selection: which study is shown, and whether its definition moved. */
function syncStudy(): void {
  const { tree, selectedId } = useStore.getState();
  const inside = studyContaining(tree, selectedId);
  if (inside) remembered = inside.id;
  const chosen = chooseStudy(tree, selectedId, remembered);
  const id = chosen?.id ?? null;
  if (id !== useExplore.getState().studyId) {
    ticket++; // a fetch for the old study is moot
    useExplore.setState(cleared(id));
    lastDefinition = definitionKey(id);
    if (id) refreshSoon(0);
    return;
  }
  const key = definitionKey(id);
  if (key !== lastDefinition) {
    lastDefinition = key;
    if (id) refreshSoon();
  }
}

// ---------------------------------------------------------------------------------------------- events
const pointQueue = new Map<number, PointEvent>();
let frame = 0;

function queuePoint(event: PointEvent): void {
  pointQueue.set(event.index, event);
  if (frame !== 0) return;
  frame = window.requestAnimationFrame(flushPoints);
}

/** Apply the point events of one frame: patch their rows, recount, rebuild the columns once. */
function flushPoints(): void {
  frame = 0;
  const { results, dataset } = useExplore.getState();
  const events = [...pointQueue.values()];
  pointQueue.clear();
  if (!results || !dataset) return;
  let unknown = false;
  const points: PointRow[] = results.points.slice();
  let changed = false;
  for (const event of events) {
    if (event.study !== results.study) continue;
    const at = dataset.position.get(event.index);
    if (at === undefined) {
      unknown = true; // a point added by hand: its inputs are in the table, not in the event
      continue;
    }
    const row = points[at];
    points[at] = {
      ...row,
      status: event.status,
      outputs: event.status === "done" ? event.outputs : event.status === "pending" ? {} : row.outputs,
      error: event.status === "failed" ? event.error : null,
    };
    changed = true;
  }
  if (changed) {
    const next: StudyResults = { ...results, points, counts: countRows(points) };
    useExplore.setState({ results: next, dataset: buildDataset(next, categoriesFor(results.study)) });
  }
  if (unknown) refreshSoon();
}

function categoriesFor(id: string): Record<string, Map<number, string>> {
  const study = findNode(useStore.getState().tree, id);
  return study ? categoryLabels(study) : {};
}

function isStudy(data: Record<string, unknown>): boolean {
  return data.study === useExplore.getState().studyId;
}

function onEvent(event: string, data: Record<string, unknown>): void {
  switch (event) {
    case "study.point": {
      if (active === 0) {
        stale = true;
        return;
      }
      const parsed = readPointEvent(data);
      if (parsed && isStudy(data)) queuePoint(parsed);
      return;
    }
    case "study.generated":
      if (isStudy(data)) {
        // A new table: its points have new indices, so the old selection means nothing.
        useExplore.getState().clearSelection();
        refreshSoon(0);
      }
      return;
    case "study.updated":
    case "study.cleared":
      if (isStudy(data)) refreshSoon();
      return;
    case "node.added":
    case "node.changed":
    case "node.removed": {
      const path = typeof data.path === "string" ? data.path : "";
      // Definitions live under /studies; a node that was removed has left only its old path behind.
      if (path.startsWith("/studies") || event === "node.removed") refreshSoon();
      return;
    }
    case "history.changed":
    case "project.changed":
      refreshSoon();
      return;
    case "project.opened":
    case "project.closed":
      ticket++;
      remembered = null;
      useExplore.setState(cleared(null));
      stale = true;
      return;
  }
}

// ---------------------------------------------------------------------------------------------- wiring
let attached: StokesClient | null = null;

function attach(c: StokesClient): void {
  if (attached === c) return;
  attached = c;
  c.onEvent(onEvent);
  c.onStatus((connected) => {
    if (connected) refreshSoon(0);
  });
}

let wired = false;

/** Follows the project store for the life of the page; safe to call more than once. */
export function startExplore(): void {
  if (wired) return;
  wired = true;
  const current = client();
  if (current) attach(current);
  useStore.subscribe((state, previous) => {
    if (state.client && state.client !== previous.client) attach(state.client);
    if (state.tree !== previous.tree || state.selectedId !== previous.selectedId) syncStudy();
  });
  syncStudy();
}

startExplore();

if (import.meta.env.DEV || import.meta.env.MODE === "review") {
  // Development and review builds only (as App.tsx does for the other stores): lets a script read and drive
  // the selection without a pointer. Not present in `npm run build`.
  (window as unknown as { __explore?: typeof useExplore }).__explore = useExplore;
}
