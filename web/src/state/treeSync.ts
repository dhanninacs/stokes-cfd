/**
 * How the project tree gets from the server to the store (ADR-0019 step 8).
 *
 * The tree is fetched whole once (`node.tree`: on connect, on a reconnect, when a project opens,
 * after an undo, and as the answer to anything a patch cannot say) and from then on kept current
 * by the node events, which carry the summaries of the nodes they name (state/treePatch.ts has
 * the patching). What arrives is not applied one event at a time. An import announces a node
 * per part as parts finish, thousands in a few seconds, and a store update per event would run
 * every subscriber of the store that often: the events are queued and the queue is applied in one
 * state update per animation frame, with the geometry events of the same frame, and the console
 * lines they produce are written in that update too.
 *
 * A fetch and the events race: the tree the server answers with was cut at some moment between
 * the request and the reply, and an event that arrives meanwhile may or may not be in it. Every
 * patch received while a fetch is in flight is kept, and once the tree is taken they are applied
 * over it. A patch is a whole summary, not a difference, so applying one the tree already shows is
 * harmless, and the order the server emitted them in is the order they are applied in.
 *
 * Patching can fail (a node whose parent this tree does not have). The fetch is the fallback, and a
 * patch that keeps failing switches the store back to fetching after every node event, as it did
 * before there were patches, rather than looping.
 */

import type { StokesClient } from "../api/client";
import type { GeometryEvent, ImportFailedEvent, NodeSummary, ProjectInfo } from "../api/types";
import type { ConsoleLine, StokesState } from "./store";
import {
  collectParts,
  noteFinished,
  noteProgress,
  patchForNode,
  pruneProgress,
  readyEntry,
  reconcileEntries,
  reconcileEntry,
  removedEntry,
  samePartNodes,
  type ImportProgress,
  type PartEntry,
  type PartNode,
} from "./parts";
import { applyPatches, indexFor, indexTree, readPatches, setTreeIndex, type TreeIndex, type TreePatch } from "./treePatch";

/** A refresh waits this long after the last event that asks for one, in ms... */
const REFRESH_DELAY = 40;
/** ...but never longer than this after the first, so a steady stream of events cannot starve it. */
const REFRESH_MAX_WAIT = 400;
/** A page that is not showing gets no animation frames: the queue is applied by this timer instead. */
const FLUSH_FALLBACK_MS = 120;
/**
 * A frame's update costs time in proportion to the parts there are (the store's mesh entries are
 * copied, and the viewport reads every one of them), so the next update waits this many times as
 * long as the last one took: about one frame in eight of main-thread time at most, however many parts
 * and however steady the stream of events. A small tree costs a fraction of a millisecond, which
 * leaves the update on every frame.
 */
const DUTY = 7;
/** ...but never longer than this, so that a change is on the screen within a quarter of a second. */
const MAX_GAP_MS = 250;
/** Most events one update applies; the rest wait for the next. A page that was starved for a second must not answer with one task of a hundred milliseconds. */
const MAX_OPS = 400;
/** Patch batches that failed in a row before the store stops patching and fetches after every node event. */
const MAX_PATCH_FAILURES = 5;

export interface LogLine {
  kind: ConsoleLine["kind"];
  text: string;
}

export interface TreeSyncHost {
  get(): StokesState;
  set(partial: Partial<StokesState>): void;
  /** The console update that appends `lines`. */
  consoleWith(lines: readonly LogLine[]): Partial<StokesState>;
  logError(text: string): void;
  /** The expansion a tree gets when its project opens. */
  defaultExpansion(tree: NodeSummary): Record<string, boolean>;
  /** An import failed: a part, or what was left of a cancelled import. */
  importFailed(event: ImportFailedEvent): void;
}

export interface TreeSync {
  /** An event from the server. `logLine` is what to show in the console, null for none. */
  receive(event: string, data: Record<string, unknown>, geometry: GeometryEvent | null, logLine: string | null): void;
  /** The tree from the server, in the store when this resolves; a fetch already running is not joined, because it began before this call. */
  refresh(): Promise<void>;
  /** Console lines the events queued: they go before a line written now, so that the console keeps the order things happened in. */
  takeLog(): LogLine[];
}

