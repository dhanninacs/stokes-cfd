/**
 * Keeps the scene in step with the store: one source per project part, loaded by node id.
 * Empty projects and imports never substitute example geometry.
 *
 * The store says what exists (`partNodes`, from the tree), what the server announced about each
 * mesh (`parts`: a revision that moves whenever the mesh changed or went away) and which colour,
 * visibility and opacity each part has. This class does what that asks for: it fetches the meshes
 * whose revision moved (never twice at once for one part), replaces the part's meshes in the scene,
 * drops them when the part has none or is gone from the tree, applies colour, visibility and opacity
 * without a fetch, and keeps the patch names unique and in step with the tree.
 *
 * Fetching (ADR-0019): a few queued parts are fetched one at a time from `/api/geometry/{id}`; many are
 * fetched in batches of up to BATCH_SIZE from `/api/geometry/batch`, two batches at a time, alongside
 * whatever single fetches are under way. Either way the revision asked for is recorded before the
 * request goes out, so the part is not queued again meanwhile, and a part that leaves stops waiting
 * for its request (a request nobody waits for is aborted). A batch that fails puts its parts back in
 * the queue to be fetched on their own, so one bad part or a refused request costs no other part.
 * The parts of a batch reply go into the scene a slice at a time, letting the page draw between.
 *
 * The scene is told nothing it did not ask for: a mesh that arrives for a part the tree no longer
 * has is dropped on the floor, and a 404 or a batch's no-mesh marker (the server has no mesh for the
 * part yet) is not an error, because the server announces the mesh when there is one.
 */

import { GeometryHttpError, type StokesClient } from "../api/client";
import { assignPatchNames, type PartNode } from "../state/parts";
import { useStore, type StokesState } from "../state/store";
import { parseGeometry, type ParsedGeometry } from "./geometry";
import { type PartStyle, type SceneManager } from "./scene";

/** Single meshes fetched at once. They come from the local server, which answers from a thread pool. */
const MAX_FETCHES = 6;
/** At least this many parts waiting go to the batch endpoint (ADR-0019); fewer are fetched one at a time. */
const BATCH_THRESHOLD = 10;
/**
 * Parts asked for in one batch request (the server refuses more than 512).
 *
 * Capped so the reply stays a few MB: the whole 4,500-part queue in one request is a 35 MB reply
 * that has to arrive in full before a single part appears, and a ~49 KB query string. The rest of the
 * queue follows in the next requests.
 */
const BATCH_SIZE = 256;
/** Batch requests in flight at once: the server answers one while the reply of the other goes into the scene. */
const MAX_BATCHES = 4;
/** Longest stretch, in ms, that the parts of a batch reply go into the scene before the page may draw and take input. */
const APPLY_SLICE = 12;

/**
 * When the load reached each stage, in the page's own clock. The loader owns the time between the
 * page arriving and every part being drawn, and the bench needs to know which half that is: the
 * server answers the mesh requests for the 4,500-part benchmark in well under a second, so the
 * figure the browser reports has to be attributed here rather than guessed at.
 */
const marks: {
  tree: number | null;
  firstMesh: number | null;
  allMeshes: number | null;
  applied: number;
  /** Total time spent awaiting the server for meshes, and how many requests that was. */
  fetchMs: number;
  batches: number;
  singles: number;
} = {
  tree: null,
  firstMesh: null,
  allMeshes: null,
  applied: 0,
  fetchMs: 0,
  batches: 0,
  singles: 0,
};

if (typeof window !== "undefined") {
  (window as unknown as { __stokesLoad?: unknown }).__stokesLoad = {
    marks,
    reset: () => {
      marks.tree = null;
      marks.firstMesh = null;
      marks.allMeshes = null;
      marks.applied = 0;
      marks.fetchMs = 0;
      marks.batches = 0;
      marks.singles = 0;
    },
  };
}

/** One request for meshes: a single part's, or a batch's. */
interface Request {
  abort: AbortController;
  /** The parts still waiting for it. A part that leaves stops waiting; a request nobody waits for is aborted. */
  waiting: Set<string>;
}

interface Slot {
  id: string;
  /** The highest revision a request was made for; set when the request goes out. */
  requested: number;
  /** The request under way for this part, if any: a part is never fetched twice at once. */
  request: Request | null;
  /** The colour, visibility and opacity the scene shows, to tell when they changed. */
  style: string;
  /** Fetch on its own next time: the last batch that asked for it failed. */
  single: boolean;
}


function styleOf(node: PartNode): PartStyle {
  // **`simulate` travels with the other display facts** (T-222): the viewport fades a body the user
  // left out, and it has to be told, because the scene never reads the model.
  return { color: node.color, visible: node.visible, opacity: node.opacity, simulate: node.simulate };
}

