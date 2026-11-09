/**
 * Keeps the scene in step with the boundary patches of the mesh (ADR-0024, T-211): one scene source
 * `mesh:<node id>` per `mesh_patch` node, the same sources the parts use, so the batches, the tint
 * attribute, picking and the section view treat a patch like any other group (its kind, `mesh`, has a
 * matte recipe in materials.ts).
 *
 * The tree says what exists (`meshPatchesOf`: id, name, colour, visibility, opacity); this class does what
 * that asks for. A patch the scene lacks is fetched with `/api/mesh/batch` (256 ids at a time) and parsed
 * by the geometry parser as it is, one group of kind `mesh` whose face ids are all 0, and a patch that
 * left the tree is taken out. A changed colour, visibility, opacity or name restyles the source without
 * a fetch. The cell edges of "Mesh with cells" are fetched once per patch, only while that display is
 * chosen, and drawn by the scene in its feature-edge material.
 *
 * What is on screen is a view filter (`setLayerFilter`): the mesh while the Mesh stage is active (or the
 * Results stage with a field on it, T-218) and a patch is there to draw, the geometry parts otherwise. It is scene visibility only: no part parameter
 * is written, nothing is journaled and nothing is undone. A patch whose surfaces the server does not have
 * (404, or a refused request) counts as absent for the filter, so a Mesh stage whose mesh cannot be drawn
 * keeps showing the geometry instead of an empty view.
 *
 * The scene is not touched by `dispose`: its owner tears it down, as `PartsController` leaves it.
 */

import { GeometryHttpError, type StokesClient } from "../api/client";
import { bareMeshOf, hasImportedCase, layerFilter, meshPatchesOf, meshPatchName, meshSourceOf, meshIdOfSource, patchStyleKey, planMeshSync, type MeshPatchEntry } from "../state/meshPatches";
import { useStore, type StokesState } from "../state/store";
import { resultsShown } from "../panels/fieldsModel";
import { parseGeometry } from "./geometry";
import { parseMeshEdges } from "./meshEdges";
import type { PartStyle, SceneManager } from "./scene";

/** Patches asked for in one batch request (the server refuses more than 512). */
const BATCH_SIZE = 256;
/** Batch requests in flight at once. */
const MAX_BATCHES = 2;
/** Edge requests in flight at once. */
const MAX_EDGE_FETCHES = 4;

type EdgeState = "none" | "loading" | "drawn" | "missing";

interface Slot {
  entry: MeshPatchEntry;
  /** The name the scene draws the patch under, so a rename can follow. */
  name: string;
  /** The style key it was last drawn with (`patchStyleKey`). */
  style: string;
  /** The surfaces are in the scene. */
  loaded: boolean;
  /** The server had no surfaces, or they could not be read: not asked for again until the patch is new. */
  failed: boolean;
  request: AbortController | null;
  edges: EdgeState;
  edgeRequest: AbortController | null;
}

/** A field colours the mesh in the Results stage (T-218): the mesh then replaces the geometry there, as it does in the Mesh stage. */
function resultsOnOf(state: StokesState): boolean {
  return resultsShown(state.stage, state.results.view, state.results.data !== null);
}

/** The held results carry a slice: for a project without patches, its whole mesh view. */
function hasSlice(state: StokesState): boolean {
  return state.results.data?.slice === true;
}

function styleOf(entry: MeshPatchEntry): PartStyle {
  return { color: entry.color, visible: entry.visible, opacity: entry.opacity };
}

export class MeshPatchesController {
  private readonly slots = new Map<string, Slot>();
  private readonly queue: string[] = [];
  private batches = 0;
  private edgeFetches = 0;
  private entries: { tree: StokesState["tree"]; list: MeshPatchEntry[] } | null = null;
  private readonly unsubscribe: () => void;
  private disposed = false;
  /** The scene's leftovers from an earlier controller have been looked at. */
  private adopted = false;
  /**
   * The "not cached in this project" line has been said for the set of patches now on screen.
   *
   * **Once per project, not once per patch** (claude-win2's 1230 review): the message is about the
   * PROJECT's missing surface cache, and three patches made it print three times - which reads as
   * three problems. Reset when the patch set changes, so opening another project says it again,
   * because that is a different project with its own cache.
   */
  private saidNotCached = false;

