/**
 * The project tree kept current from events (ADR-0019 step 8), as pure functions.
 *
 * A node event carries the summary of the node it names and, for a change that reached further
 * (an edited parameter that expressions read), the summaries of the others: `readPatches` turns
 * one into patches and `applyPatches` puts a batch of them into the tree, in one pass and with
 * structural sharing, so that what did not change stays the very same object. That is what keeps
 * the rows of the tree panel and every `useStore` selector on the tree from redoing work for the
 * 4,000 nodes an import did not touch.
 *
 * The tree is still nested (`children` arrays), as `node.tree` makes it and as every panel reads
 * it. Beside it a `TreeIndex` maps ids and paths to nodes and ids to parents, so that a patch finds
 * its node, and `findNode` its answer, without walking the tree. The index is shared by the
 * versions of the tree and always describes the newest; `findNode` checks the root before it
 * trusts it.
 *
 * A patch that cannot be applied (an added node whose parent this tree does not have) makes
 * `applyPatches` return null and leaves the tree and the index as they were: the caller fetches
 * the whole tree, which stays the way to load one and the answer to anything unexpected.
 */

import type { NodeSummary } from "../api/types";

// ---------------------------------------------------------------------------------------------- patches
/** One change to the tree. */
export type TreePatch =
  | {
      kind: "put";
      /** The node's own fields, as the server has them now; its children are the tree's. */
      node: NodeSummary;
      /** Where a node that is not in the tree yet goes. */
      parent: string | null;
      index: number | null;
      /** True for `node.added`: a node the tree lacks is put in. Otherwise it is only updated. */
      add: boolean;
      /** True when the event names the node: one the tree lacks means the tree is behind. Reach-along nodes (`also`) are skipped when unknown. */
      strict: boolean;
    }
  | { kind: "drop"; id: string };

/** What an event says about the tree. `refresh` asks for the whole tree to be fetched. */
export interface PatchRead {
  patches: TreePatch[];
  refresh: boolean;
}