function styleKey(style: PartStyle): string {
  /*
    **`simulate` is part of the key, and leaving it out was the bug that made the first picture
    useless**: `setSourceStyle` is called only when the key CHANGES, so a part whose exclusion is not
    in the key keeps the style it already had - the tree showed the slashed `⚡` and the body stayed
    solid. A property that affects the drawing belongs in the key that decides whether to redraw.
  */
  return `${style.color ?? ""}|${style.visible}|${style.opacity}|${style.simulate !== false}`;
}

/** Lets the page draw and take input before a long job goes on (a message, so no timer clamping). */
function yieldToPage(): Promise<void> {
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = () => {
      channel.port1.close();
      resolve();
    };
    channel.port2.postMessage(null);
  });
}

export class PartsController {
  private readonly slots = new Map<string, Slot>();
  /** Parts whose revision is ahead of what was requested, in the order they were noticed. */
  private readonly queue = new Set<string>();
  /**
   * The parts whose mesh has several groups, with the groups' names. A part with one group is named
   * after its node, which needs nothing from the mesh, so only these can change anyone's name.
   */
  private readonly multi = new Map<string, readonly string[]>();
  private multiVersion = 0;
  private names: { nodes: readonly PartNode[]; version: number; byPart: Map<string, string[]> } | null = null;
  private nodeIndex: { nodes: readonly PartNode[]; byId: Map<string, PartNode> } | null = null;
  private readonly unsubscribe: () => void;
  private lastNodes: readonly PartNode[] | null = null;
  /** Single fetches under way. */
  private inflight = 0;
  /** Batch requests under way. */
  private batches = 0;
  private disposed = false;

  constructor(
    private readonly client: StokesClient,
    private readonly scene: SceneManager,
  ) {
    this.unsubscribe = useStore.subscribe((state, previous) => {
      if (state.tree !== previous.tree || state.partNodes !== previous.partNodes || state.parts !== previous.parts) this.sync(state);
    });
    this.sync(useStore.getState());
  }

  dispose(): void {
    this.disposed = true;
    this.unsubscribe();
    for (const slot of this.slots.values()) slot.request?.abort.abort();
    this.slots.clear();
    this.queue.clear();
    // The scene is torn down by its owner; what it drew is no longer there to select.
    useStore.getState().setGeometry([]);
  }

  // ------------------------------------------------------------------ reconcile
  private sync(state: StokesState): void {
    if (this.disposed) return;
    if (state.tree !== null && marks.tree === null) marks.tree = performance.now();
    this.syncParts(state);
    this.pump();
    this.markComplete();
  }

  /**
   * Stamps the moment the load finished, if every part the tree names has a mesh now.
   *
   * Called where the count moves as well as from `sync`: the last batch to arrive does not
   * necessarily change the store afterwards, and a mark that is only checked on a store change
   * then never fires — the 4,500-part bench has seen exactly that. It is the bench's stop
   * condition, so it is set from the event that satisfies it.
   */
  private markComplete(): void {
    const { partNodes } = useStore.getState();
    if (marks.allMeshes === null && partNodes.length > 0 && marks.applied >= partNodes.length) {
      marks.allMeshes = performance.now();
    }
  }

  private syncParts(state: StokesState): void {
    const { parts, partNodes } = state;
    for (const id of [...this.slots.keys()]) if (!(id in parts)) this.forget(id);
    for (const entry of Object.values(parts)) {
      let slot = this.slots.get(entry.id);
      if (!slot) {
        slot = { id: entry.id, requested: 0, request: null, style: "", single: false };
        this.slots.set(entry.id, slot);
      }
      if (!entry.present) {
        // No mesh: an undone import, or one that failed. Whatever is drawn or on its way is stale.
        this.cancel(slot);
        this.queue.delete(entry.id);
        slot.requested = Math.max(slot.requested, entry.revision);
        if (this.scene.hasSource(entry.id)) this.release(slot);
      } else if (entry.revision > slot.requested) {
        this.queue.add(entry.id);
      }
    }
    // The part nodes changed: colour, visibility and opacity may have, and so may names (a rename,
    // another part with the same name arriving). None needs a new mesh.
    if (partNodes !== this.lastNodes) {
      this.lastNodes = partNodes;
      for (const node of partNodes) {
        const slot = this.slots.get(node.id);
        if (!slot || !this.scene.hasSource(node.id)) continue;
        const style = styleOf(node);
        const key = styleKey(style);
        if (key === slot.style) continue;
        slot.style = key;
        this.scene.setSourceStyle(node.id, style);
      }
      this.syncNames(partNodes);
    }
  }

  /** A part left the tree: stop asking for it and take its meshes out of the scene. */
  private forget(id: string): void {
    const slot = this.slots.get(id);
    if (slot) this.cancel(slot);
    this.slots.delete(id);
    this.queue.delete(id);
    this.setMulti(id, []);
    if (this.scene.hasSource(id)) this.scene.removeSource(id);
  }