  constructor(
    private readonly client: StokesClient,
    private readonly scene: SceneManager,
  ) {
    this.unsubscribe = useStore.subscribe((state, previous) => {
      if (state.tree !== previous.tree || state.stage !== previous.stage || state.meshDisplay !== previous.meshDisplay || resultsOnOf(state) !== resultsOnOf(previous) || hasSlice(state) !== hasSlice(previous)) this.sync(state);
    });
    this.sync(useStore.getState());
  }

  dispose(): void {
    this.disposed = true;
    this.unsubscribe();
    for (const slot of this.slots.values()) {
      slot.request?.abort();
      slot.edgeRequest?.abort();
    }
    this.slots.clear();
    this.queue.length = 0;
  }

  // ------------------------------------------------------------------ reconcile
  private patchList(state: StokesState): MeshPatchEntry[] {
    if (this.entries?.tree !== state.tree) this.entries = { tree: state.tree, list: meshPatchesOf(state.tree) };
    return this.entries.list;
  }

  private sync(state: StokesState): void {
    if (this.disposed) return;
    const list = this.patchList(state);
    const held = new Map<string, string>();
    for (const [id, slot] of this.slots) held.set(id, slot.style);
    const plan = planMeshSync(list, held);

    for (const id of plan.remove) this.forget(id);
    // **A different patch set is a different occasion to say it.** `plan.fetch` is what tells us the
    // list moved on; without this a second project's missing cache would be silent, which is the
    // opposite failure.
    if (plan.fetch.length > 0) this.saidNotCached = false;
    for (const entry of list) {
      const slot = this.slots.get(entry.id);
      if (slot === undefined) {
        this.slots.set(entry.id, { entry, name: meshPatchName(entry), style: patchStyleKey(entry), loaded: false, failed: false, request: null, edges: "none", edgeRequest: null });
        this.queue.push(entry.id);
      } else {
        slot.entry = entry;
      }
    }
    for (const id of plan.restyle) this.restyle(id);
    if (!this.adopted) {
      // A scene that outlived the last controller (the client changed) may hold patches the tree no longer has: removed, once.
      this.adopted = true;
      for (const meta of this.scene.patches()) {
        const id = meshIdOfSource(meta.part);
        if (id !== null && meta.part !== null && !this.slots.has(id)) this.scene.removeSource(meta.part);
      }
    }

    this.applyViews(state);
    this.pump();
    this.pumpEdges(state);
  }

  /** The view filter and the cell-edge switch, from the stage, the display choice and what could be loaded. */
  private applyViews(state: StokesState): void {
    const usable = [...this.slots.values()].some((slot) => !slot.failed);
    this.scene.setLayerFilter(layerFilter(state.stage, usable, resultsOnOf(state), bareMeshOf(this.slots.size, hasSlice(state), hasImportedCase(state.tree))));
    this.scene.setCellEdgesShown(state.meshDisplay === "cells");
  }

  private forget(id: string): void {
    const slot = this.slots.get(id);
    slot?.request?.abort();
    slot?.edgeRequest?.abort();
    this.slots.delete(id);
    const queued = this.queue.indexOf(id);
    if (queued >= 0) this.queue.splice(queued, 1);
    const source = meshSourceOf(id);
    if (this.scene.hasSource(source)) this.scene.removeSource(source);
  }

  /** A changed colour, visibility, opacity or name: the source follows, with no fetch. */
  private restyle(id: string): void {
    const slot = this.slots.get(id);
    if (slot === undefined) return;
    const key = patchStyleKey(slot.entry);
    if (key === slot.style) return;
    slot.style = key;
    const source = meshSourceOf(id);
    if (!this.scene.hasSource(source)) return;
    this.scene.setSourceStyle(source, styleOf(slot.entry));
    const name = meshPatchName(slot.entry);
    if (name !== slot.name) {
      const renames = new Map([[slot.name, name]]);
      slot.name = name;
      this.scene.renamePatches(renames);
      useStore.getState().renamePatches(renames);
    }
  }

  // ------------------------------------------------------------------ the surfaces
  private pump(): void {
    while (!this.disposed && this.batches < MAX_BATCHES && this.queue.length > 0) {
      const ids = this.queue.splice(0, BATCH_SIZE).filter((id) => {
        const slot = this.slots.get(id);
        return slot !== undefined && !slot.loaded && !slot.failed && slot.request === null;
      });
      if (ids.length > 0) void this.fetchBatch(ids);
    }
  }