/** Numbers for reviews: the harness reads them from `window.__stokesTree`. */
export const treeStats = {
  /** Updates applied (one per animation frame that had anything). */
  batches: 0,
  /** Patches applied, and events queued for them. */
  patches: 0,
  /** Whole trees fetched. */
  fetches: 0,
  /** Batches that could not be applied and asked for a fetch. */
  failedBatches: 0,
  /** Time of the last batch and the longest, in ms: patching, deriving parts, writing the store. */
  lastBatchMs: 0,
  maxBatchMs: 0,
  /** The longest of each part of a batch, in ms: applying the patches, the part entries, and `set` (which runs the store's subscribers, the viewport's among them). */
  maxApplyMs: 0,
  maxPartsMs: 0,
  maxSetMs: 0,
  /** Time to take a fetched tree, in ms: indexing, parts, the store. */
  lastTakeMs: 0,
  legacy: false,
};

if (import.meta.env.DEV || import.meta.env.MODE === "review") {
  (window as unknown as { __stokesTree?: typeof treeStats }).__stokesTree = treeStats;
}

type Op = { kind: "patch"; seq: number; patch: TreePatch } | { kind: "geometry"; event: GeometryEvent };

interface Fetch {
  /** Patches with a number above this arrived after the request left. */
  startSeq: number;
}

/** True for the geometry events that, without a summary of their own, leave the tree's parameters and status to be fetched. */
function geometryNeedsFetch(event: GeometryEvent): boolean {
  return event.kind === "failed" || (event.kind === "ready" && event.complete);
}