  private release(slot: Slot): void {
    this.scene.removeSource(slot.id);
    this.setMulti(slot.id, []);
    slot.style = "";
  }

  /** The part no longer waits for its request; a request that nobody waits for any more is aborted. */
  private cancel(slot: Slot): void {
    const request = slot.request;
    if (!request) return;
    slot.request = null;
    request.waiting.delete(slot.id);
    if (request.waiting.size === 0) request.abort.abort();
  }

  // ------------------------------------------------------------------ fetching
  /**
   * Starts requests for the queued parts: batches while at least BATCH_THRESHOLD parts can go in one
   * (up to MAX_BATCHES at a time, single fetches or not), single fetches for the rest, up to
   * MAX_FETCHES at a time.
   */
  private pump(): void {
    if (this.disposed) return;
    const { parts } = useStore.getState();
    const batchable: string[] = [];
    for (const id of this.queue) {
      const slot = this.slots.get(id);
      const entry = parts[id];
      if (!slot || !entry || !entry.present || entry.revision <= slot.requested) {
        this.queue.delete(id);
        continue;
      }
      // In flight: it is queued again when that lands, if the revision moved on meanwhile.
      if (slot.request !== null) continue;
      if (!slot.single) batchable.push(id);
    }
    while (this.batches < MAX_BATCHES && batchable.length >= BATCH_THRESHOLD) {
      const ids = batchable.splice(0, BATCH_SIZE);
      for (const id of ids) this.queue.delete(id);
      void this.fetchBatch(ids);
    }
    // Enough left for a batch: they wait for one rather than trickle out a few at a time.
    const waiting = batchable.length >= BATCH_THRESHOLD ? new Set(batchable) : null;
    for (const id of this.queue) {
      if (this.inflight >= MAX_FETCHES) return;
      if (waiting?.has(id)) continue;
      const slot = this.slots.get(id);
      const entry = parts[id];
      if (!slot || !entry || slot.request !== null) continue;
      this.queue.delete(id);
      void this.fetch(slot, entry.revision);
    }
  }

  /** Fetches many parts in one request (ADR-0019). `ids` come from `pump`: queued, present, none in flight. */
  private async fetchBatch(ids: readonly string[]): Promise<void> {
    const { parts } = useStore.getState();
    const request: Request = { abort: new AbortController(), waiting: new Set() };
    // What each part had asked for before, to go back to if this request fails.
    const before = new Map<string, number>();
    for (const id of ids) {
      const slot = this.slots.get(id);
      const entry = parts[id];
      if (!slot || !entry) continue;
      before.set(id, slot.requested);
      // Before the request, as for a single fetch: the part is not queued again while this is under way.
      slot.requested = entry.revision;
      slot.request = request;
      request.waiting.add(id);
    }
    if (request.waiting.size === 0) return;
    this.batches++;
    let failed = false;
    try {
      const fetchStart = performance.now();
      const replies = await this.client.fetchGeometryBatch([...request.waiting], request.abort.signal);
      marks.fetchMs += performance.now() - fetchStart;
      marks.batches += 1;
      let slice = performance.now();
      for (const [id, buffer] of replies) {
        if (this.disposed || request.abort.signal.aborted) return;
        const slot = this.slots.get(id);
        // Gone from the tree, or no longer waiting for this request.
        if (!slot || slot.request !== request) continue;
        slot.request = null;
        request.waiting.delete(id);
        // No mesh yet: the server announces it (a new revision) when there is one.
        if (buffer === null) continue;
        try {
          this.apply(slot, parseGeometry(buffer));
          marks.applied += 1;
          if (marks.firstMesh === null) marks.firstMesh = performance.now();
          this.markComplete();
        } catch (err) {
          // One unreadable part must not cost the rest of the batch.
          useStore.getState().log("error", `geometry ${this.nameOf(id)}: ${(err as Error).message}`);
        }
        if (performance.now() - slice > APPLY_SLICE) {
          await yieldToPage();
          slice = performance.now();
        }
      }
    } catch (err) {
      if (this.disposed || request.abort.signal.aborted) return;
      failed = true;
      useStore.getState().log("error", `geometry batch of ${ids.length}: ${(err as Error).message}; fetching them one by one`);
    } finally {
      this.batches--;
      if (!this.disposed) this.settleBatch(request, before, failed);
    }
  }