  private async fetchBatch(ids: readonly string[]): Promise<void> {
    const request = new AbortController();
    for (const id of ids) {
      const slot = this.slots.get(id);
      if (slot !== undefined) slot.request = request;
    }
    this.batches++;
    try {
      const replies = await this.client.meshBatch(ids, request.signal);
      if (this.disposed || request.signal.aborted) return;
      for (const id of ids) {
        const slot = this.slots.get(id);
        if (slot === undefined || slot.request !== request) continue;
        slot.request = null;
        this.apply(slot, replies.get(id) ?? null);
      }
    } catch (err) {
      if (this.disposed || request.signal.aborted) return;
      const status = err instanceof GeometryHttpError ? ` (HTTP ${err.status})` : "";
      useStore.getState().log("error", `mesh patches${status}: ${(err as Error).message}`);
      for (const id of ids) {
        const slot = this.slots.get(id);
        if (slot !== undefined && slot.request === request) {
          slot.request = null;
          slot.failed = true;
        }
      }
    } finally {
      this.batches--;
      if (!this.disposed) {
        const state = useStore.getState();
        this.applyViews(state);
        this.pump();
        this.pumpEdges(state);
      }
    }
  }

  /** Puts a fetched patch into the scene, or marks it failed: one unreadable patch costs no other. */
  private apply(slot: Slot, buffer: ArrayBuffer | null): void {
    if (buffer === null) {
      // The server has no surfaces for it: a reopened project whose cache file is gone.
      //
      // **`info`, not `error`.** The message was logged as an error, so opening the demo put a red
      // dot and a count on the Console tab - and the owner read that, correctly, as "something is
      // broken". Nothing is: the mesh result is intact and the fix is one button, which the message
      // already names. A red badge is a claim about the app, and this is not one.
      //
      // The `generate the mesh again` half is kept verbatim: it is the instruction, and the spec
      // wants a failure to say what to do in plain words.
      slot.failed = true;
      if (!this.saidNotCached) {
        this.saidNotCached = true;
        useStore.getState().log(
          "info",
          `mesh patches are not cached in this project: press Generate to draw them again`,
        );
      }
      return;
    }
    try {
      const parsed = parseGeometry(buffer);
      slot.name = meshPatchName(slot.entry);
      slot.style = patchStyleKey(slot.entry);
      const source = meshSourceOf(slot.entry.id);
      this.scene.setSource(source, parsed.groups, [slot.name], styleOf(slot.entry));
      // A patch that has just arrived carries no field colouring: T-218 sets one through this hook.
      this.scene.setSourceVertexTint(source, null);
      slot.loaded = true;
    } catch (err) {
      slot.failed = true;
      useStore.getState().log("error", `mesh patch ${slot.entry.name}: ${(err as Error).message}`);
    }
  }

  // ------------------------------------------------------------------ the cell edges
  /** Fetches the cell edges of loaded patches, once each, while "Mesh with cells" is chosen. */
  private pumpEdges(state: StokesState): void {
    if (this.disposed || state.meshDisplay !== "cells") return;
    for (const slot of this.slots.values()) {
      if (this.edgeFetches >= MAX_EDGE_FETCHES) return;
      if (slot.loaded && slot.edges === "none") void this.fetchEdges(slot);
    }
  }

  private async fetchEdges(slot: Slot): Promise<void> {
    const request = new AbortController();
    slot.edges = "loading";
    slot.edgeRequest = request;
    this.edgeFetches++;
    try {
      const buffer = await this.client.meshEdges(slot.entry.id, request.signal);
      if (this.disposed || request.signal.aborted) return;
      if (buffer === null) {
        slot.edges = "missing";
        return;
      }
      this.scene.setSourceCellEdges(meshSourceOf(slot.entry.id), parseMeshEdges(buffer));
      slot.edges = "drawn";
    } catch (err) {
      if (this.disposed || request.signal.aborted) return;
      slot.edges = "missing";
      useStore.getState().log("error", `mesh edges ${slot.entry.name}: ${(err as Error).message}`);
    } finally {
      this.edgeFetches--;
      if (slot.edgeRequest === request) slot.edgeRequest = null;
      if (!this.disposed) this.pumpEdges(useStore.getState());
    }
  }
}