export function createTreeSync(host: TreeSyncHost): TreeSync {
  let seq = 0;
  let queue: Op[] = [];
  let lines: LogLine[] = [];
  let replayLog: { seq: number; patch: TreePatch }[] = [];
  const inflight = new Set<Fetch>();
  /** Counts the fetches begun. */
  let ticket = 0;
  /** The newest fetch whose tree was taken. */
  let taken = 0;
  /** Set when `project.opened` arrives, to the number of fetches begun by then: the first fetch begun after it opens the new tree. */
  let openAfter: number | null = null;
  let failures = 0;
  let legacy = false;
  let frame = 0;
  let timer = 0;
  /** When the last update ended, and how long the next one waits after that: see DUTY. */
  let lastFlushEnd = 0;
  let gap = 0;
  let refreshTimer = 0;
  let refreshSince = 0;
  let running: Promise<void> | null = null;
  let retried = false; // one automatic retry after a refused read, so a project the server already holds is not missed
  let next: Promise<void> | null = null;
  /** Where each part is in `partNodes`; valid for `slotsOf`. */
  let slots = new Map<string, number>();
  let slotsOf: readonly PartNode[] | null = null;

  // ------------------------------------------------------------------ applying a frame's events
  function schedule(): void {
    if (frame !== 0 || timer !== 0) return;
    const wait = lastFlushEnd + gap - performance.now();
    if (wait > 1) {
      timer = window.setTimeout(flush, wait); // a stream of events is answered at the pace the updates can keep
      return;
    }
    frame = requestAnimationFrame(flush);
    timer = window.setTimeout(flush, FLUSH_FALLBACK_MS);
  }

  function slotOf(nodes: readonly PartNode[], id: string): number | undefined {
    if (slotsOf !== nodes) {
      slots = new Map(nodes.map((node, i) => [node.id, i]));
      slotsOf = nodes;
    }
    return slots.get(id);
  }

  /**
   * `partNodes` after `changed` nodes were patched in: the same array when no part moved, a copy
   * with the new entries otherwise, and null when the list has to be made again (a part this list
   * does not have). `moved` is the parts whose entry in the list changed.
   */
  function patchedParts(before: PartNode[], changed: readonly NodeSummary[]): { nodes: PartNode[]; moved: PartNode[] } | null {
    let copy: PartNode[] | null = null;
    const moved: PartNode[] = [];
    for (const node of changed) {
      if (node.type !== "part") continue;
      const slot = slotOf(before, node.id);
      if (slot === undefined) return null;
      const fresh = collectParts(node);
      if (fresh.length !== 1) return null;
      if (!samePartNodes([(copy ?? before)[slot]], fresh)) {
        copy ??= before.slice();
        copy[slot] = fresh[0];
        moved.push(fresh[0]);
      }
    }
    if (copy !== null) slotsOf = copy; // the parts are where they were: the slots hold for the copy
    return { nodes: copy ?? before, moved };
  }

  function flush(): void {
    if (frame !== 0) cancelAnimationFrame(frame);
    window.clearTimeout(timer);
    frame = 0;
    timer = 0;
    const ops = queue.length > MAX_OPS ? queue.splice(0, MAX_OPS) : queue;
    const written = lines;
    if (ops === queue) queue = [];
    lines = [];
    if (ops.length === 0 && written.length === 0) return;
    const started = performance.now();
    const before = host.get();
    const update: Partial<StokesState> = {};
    let fetchAgain = false;
    // The mesh entries are copied once for the frame, when the first one changes, and written in place after that:
    // thousands of events a second must not each copy thousands of entries.
    let parts = before.parts;
    let ownParts = false;
    const writableParts = (): Record<string, PartEntry> => {
      if (!ownParts) {
        parts = { ...parts };
        ownParts = true;
      }
      return parts;
    };

    // The tree.
    const patches: TreePatch[] = [];
    for (const op of ops) if (op.kind === "patch") patches.push(op.patch);
    if (patches.length > 0) {
      const index = before.tree === null ? null : (indexFor(before.tree) ?? reindex(before.tree));
      if (index === null) {
        fetchAgain = inflight.size === 0; // with a fetch on the way, the log carries these over it
      } else {
        const appliedAt = performance.now();
        const applied = applyPatches(index, patches);
        treeStats.maxApplyMs = Math.max(treeStats.maxApplyMs, performance.now() - appliedAt);
        if (applied === null) {
          fetchAgain = true;
          treeStats.failedBatches++;
          if (++failures >= MAX_PATCH_FAILURES && !legacy) {
            legacy = true;
            treeStats.legacy = true;
            host.logError("The tree could not be patched from events; it is fetched after every change instead.");
          }
        } else {
          failures = 0;
          treeStats.patches += patches.length;
          const tree = index.root;
          update.tree = tree;
          const partsAt = performance.now();
          // The parts follow the tree: a mesh entry for every part node, none for a node that left.
          const patched = applied.structural ? null : patchedParts(before.partNodes, applied.changed);
          if (patched === null) {
            const nodes = collectParts(tree);
            if (!samePartNodes(before.partNodes, nodes)) {
              update.partNodes = nodes;
              const reconciled = reconcileEntries(parts, nodes);
              if (reconciled !== parts) {
                parts = reconciled;
                ownParts = true;
              }
            }
          } else if (patched.nodes !== before.partNodes) {
            update.partNodes = patched.nodes;
            for (const node of patched.moved) {
              const old = parts[node.id];
              const entry = reconcileEntry(old, node);
              if (entry !== old) writableParts()[node.id] = entry;
            }
          }
          treeStats.maxPartsMs = Math.max(treeStats.maxPartsMs, performance.now() - partsAt);
          if (Object.keys(before.importProgress).length > 0) update.importProgress = pruneProgress(before.importProgress, tree, Date.now());
          const selected = before.selectedId;
          if (selected !== null) {
            const node = index.byId.get(selected);
            if (node === undefined) {
              update.selectedId = null;
              update.selectedPatch = null;
            } else if (applied.changed.some((changed) => changed.id === selected)) {
              update.selectedPatch = patchForNode(node, before.geometry);
            }
          }
        }
      }
    }

    // What the geometry store said about meshes, in the order it said it.
    let importProgress: Record<string, ImportProgress> = update.importProgress ?? before.importProgress;
    for (const op of ops) {
      if (op.kind !== "geometry") continue;
      const event = op.event;
      switch (event.kind) {
        case "progress":
          importProgress = noteProgress(importProgress, event, Date.now());
          break;
        case "ready": {
          const entry = parts[event.nodeId]; // a part the tree does not have yet is left to the reconcile that follows its node
          if (entry) writableParts()[entry.id] = readyEntry(entry, event);
          if (event.complete) importProgress = noteFinished(importProgress, event.path, event.nodeId);
          break;
        }
        case "removed": {
          const entry = parts[event.nodeId];
          if (entry) writableParts()[entry.id] = removedEntry(entry);
          break;
        }
        case "failed":
          importProgress = noteFinished(importProgress, event.path, event.nodeId);
          host.importFailed(event);
          break;
      }
    }
    if (parts !== before.parts) update.parts = parts;
    if (importProgress !== before.importProgress) update.importProgress = importProgress;
    if (written.length > 0) Object.assign(update, host.consoleWith(written));
    if (Object.keys(update).length > 0) {
      const setAt = performance.now();
      host.set(update);
      treeStats.maxSetMs = Math.max(treeStats.maxSetMs, performance.now() - setAt);
    }

    lastFlushEnd = performance.now();
    const ms = lastFlushEnd - started;
    gap = Math.min(MAX_GAP_MS, ms * DUTY);
    treeStats.batches++;
    treeStats.lastBatchMs = ms;
    if (ms > treeStats.maxBatchMs) treeStats.maxBatchMs = ms;
    if (fetchAgain) scheduleRefresh();
    if (queue.length > 0) schedule(); // more than one update's worth was waiting
  }

  /** An index for a tree somebody put in the store by hand (a review script does). */
  function reindex(tree: NodeSummary): TreeIndex {
    const index = indexTree(tree);
    setTreeIndex(index);
    return index;
  }

  // ------------------------------------------------------------------ fetching the tree
  /**
   * Asks for a refresh soon. Events come in bursts, so one fetch answers a burst; the wait is
   * counted from the last event, but a burst that never pauses is answered after
   * REFRESH_MAX_WAIT all the same.
   */
  function scheduleRefresh(): void {
    const now = Date.now();
    if (refreshSince === 0) refreshSince = now;
    window.clearTimeout(refreshTimer);
    const wait = Math.max(0, Math.min(REFRESH_DELAY, refreshSince + REFRESH_MAX_WAIT - now));
    refreshTimer = window.setTimeout(() => {
      refreshSince = 0;
      void refresh();
    }, wait);
  }

  function refresh(): Promise<void> {
    if (running === null) {
      running = fetchTree().finally(() => {
        running = null;
      });
      return running;
    }
    // One is on the way, and it began before this call: whatever it brings may predate the change the caller waits for. One more follows it, however many ask.
    if (next === null) {
      next = running.then(() => {
        next = null;
        return refresh();
      });
    }
    return next;
  }

  async function fetchTree(): Promise<void> {
    const client: StokesClient | null = host.get().client;
    if (!client) return;
    const mine = ++ticket;
    const fetch: Fetch = { startSeq: seq };
    inflight.add(fetch);
    try {
      const [tree, project] = await Promise.all([client.call<NodeSummary>("node.tree"), client.call<ProjectInfo>("project.info")]);
      // Debug-level breadcrumb for the "start screen although the server holds a project" report:
      // says whether this read was taken and what project.info answered. Silent at default levels.
      console.debug("[stokes] tree read", { ticket: mine, taken, project: project?.name ?? null, path: project?.path ?? null });
      if (mine > taken) {
        taken = mine;
        take(tree, project, fetch, mine);
      }
      retried = false;
    } catch (err) {
      // With no project open (the Start screen) both reads are refused with `api.no_project`: that
      // is the normal first state, not a failure, so the console stays clean.
      const code = (err as { code?: unknown } | null)?.code;
      console.debug("[stokes] tree read refused", { ticket: mine, code, message: (err as Error)?.message });
      // **No project any more is a state to show, not only to tolerate.** After `project.close` this
      // read is refused with `api.no_project`, and keeping the old tree and project left the closed
      // project's workspace on screen (and every check failing with "no project is open") instead of
      // the Start screen. A newer read that already landed wins, as everywhere in this module.
      if (code === "api.no_project" && mine > taken) {
        taken = mine;
        if (host.get().project !== null || host.get().tree !== null) host.set({ project: null, tree: null });
      }
      if (!(err instanceof Error && err.message === "not connected") && code !== "api.no_project") {
        host.logError((err as Error).message);
        // A read refused for any other reason (the server still activating the project it was
        // started with, say) must not leave the Start screen up until some event happens to
        // arrive: one more try, a moment later, is what an event would have caused anyway.
        if (!retried) {
          retried = true;
          scheduleRefresh();
        }
      }
    } finally {
      inflight.delete(fetch);
      if (inflight.size === 0) replayLog = [];
      else {
        const oldest = Math.min(...[...inflight].map((f) => f.startSeq));
        replayLog = replayLog.filter((entry) => entry.seq > oldest);
      }
    }
  }

  /** The fetched tree becomes the store's, with what arrived while it was on the way applied over it. */
  function take(tree: NodeSummary, project: ProjectInfo, fetch: Fetch, mine: number): void {
    const started = performance.now();
    const before = host.get();
    const index = indexTree(tree);
    setTreeIndex(index);
    const replay = replayLog.filter((entry) => entry.seq > fetch.startSeq).map((entry) => entry.patch);
    // The patches still queued came before the reply was read (it has them) or are in the replay.
    queue = queue.filter((op) => op.kind !== "patch");
    let root = tree;
    if (replay.length > 0) {
      if (applyPatches(index, replay) === null) scheduleRefresh();
      else root = index.root;
    }
    const nodes = collectParts(root);
    const update: Partial<StokesState> = {
      tree: root,
      project,
      // The parts follow the tree: a mesh entry for every part node, none for a node that left.
      partNodes: samePartNodes(before.partNodes, nodes) ? before.partNodes : nodes,
      parts: reconcileEntries(before.parts, nodes),
      importProgress: pruneProgress(before.importProgress, root, Date.now()),
      // **A hidden node that has gone is forgotten.** Without this the list grows for the life of
      // the session and - worse - keeps a NAME that a future node could inherit, so a new part
      // called `box` would appear pre-hidden for no reason a user could discover. The same rule the
      // selection state below follows for a node that left.
      hiddenNodes:
        before.hiddenNodes.length === 0
          ? before.hiddenNodes
          : before.hiddenNodes.filter((id) => index.byId.has(id)),
    };
    // The first tree of a project that opened: its groups open to the workspace's depth. A fetch
    // that began before the event may still bring the old project's tree, and leaves this to the next.
    if (openAfter !== null && mine > openAfter) {
      openAfter = null;
      update.expanded = host.defaultExpansion(root);
    }
    // The selection may have been created, removed or renamed by this or another client.
    const selected = before.selectedId;
    if (selected !== null) {
      const node = index.byId.get(selected);
      if (node === undefined) {
        update.selectedId = null;
        update.selectedPatch = null;
      } else {
        update.selectedPatch = patchForNode(node, before.geometry);
      }
    }
    host.set(update);
    treeStats.fetches++;
    treeStats.lastTakeMs = performance.now() - started;
  }

  // ------------------------------------------------------------------ events
  function receive(event: string, data: Record<string, unknown>, geometry: GeometryEvent | null, logLine: string | null): void {
    if (logLine !== null) lines.push({ kind: "event", text: logLine });
    if (event === "project.opened") {
      openAfter = ticket;
      queue = [];
      replayLog = [];
    } else if (event === "project.closed") {
      queue = [];
      replayLog = [];
    }
    const read = legacy ? null : readPatches(event, data);
    if (read !== null) {
      for (const patch of read.patches) {
        const entry = { seq: ++seq, patch };
        queue.push({ kind: "patch", ...entry });
        if (inflight.size > 0) replayLog.push(entry);
      }
      if (read.refresh) scheduleRefresh();
    } else if (/^(node|project|history)\./.test(event) || (geometry !== null && geometryNeedsFetch(geometry))) {
      scheduleRefresh(); // an event that cannot be patched from, or one whose effect on the tree is too wide to patch
    }
    if (geometry !== null) queue.push({ kind: "geometry", event: geometry });
    if (queue.length > 0 || lines.length > 0) schedule();
  }

  function takeLog(): LogLine[] {
    const taken = lines;
    lines = [];
    return taken;
  }

  return { receive, refresh, takeLog };
}
