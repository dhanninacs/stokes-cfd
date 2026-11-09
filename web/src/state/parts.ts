/**
 * What the front end keeps about imported parts, as pure functions over the node tree and the
 * geometry.* events of stokes.geometry.store (ADR-0014, decision G1). The store holds the state and
 * calls these; the viewport reads it and does the drawing.
 *
 * A part is one solid with its own mesh. A file with several solids becomes a group of parts, a
 * file with one becomes a single part; either way the viewport asks for every `part` node's mesh by
 * the node's id, and the server says when a mesh changed (`geometry.part_ready`), went away
 * (`geometry.part_removed`) or could not be made (`geometry.import_failed`).
 */

import type { GeometryEvent, ImportProgressEvent, NodeStatus, NodeSummary, PartReadyEvent } from "../api/types";
import type { PatchMeta } from "../viewport/geometry";

// ---------------------------------------------------------------------------------------------- events
function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function whole(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
}

/** A geometry.* event as a typed value, or null for another event or a payload that is not one of ours. */
export function readGeometryEvent(event: string, data: Record<string, unknown>): GeometryEvent | null {
  const nodeId = text(data.node_id);
  if (nodeId === null) return null;
  const path = text(data.path);
  switch (event) {
    case "geometry.import_progress": {
      const pass = text(data.pass);
      if (pass === null) return null;
      return { kind: "progress", nodeId, path, pass, solid: text(data.solid), index: whole(data.index), count: whole(data.count), triangles: whole(data.triangles) };
    }
    case "geometry.part_ready":
      return { kind: "ready", nodeId, path, pass: text(data.pass) ?? "", complete: data.complete === true };
    case "geometry.part_removed":
      return { kind: "removed", nodeId, path };
    case "geometry.import_failed":
      return { kind: "failed", nodeId, path, code: text(data.code) ?? "geometry.error", message: text(data.message) ?? "the import failed" };
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------------------------- part nodes
/** A `part` node of the tree reduced to what the viewport needs. */
export interface PartNode {
  id: string;
  name: string;
  path: string;
  status: NodeStatus;
  /** The CAD file the part came from (the `source` parameter). */
  source: string;
  /** `#rrggbb` from the `color` parameter, or null when it is missing or malformed. */
  color: string | null;
  /** The `visible` parameter. */
  visible: boolean;
  /** The `opacity` parameter clamped to 0..1: 1 when it is missing or not a number (ADR-0021 4.1). */
  opacity: number;
  /** The part's `simulate` parameter: false when the user left it out of the simulation (T-222). */
  simulate: boolean;
}

const HEX = /^#[0-9a-f]{6}$/i;

function opacityOf(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 1;
}

/** Every `part` node of the tree, in tree order. */
export function collectParts(tree: NodeSummary | null): PartNode[] {
  const out: PartNode[] = [];
  const visit = (node: NodeSummary): void => {
    if (node.type === "part") {
      const color = node.params.color;
      out.push({
        id: node.id,
        name: node.name,
        path: node.path,
        status: node.status,
        source: typeof node.params.source === "string" ? node.params.source : "",
        color: typeof color === "string" && HEX.test(color) ? color.toLowerCase() : null,
        visible: node.params.visible !== false,
        opacity: opacityOf(node.params.opacity),
        // **Read here, beside `visible` and `opacity`**, because it is the same kind of fact: a
        // display property of the body that the tree and the viewport both show.
        simulate: node.params.simulate !== false,
      });
    }
    for (const child of node.children ?? []) visit(child);
  };
  if (tree) visit(tree);
  return out;
}

export function samePartNodes(a: readonly PartNode[], b: readonly PartNode[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((x, i) => {
    const y = b[i];
    return (
      x.id === y.id &&
      x.name === y.name &&
      x.path === y.path &&
      x.status === y.status &&
      x.source === y.source &&
      x.color === y.color &&
      x.visible === y.visible &&
      x.opacity === y.opacity
    );
  });
}

// ---------------------------------------------------------------------------------------------- mesh entries
/** What the server last said about one part's mesh. The viewport acts when `revision` moves. */
export interface PartEntry {
  id: string;
  /** Moves whenever the mesh may have changed or gone: the viewport fetches it again, or drops it. */
  revision: number;
  /** False while there is no mesh to fetch: the part is new, failed, or its mesh was dropped. */
  present: boolean;
  /** The part's tree status when last seen; a part that leaves "new" without an announcement is fetched. */
  status: NodeStatus;
  /** The pass of the last geometry.part_ready: "coarse", "fine" or "cache"; null before any. */
  pass: string | null;
  /** True once the part's final mesh is in place. */
  complete: boolean;
}

/**
 * A part just seen in the tree. One that is new or failed has no mesh yet: asking would only draw a
 * 404, and the server announces the mesh when there is one. Any other has been imported before (it
 * was cached, or the import ran while no client listened), so its mesh is fetched at once.
 */
export function startEntry(node: PartNode): PartEntry {
  const waiting = node.status === "new" || node.status === "failed";
  return { id: node.id, revision: waiting ? 0 : 1, present: !waiting, status: node.status, pass: null, complete: false };
}

/**
 * The entries for the parts the tree has: one is made for every new part, one is dropped for every
 * part that left, and a part that stopped being "new" without an announcement (an event lost to a
 * reconnect, or one that arrived before the tree) is fetched. Returns `entries` itself when nothing
 * changed, so subscribers see no update.
 */
export function reconcileEntries(entries: Record<string, PartEntry>, nodes: readonly PartNode[]): Record<string, PartEntry> {
  let changed = nodes.length !== Object.keys(entries).length;
  const next: Record<string, PartEntry> = {};
  for (const node of nodes) {
    const old = entries[node.id];
    const entry = reconcileEntry(old, node);
    next[node.id] = entry;
    if (entry !== old) changed = true;
  }
  return changed ? next : entries;
}

/**
 * The entry for one part the tree shows: made when there is none, moved on when the part's status
 * did, and `old` itself when nothing changed. `reconcileEntries` is this over every part; the tree
 * sync (treeSync.ts) uses it for the parts a batch of patches touched.
 */
export function reconcileEntry(old: PartEntry | undefined, node: PartNode): PartEntry {
  if (!old) return startEntry(node);
  if (old.status === node.status) return old;
  // Left "new" with no mesh known: the announcement was missed, so ask. With a mesh known (the
  // usual case: part_ready came first and the tree catches up) there is nothing to fetch again.
  const missed = !old.present && old.status === "new" && node.status !== "new" && node.status !== "failed";
  return missed ? { ...old, status: node.status, present: true, revision: old.revision + 1 } : { ...old, status: node.status };
}

/** The entry of a part whose mesh changed: fetch it again. */
export function readyEntry(entry: PartEntry, event: PartReadyEvent): PartEntry {
  return { ...entry, present: true, revision: entry.revision + 1, pass: event.pass, complete: event.complete };
}

/** The entry of a part whose mesh is gone. */
export function removedEntry(entry: PartEntry): PartEntry {
  return { ...entry, present: false, revision: entry.revision + 1, complete: false };
}

/** The mesh of a part changed: fetch it again. A part the tree does not know yet is left to `reconcileEntries`. */
export function markReady(entries: Record<string, PartEntry>, event: PartReadyEvent): Record<string, PartEntry> {
  const entry = entries[event.nodeId];
  if (!entry) return entries;
  return { ...entries, [entry.id]: readyEntry(entry, event) };
}

/** The mesh of a part is gone (undo, or an import that did not finish): the viewport drops it. */
export function markRemoved(entries: Record<string, PartEntry>, id: string): Record<string, PartEntry> {
  const entry = entries[id];
  if (!entry) return entries;
  return { ...entries, [id]: removedEntry(entry) };
}

/** After a reconnect every announcement since the drop is lost: ask for every mesh again. */
export function markAllStale(entries: Record<string, PartEntry>): Record<string, PartEntry> {
  const next: Record<string, PartEntry> = {};
  for (const entry of Object.values(entries)) next[entry.id] = { ...entry, present: true, revision: entry.revision + 1 };
  return next;
}

// ---------------------------------------------------------------------------------------------- import progress
/**
 * One import under way. A file of several solids is one import of many parts, so progress is kept per
 * import (by the path of its top node), not per part: the line shows the newest event and does not
 * step back when one part finishes before the others.
 */
export interface ImportProgress {
  /** Path of the import's top node: the single part, or the group the file became. */
  root: string;
  /** The file's name when the command that started the import said it. */
  file: string | null;
  /** "read" while the file is read, then "coarse" and "fine". */
  pass: string;
  /** Position of the solid being meshed among the import's solids, from 0. */
  index: number;
  /** Solids in the import; 0 while that is not known yet. */
  count: number;
  triangles: number;
  /** Ids of the parts that finished, or failed: the import ends when all of them have. */
  done: string[];
  /** True once the tree has shown the root: from then on its absence means the import is gone. */
  seen: boolean;
  at: number;
}

const PARTS_PREFIX = "/geometry/parts/";
/** Entries that never showed up in the tree are dropped after this long, in ms. */
const UNSEEN_MS = 5000;

/** The top node of the import a part belongs to: `/geometry/parts/two/block` belongs to `/geometry/parts/two`. */
export function importRoot(path: string): string {
  if (!path.startsWith(PARTS_PREFIX)) return path;
  return PARTS_PREFIX + path.slice(PARTS_PREFIX.length).split("/")[0];
}

export function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/** The newest event of an import: what pass it is in and which solid it has reached. */
export function noteProgress(map: Record<string, ImportProgress>, event: ImportProgressEvent, now: number): Record<string, ImportProgress> {
  if (event.path === null) return map;
  const root = importRoot(event.path);
  const old = map[root];
  const entry: ImportProgress = {
    root,
    file: old?.file ?? null,
    pass: event.pass,
    index: event.index,
    count: event.count > 0 ? event.count : (old?.count ?? 0),
    triangles: event.triangles,
    done: old?.done ?? [],
    seen: old?.seen ?? false,
    at: now,
  };
  return { ...map, [root]: entry };
}

/** An import the user's own command started: the line can say "Reading" before the server's first event. */
export function noteStarted(map: Record<string, ImportProgress>, path: string, file: string, now: number): Record<string, ImportProgress> {
  const root = importRoot(path);
  const old = map[root];
  if (old) return old.file === file ? map : { ...map, [root]: { ...old, file } };
  return { ...map, [root]: { root, file, pass: "read", index: 0, count: 0, triangles: 0, done: [], seen: false, at: now } };
}

/** A part reached its final mesh or failed; the import is over when every part has. */
export function noteFinished(map: Record<string, ImportProgress>, path: string | null, id: string): Record<string, ImportProgress> {
  if (path === null) return map;
  const root = importRoot(path);
  const old = map[root];
  if (!old) return map;
  const done = old.done.includes(id) ? old.done : [...old.done, id];
  const next = { ...map };
  if (old.count <= 0 || done.length >= old.count) delete next[root];
  else next[root] = { ...old, done };
  return next;
}

/** The node at a path such as `/geometry/parts/two`, or null. */
export function nodeAtPath(tree: NodeSummary | null, path: string): NodeSummary | null {
  let node = tree;
  for (const name of path.split("/")) {
    if (!name) continue;
    node = node?.children?.find((child) => child.name === name) ?? null;
    if (!node) return null;
  }
  return node;
}

/**
 * Drops imports whose top node has left the tree (an undo or a delete ends an import without a
 * word) and notes the ones the tree now shows. An import the tree never showed is given a few
 * seconds, because the tree is fetched a moment after the command that made the node.
 */
export function pruneProgress(map: Record<string, ImportProgress>, tree: NodeSummary | null, now: number): Record<string, ImportProgress> {
  let next = map;
  for (const entry of Object.values(map)) {
    const present = nodeAtPath(tree, entry.root) !== null;
    let replacement: ImportProgress | null | undefined;
    if (present && !entry.seen) replacement = { ...entry, seen: true };
    else if (!present && (entry.seen || now - entry.at > UNSEEN_MS)) replacement = null;
    if (replacement === undefined) continue;
    if (next === map) next = { ...map };
    if (replacement === null) delete next[entry.root];
    else next[entry.root] = replacement;
  }
  return next;
}

/** The import the progress line shows: the one that spoke last. */
export function latestProgress(map: Record<string, ImportProgress>): ImportProgress | null {
  let latest: ImportProgress | null = null;
  for (const entry of Object.values(map)) if (latest === null || entry.at > latest.at) latest = entry;
  return latest;
}

const PASS_ORDER = ["coarse", "fine"];

/** How far the import has come, 0..1, or null while that is not known (the file is being read). */
export function progressFraction(p: ImportProgress): number | null {
  const pass = PASS_ORDER.indexOf(p.pass);
  if (pass < 0 || p.count <= 0) return null;
  return (pass + Math.min(1, (p.index + 1) / p.count)) / PASS_ORDER.length;
}

/** "Reading part.step…" or "Meshing 3 of 12 (coarse)…". */
export function progressText(p: ImportProgress, label: string): string {
  if (progressFraction(p) === null) return `Reading ${label}…`;
  return `Meshing ${Math.min(p.index + 1, p.count)} of ${p.count} (${p.pass})…`;
}

/** What to call an import: its file, else the file of a part under it, else the name of its top node. */
export function progressLabel(p: ImportProgress, parts: readonly PartNode[]): string {
  if (p.file) return p.file;
  const own = parts.find((part) => (part.path === p.root || part.path.startsWith(`${p.root}/`)) && part.source !== "");
  if (own) return basename(own.source);
  return basename(p.root);
}

// ---------------------------------------------------------------------------------------------- patch names
/**
 * The unique name of every drawn group, which the selection, hiding and isolating work with and
 * which is also what "Assign boundary" names the boundary after. A part with one group is named
 * after its node, so the viewport and the tree agree and the name is a valid node name; a part
 * whose mesh has several groups is named after those. Names are made unique in tree order, with
 * `_2`, `_3`, so they depend on the tree alone, not on which mesh arrived first, and a boundary made
 * today still means the same part tomorrow. A part without a mesh yet keeps its name free for when
 * it arrives.
 *
 * @param groupNames the transport group names of the parts whose mesh has several groups, by node id
 * @returns the names of the groups of every part, by node id
 */
export function assignPatchNames(nodes: readonly PartNode[], groupNames: ReadonlyMap<string, readonly string[]>): Map<string, string[]> {
  const used = new Set<string>();
  const unique = (base: string): string => {
    let name = base;
    for (let n = 2; used.has(name); n++) name = `${base}_${n}`;
    used.add(name);
    return name;
  };
  const names = new Map<string, string[]>();
  for (const node of nodes) {
    const groups = groupNames.get(node.id);
    names.set(node.id, groups !== undefined && groups.length > 1 ? groups.map((group) => unique(group)) : [unique(node.name)]);
  }
  return names;
}

// ---------------------------------------------------------------------------------------------- selection
/**
 * The patch a tree node stands for: a part's only patch, or a patch of the sample part that the
 * node shares its name with. Null for a tag, which has no single patch of its own (its row picks
 * its members instead), and for a part with several patches, which has no single one to select.
 */
export function patchForNode(node: NodeSummary | null, geometry: readonly PatchMeta[]): string | null {
  if (!node) return null;
  if (node.type === "part") {
    const own = geometry.filter((g) => g.part === node.id);
    return own.length === 1 ? own[0].name : null;
  }
  if (node.type === "tag") return null;
  return geometry.some((g) => g.part === null && g.name === node.name) ? node.name : null;
}