/** Events that name a node by `node_id` and say what changed. */
const NODE_EVENTS: ReadonlySet<string> = new Set(["node.added", "node.changed", "node.removed"]);
/** Events that follow a change of a node's status that no node event announced (see `stokes/api/summaries.py`). */
const STATE_EVENTS: ReadonlySet<string> = new Set(["geometry.part_ready", "geometry.import_failed"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function whole(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** A node summary from the wire, or null when it is not one. Its children are dropped: an event says where a node goes, not what is below it. */
export function asNode(value: unknown): NodeSummary | null {
  if (!isRecord(value) || !isRecord(value.params)) return null;
  const { id, type, name, path, status } = value;
  if (typeof id !== "string" || typeof type !== "string" || typeof name !== "string" || typeof path !== "string" || typeof status !== "string") return null;
  const count = value.children_count;
  const node = { ...value, children_count: typeof count === "number" && Number.isFinite(count) ? Math.max(0, Math.trunc(count)) : 0 } as unknown as NodeSummary;
  delete node.children;
  return node;
}

/**
 * The patches an event carries, `{ patches: [], refresh: true }` for a node event that says too
 * little to patch with (no summary: a node that went, a server that does not send them), and null
 * for an event that is no business of the tree.
 */
export function readPatches(event: string, data: Record<string, unknown>): PatchRead | null {
  const isNode = NODE_EVENTS.has(event);
  if (!isNode && !STATE_EVENTS.has(event)) return null;
  const id = text(data.node_id);
  const patches: TreePatch[] = [];
  let refresh = data.resync === true;
  if (event === "node.removed") {
    if (id !== null) patches.push({ kind: "drop", id });
    else refresh = true;
  } else {
    const node = asNode(data.node);
    if (node !== null) patches.push({ kind: "put", node, parent: text(data.parent), index: whole(data.index), add: event === "node.added", strict: isNode });
    else if (isNode) refresh = true;
    else if (!refresh) return null; // a geometry event with no summary: the store decides what that needs
  }
  if (Array.isArray(data.also)) {
    for (const item of data.also) {
      const other = asNode(item);
      if (other !== null) patches.push({ kind: "put", node: other, parent: null, index: null, add: false, strict: false });
    }
  }
  return { patches, refresh };
}

// ---------------------------------------------------------------------------------------------- the index
/** Lookups over one tree. Shared by its versions: `root` is the newest. */
export interface TreeIndex {
  root: NodeSummary;
  byId: Map<string, NodeSummary>;
  /** The id of each node's parent; null for the root. */
  parentOf: Map<string, string | null>;
  /** By path; built when a path is first asked for, and dropped when a rename changes paths. */
  byPath: Map<string, NodeSummary> | null;
}

export function indexTree(root: NodeSummary): TreeIndex {
  const byId = new Map<string, NodeSummary>();
  const parentOf = new Map<string, string | null>();
  const visit = (node: NodeSummary, parent: string | null): void => {
    byId.set(node.id, node);
    parentOf.set(node.id, parent);
    for (const child of node.children ?? []) visit(child, node.id);
  };
  visit(root, null);
  return { root, byId, parentOf, byPath: null };
}

/** The index of the newest tree. `findNode` and `ancestorsOf` use it for the root it describes and walk for any other. */
let current: TreeIndex | null = null;

export function setTreeIndex(index: TreeIndex | null): void {
  current = index;
}

/** The index when it describes `root`, else null. */
export function indexFor(root: NodeSummary | null): TreeIndex | null {
  return current !== null && root !== null && current.root === root ? current : null;
}

/** The node at a path in the tree the index describes; built on first use, kept up by the patches. */
export function nodeAtIndexedPath(index: TreeIndex, path: string): NodeSummary | null {
  if (index.byPath === null) {
    const byPath = new Map<string, NodeSummary>();
    for (const node of index.byId.values()) byPath.set(node.path, node);
    index.byPath = byPath;
  }
  return index.byPath.get(path) ?? null;
}

/** The ancestors of a node from the first level down to its parent, or null when the node is the root or is not in the tree. */
export function ancestorChain(index: TreeIndex, id: string): NodeSummary[] | null {
  if (!index.byId.has(id) || id === index.root.id) return null;
  const chain: NodeSummary[] = [];
  for (let up = index.parentOf.get(id) ?? null; up !== null && up !== index.root.id; up = index.parentOf.get(up) ?? null) {
    const node = index.byId.get(up);
    if (node) chain.push(node);
  }
  return chain.reverse();
}

// ---------------------------------------------------------------------------------------------- applying
export interface Applied {
  /** Ids of the nodes whose own fields changed (patched, added, or renamed through an ancestor), with their new objects. */
  changed: NodeSummary[];
  /** True when a node was added or removed: the order and the set of nodes moved. */
  structural: boolean;
}

/** `list.splice(at, 0, id)` without the cost of one at the end. */
function insertAt(list: string[], at: number, id: string): void {
  if (at >= list.length) list.push(id);
  else list.splice(at, 0, id);
}

/**
 * Puts `patches`, in order, into the tree the index describes. On success the index describes the
 * new tree (`index.root`) and the result says what changed; the objects of everything that did
 * not change are the old ones. Returns null, changing nothing, when a patch cannot be applied.
 *
 * Cost: the patches, plus one pass over the children of each parent that had a child change (a
 * group of 4,500 parts is one pass of 4,500 map lookups however many of its parts a batch names),
 * plus the chain of ancestors up to the root.
 */
export function applyPatches(index: TreeIndex, patches: readonly TreePatch[]): Applied | null {
  // The batch works on overlays and touches the index only when every patch has applied.
  const nodes = new Map<string, NodeSummary | null>(); // own-field objects replaced in this batch; null: removed
  const kids = new Map<string, string[]>(); // child id lists changed in this batch
  const parents = new Map<string, string | null>(); // parents of the nodes added in it
  const dirty = new Set<string>(); // objects to rebuild: changed, or with a changed descendant
  const withChangedChild = new Set<string>();
  const changedIds = new Set<string>();
  const shrunk = new Set<string>();
  const removedPaths: string[] = [];
  let structural = false;
  let renamed = false;

  const get = (id: string): NodeSummary | null => (nodes.has(id) ? (nodes.get(id) ?? null) : (index.byId.get(id) ?? null));
  const parentOf = (id: string): string | null => (parents.has(id) ? (parents.get(id) ?? null) : (index.parentOf.get(id) ?? null));
  const peekKids = (id: string): readonly string[] => kids.get(id) ?? (get(id)?.children ?? []).map((c) => c.id);
  const ownKids = (id: string): string[] => {
    let list = kids.get(id);
    if (list === undefined) {
      list = (get(id)?.children ?? []).map((c) => c.id);
      kids.set(id, list);
    }
    return list;
  };
  /** Marks a node and the chain of its ancestors for rebuilding. */
  const touch = (id: string): void => {
    dirty.add(id);
    for (let up = parentOf(id); up !== null && !withChangedChild.has(up); up = parentOf(up)) {
      withChangedChild.add(up);
      dirty.add(up);
    }
  };

  /** A rename moves every path below the node. */
  const movePaths = (id: string, from: string, to: string): void => {
    const stack = [...peekKids(id)];
    while (stack.length > 0) {
      const below = stack.pop() as string;
      const node = get(below);
      if (node === null) continue;
      nodes.set(below, { ...node, path: to + node.path.slice(from.length) });
      changedIds.add(below);
      touch(below);
      for (const next of peekKids(below)) stack.push(next);
    }
  };

  for (const patch of patches) {
    if (patch.kind === "drop") {
      const gone = get(patch.id);
      const parent = parentOf(patch.id);
      if (gone === null || parent === null) continue; // not here, or the root: nothing to remove
      const list = ownKids(parent);
      const at = list.indexOf(patch.id);
      if (at >= 0) list.splice(at, 1);
      shrunk.add(parent);
      touch(parent);
      const stack = [patch.id];
      while (stack.length > 0) {
        const id = stack.pop() as string;
        const node = get(id);
        if (node === null) continue;
        for (const next of peekKids(id)) stack.push(next);
        removedPaths.push(node.path);
        nodes.set(id, null);
        changedIds.delete(id);
      }
      structural = true;
      continue;
    }

    const existing = get(patch.node.id);
    if (existing !== null) {
      const own = kids.get(existing.id)?.length ?? existing.children?.length ?? 0;
      const next: NodeSummary = { ...patch.node, children: existing.children, children_count: Math.max(patch.node.children_count, own) };
      if (next.path !== existing.path) {
        renamed = true;
        movePaths(existing.id, existing.path, next.path);
      }
      nodes.set(next.id, next);
      changedIds.add(next.id);
      touch(next.id);
    } else if (patch.add) {
      if (patch.parent === null || get(patch.parent) === null) return null; // the tree is behind: fetch it
      const list = ownKids(patch.parent);
      insertAt(list, patch.index ?? list.length, patch.node.id);
      nodes.set(patch.node.id, { ...patch.node, children: [] });
      kids.set(patch.node.id, []);
      parents.set(patch.node.id, patch.parent);
      changedIds.add(patch.node.id);
      touch(patch.node.id);
      structural = true;
    } else if (patch.strict) {
      return null; // a change to a node this tree does not have: it is behind
    }
  }

  // Rebuild what changed, the deepest first, so that a parent finds its children's new objects.
  const levels: string[][] = [];
  for (const id of dirty) {
    if (get(id) === null) continue;
    let depth = 0;
    for (let up = parentOf(id); up !== null; up = parentOf(up)) depth++;
    (levels[depth] ??= []).push(id);
  }
  const rebuilt = new Map<string, NodeSummary>();
  for (let depth = levels.length - 1; depth >= 0; depth--) {
    for (const id of levels[depth] ?? []) {
      const own = get(id) as NodeSummary;
      if (!withChangedChild.has(id) && !kids.has(id)) {
        rebuilt.set(id, own);
        continue;
      }
      const children = kids.has(id)
        ? (kids.get(id) as string[]).map((child) => rebuilt.get(child) ?? (get(child) as NodeSummary))
        : (own.children ?? []).map((child) => rebuilt.get(child.id) ?? child);
      const count = shrunk.has(id) ? children.length : Math.max(own.children_count, children.length);
      rebuilt.set(id, { ...own, children, children_count: count });
    }
  }

  // Everything applied: commit to the index.
  for (const [id, node] of nodes) {
    if (node === null) {
      index.byId.delete(id);
      index.parentOf.delete(id);
    }
  }
  for (const [id, parent] of parents) if (nodes.get(id) !== null) index.parentOf.set(id, parent);
  for (const [id, node] of rebuilt) index.byId.set(id, node);
  if (renamed) {
    index.byPath = null;
  } else if (index.byPath !== null) {
    for (const path of removedPaths) index.byPath.delete(path);
    for (const node of rebuilt.values()) index.byPath.set(node.path, node);
  }
  index.root = rebuilt.get(index.root.id) ?? index.root;
  const changed: NodeSummary[] = [];
  for (const id of changedIds) {
    const node = rebuilt.get(id);
    if (node !== undefined) changed.push(node);
  }
  return { changed, structural };
}