  /**
   * After a batch: a part it did not answer (the request failed, or the reply left the part out) goes
   * back to what it had asked for before and is fetched on its own, and a part whose revision moved
   * while the request was under way is queued again.
   */
  private settleBatch(request: Request, before: ReadonlyMap<string, number>, failed: boolean): void {
    const { parts } = useStore.getState();
    for (const id of request.waiting) {
      const slot = this.slots.get(id);
      if (!slot || slot.request !== request) continue;
      slot.request = null;
      slot.requested = Math.min(slot.requested, before.get(id) ?? 0);
      slot.single = true;
      if (!failed) useStore.getState().log("error", `geometry ${this.nameOf(id)}: missing from the batch reply; fetching it on its own`);
    }
    request.waiting.clear();
    for (const id of before.keys()) {
      const slot = this.slots.get(id);
      const entry = parts[id];
      if (slot && slot.request === null && entry?.present && entry.revision > slot.requested) this.queue.add(id);
    }
    this.pump();
  }

  private async fetch(slot: Slot, revision: number): Promise<void> {
    const request: Request = { abort: new AbortController(), waiting: new Set([slot.id]) };
    slot.request = request;
    slot.requested = revision;
    this.inflight++;
    try {
      const singleStart = performance.now();
      const single = await this.client.fetchGeometry(slot.id, request.abort.signal);
      marks.fetchMs += performance.now() - singleStart;
      marks.singles += 1;
      const parsed = parseGeometry(single);
      if (this.disposed || request.abort.signal.aborted) return;
      this.apply(slot, parsed);
      slot.single = false;
    } catch (err) {
      if (this.disposed || request.abort.signal.aborted) return;
      // 404: the server has no mesh for this part yet. It says so with part_ready when it has.
      if (err instanceof GeometryHttpError && err.status === 404) return;
      useStore.getState().log("error", `geometry ${this.nameOf(slot.id)}: ${(err as Error).message}`);
    } finally {
      if (slot.request === request) slot.request = null;
      this.inflight--;
      if (!this.disposed) {
        const entry = useStore.getState().parts[slot.id];
        if (this.slots.get(slot.id) === slot && entry?.present && entry.revision > slot.requested) this.queue.add(slot.id);
        this.pump();
      }
    }
  }

  /** Puts a fetched mesh into the scene, in place of the part's earlier (coarse) one. */
  private apply(slot: Slot, parsed: ParsedGeometry): void {
    const { partNodes } = useStore.getState();
    const node = this.nodesById(partNodes).get(slot.id);
    if (!node) return; // the tree has already dropped the part
    const groups = parsed.groups.map((g) => g.name);
    const wasMulti = this.multi.has(slot.id);
    this.setMulti(slot.id, groups);
    const style = styleOf(node);
    slot.style = styleKey(style);
    this.scene.setSource(slot.id, parsed.groups, this.namesFor(partNodes).get(slot.id) ?? groups, style);
    // Only a part with several groups can move another part's name; the usual one cannot.
    if (wasMulti || groups.length > 1) this.syncNames(partNodes);
  }

  private nameOf(id: string): string {
    return this.nodesById(useStore.getState().partNodes).get(id)?.name ?? id;
  }

  /** The part nodes by id, remembered while the list is the same: a 4,500-part load looks each one up. */
  private nodesById(partNodes: readonly PartNode[]): Map<string, PartNode> {
    if (this.nodeIndex?.nodes !== partNodes) this.nodeIndex = { nodes: partNodes, byId: new Map(partNodes.map((n) => [n.id, n])) };
    return this.nodeIndex.byId;
  }

  // ------------------------------------------------------------------ patch names
  private setMulti(id: string, groups: readonly string[]): void {
    if (groups.length > 1) {
      this.multi.set(id, groups);
      this.multiVersion++;
    } else if (this.multi.delete(id)) {
      this.multiVersion++;
    }
  }

  /** The patch names the tree dictates, remembered while neither the nodes nor the multi-group parts change. */
  private namesFor(partNodes: readonly PartNode[]): Map<string, string[]> {
    const cached = this.names;
    if (cached && cached.nodes === partNodes && cached.version === this.multiVersion) return cached.byPart;
    const byPart = assignPatchNames(partNodes, this.multi);
    this.names = { nodes: partNodes, version: this.multiVersion, byPart };
    return byPart;
  }

  /**
   * Gives every drawn part the name the tree dictates (see `assignPatchNames`). A name changes when
   * the node is renamed or another part with the same name comes or goes; the scene and the store
   * follow with the old name as the key, so the selection does not lose its patch.
   */
  private syncNames(partNodes: readonly PartNode[]): void {
    const wanted = this.namesFor(partNodes);
    const renames = new Map<string, string>();
    for (const slot of this.slots.values()) {
      const names = wanted.get(slot.id);
      if (!names || !this.scene.hasSource(slot.id)) continue;
      const current = this.scene.patchNames(slot.id);
      for (let i = 0; i < names.length && i < current.length; i++) if (current[i] !== names[i]) renames.set(current[i], names[i]);
    }
    if (renames.size === 0) return;
    this.scene.renamePatches(renames);
    useStore.getState().renamePatches(renames);
  }
}
