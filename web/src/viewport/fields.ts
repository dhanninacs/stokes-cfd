/**
 * Colours the mesh with the results (T-218): the one place where a field reaches the scene. It reads the
 * `results` slice of the store (what `run.fields` said, the field, the range, the slice switch), the mesh
 * patches of the tree and the stage, fetches what it needs and does three things through the hooks T-211
 * built and nothing else:
 *
 * * `GET /api/results/field/{run}/{patch node id}?field=` once per run, patch and field (the arrays are
 *   kept while the results are the same: a range change recolours from them and fetches nothing), at most
 *   four in flight, the largest patch first; each is parsed by `state/fieldCodec.ts` and becomes
 *   `scene.setSourceVertexTint("mesh:<node id>", rgba)`, RGBA8 per vertex from the colour map, set AFTER the
 *   patch is in the scene (the mesh controller clears a tint when a patch loads, so a patch that arrives
 *   later is coloured when the scene reports it). A patch the server has no field for (404 or 400) keeps its
 *   own colour: its tint is `null`, never zeros;
 * * the mid-plane slice as one more mesh source, `mesh:slice`: `GET /api/results/slice/{run}` is transport
 *   version 2, read by `parseGeometry` like a patch, its cell edges are `GET /api/results/slice-edges/{run}`
 *   (the edge codec), and its field is the `slice` target of the field route, tinted like a patch;
 * * the slices the user cut (`state.slices`, any plane) are `SliceLayer`'s (sliceLayer.ts): this controller
 *   hands it the store, the results and the wall patches each time it syncs;
 * * leaving the Results stage, choosing no field, dropping the results or turning the slice off clears the
 *   tint (`null`) and removes the slice;
 * * the slice lies inside a closed body, so while it is in the scene the Section view cuts the patches at its
 *   plane (the half between the camera and the slice goes, the plane a hair past the slice so the slice is not
 *   itself clipped): the cut face is the slice. The user's own section is remembered and comes back when the
 *   slice goes (`engageSliceCut` and `releaseSliceCut` in `state/sectionState.ts`).
 *
 * `dispose` takes the tints off the patches it coloured; the scene itself is torn down by its owner.
 */

import type { StokesClient } from "../api/client";
import { legendOf } from "../panels/legendModel";
import { effectiveRange, fetchOrder, patchTint, resultsShown, SLICE_NAME, SLICE_SOURCE, type Range } from "../panels/fieldsModel";
import { reducedForTint, requestKeyOf, requestedFieldOf } from "./sliceValues";
import { fetchResultBytes, fieldRoute, sliceEdgesRoute, sliceRoute, SLICE_TARGET } from "../panels/resultsApi";
import { parseField, type FieldValues } from "../state/fieldCodec";
import { bareMeshOf, hasImportedCase, MESH_SOURCE_PREFIX, meshPatchesOf, meshSourceOf, type MeshPatchEntry } from "../state/meshPatches";
import { engageSliceCut, forgetSaved, midSliceBuried, releaseSliceCut, sliceSection, slicePlaneOf, type SliceCut } from "../state/sectionState";
import { useStore, type StokesState } from "../state/store";
import { patchLoading } from "../state/loadingState";
import { findSolverSettings } from "../panels/runPanelModel";
import { parseGeometry, type LoadedGroup, type PatchMeta } from "./geometry";
import { parseMeshEdges } from "./meshEdges";
import type { PartStyle, SceneManager } from "./scene";
import { SliceLayer } from "./sliceLayer";

/** Field requests in flight at once. */
const MAX_FETCHES = 4;

const SLICE_STYLE: PartStyle = { color: "#9aa3ad", visible: true, opacity: 1, unlit: true };

/** What a source was last coloured with: the scene's record of it and the settings of the colouring. */
interface Applied {
  meta: PatchMeta;
  key: string;
}

/** One surface the fields are drawn on: a mesh patch, or the slice. */
interface Surface {
  /** The target of the field route: the node id, or `slice`. */
  target: string;
  source: string;
  faces: number;
  name: string;
}

type Cached = FieldValues | null | "loading";

type SliceGeometry = { seq: number; state: "loading" | "missing" | "ready"; groups: LoadedGroup[]; edges: Float32Array | null };

/** The controller of the viewport now, so the Looks card can reach its slice's cut (`forgetSavedSection`). */
let live: FieldsController | null = null;

/** Drops the section the slice's cut remembers for the user; nothing when no controller is live. */
export function forgetSavedSection(): void {
  live?.forgetSavedSection();
}

export class FieldsController {
  private readonly applied = new Map<string, Applied>();
  private readonly cache = new Map<string, Cached>();
  private entries: { tree: StokesState["tree"]; list: MeshPatchEntry[] } | null = null;
  private slice: SliceGeometry | null = null;
  /** The slices the user cut, at any plane. */
  private readonly slices: SliceLayer;
  /** The section the slice set and the user's own, while the slice cuts the patches. */
  private cut: SliceCut | null = null;
  /** The slice could not be cut (its plane is not along an axis): nothing to restore. */
  private noCut = false;
  /** The Looks asked to forget the user's section before the slice's cut had engaged: the cut keeps none (`forgetSavedSection`). */
  private forgetNext = false;
  private inflight = 0;
  private generation = new AbortController();
  private seen = -1;
  private readonly warned = new Set<string>();
  /** The mid-plane slice's cell edges are in the scene. */
  private sliceEdgesDrawn = false;
  private readonly unsubscribe: () => void;
  private disposed = false;

  constructor(
    private readonly client: StokesClient,
    private readonly scene: SceneManager,
  ) {
    // A stale slice from an earlier controller (the client changed) is not ours to keep.
    if (scene.hasSource(SLICE_SOURCE)) scene.removeSource(SLICE_SOURCE);
    this.slices = new SliceLayer(client, scene, () => this.sync(useStore.getState()));
    this.unsubscribe = useStore.subscribe((state, previous) => {
      // The status of a cut is written by the slice layer itself; only the list and the walls' fade ask for a sync.
      if (
        state.results !== previous.results ||
        state.stage !== previous.stage ||
        state.tree !== previous.tree ||
        state.geometry !== previous.geometry ||
        state.section !== previous.section ||
        state.slices.list !== previous.slices.list ||
        state.slices.fadeWalls !== previous.slices.fadeWalls ||
        /*
          **The ARROWS are a drawing input** (results item 2). Measured: with this test absent, pressing
          Show changed the store and drew nothing - the subscription never woke the layer, so the slice
          was never redrawn and `drawArrows` saw the OLD value. A stamp that includes an input is no use
          if nothing asks for the redraw in the first place.
        */
        state.arrows !== previous.arrows ||
        // The iso-surface fades the walls as a slice does.
        state.isoDrawn !== previous.isoDrawn ||
        state.iso !== previous.iso ||
        // Streamlines and volume arrows fade the walls too (`wallsFaded`'s `inside`).
        state.streamlines !== previous.streamlines ||
        state.vectorCloud !== previous.vectorCloud ||
        // A case's bare slice draws its cell edges with the Mesh step's switch.
        state.meshDisplay !== previous.meshDisplay
      )
        this.sync(state);
    });
    live = this;
    this.sync(useStore.getState());
  }

  dispose(): void {
    if (live === this) live = null;
    this.disposed = true;
    this.releaseCut();
    this.unsubscribe();
    this.slices.dispose();
    this.generation.abort();
    this.inflight = 0;
    if (live === null) this.publishLoading();
    // The patches go back to their own colours: a controller made for another client would not know these tints.
    for (const source of this.applied.keys()) if (this.scene.hasSource(source)) this.scene.setSourceVertexTint(source, null);
    this.applied.clear();
    this.cache.clear();
  }

  private patchList(state: StokesState): MeshPatchEntry[] {
    if (this.entries?.tree !== state.tree) this.entries = { tree: state.tree, list: meshPatchesOf(state.tree) };
    return this.entries.list;
  }

  /** New results (`seq`) forget every array and every request of the old ones, and the old slice: its surface is the old run's. */
  private renew(seq: number): void {
    if (seq === this.seen) return;
    this.seen = seq;
    this.slices.renew(seq);
    this.removeSlice();
    this.generation.abort();
    this.generation = new AbortController();
    this.cache.clear();
    this.inflight = 0;
    this.publishLoading();
    this.slice = null;
    this.warned.clear();
  }

  private sync(state: StokesState): void {
    if (this.disposed) return;
    const { results } = state;
    this.renew(results.seq);
    const range = results.data === null ? null : effectiveRange(results.view, results.data);
    const walls = this.patchList(state);
    // The mid-plane slice with its cut's section removed lies inside the whole body: the walls fade to show it.
    const buried = midSliceBuried(results.view.slice && results.data?.slice === true, this.cut !== null, state.section);
    this.slices.sync(state, state.stage === "Results" && results.data !== null ? { run: results.data.run, seq: results.seq, data: results.data, view: results.view } : null, walls, this.cut === null, buried);
    if (!resultsShown(state.stage, results.view, results.data !== null) || range === null || results.data === null) {
      if (bareMeshOf(walls.length, results.data?.slice === true, hasImportedCase(state.tree))) {
        this.showBareSlice(state);
        return;
      }
      this.clear();
      return;
    }
    const field = results.view.field;
    // **The look is part of the stamp**: a patch already coloured is left alone, so a stamp without the band
    // count, the map and its direction would skip the repaint when the user changes one of them.
    const look = legendOf(results.view);
    const key = `${results.seq}|${field}|${range.min}|${range.max}|${look.bands}|${look.map}|${look.reverse}`;
    const surfaces: Surface[] = this.patchList(state).map((e) => ({ target: e.id, source: meshSourceOf(e.id), faces: e.faces, name: e.name }));
    this.syncSliceSource(state, surfaces);
    const metas = new Map<string, PatchMeta>();
    for (const meta of this.scene.patches()) if (meta.part !== null && meta.part.startsWith(MESH_SOURCE_PREFIX)) metas.set(meta.part, meta);
    // A patch is coloured once the scene holds it; the sources the scene lost track of are forgotten.
    for (const source of [...this.applied.keys()]) if (!this.scene.hasSource(source)) this.applied.delete(source);

    const run = results.data.run;
    for (const surface of fetchOrder(surfaces)) {
      const meta = metas.get(surface.source);
      if (meta === undefined) continue;
      const cached = this.cache.get(requestKeyOf(results.seq, surface.target, field));
      if (cached === undefined) this.request(run, results.seq, surface, field);
      const values = cached === undefined || cached === "loading" ? null : cached;
      const done = this.applied.get(surface.source);
      const stamp = cached === undefined || cached === "loading" ? `${key}|pending` : key;
      if (done !== undefined && done.meta === meta && done.key === stamp) continue;
      const tint = this.tintOf(values, range, meta, surface, field);
      this.scene.setSourceVertexTint(surface.source, tint);
      this.applied.set(surface.source, { meta, key: stamp });
    }
  }

  private tintOf(values: FieldValues | null, range: Range, meta: PatchMeta, surface: Surface, field: string): Uint8Array | null {
    // **The band count comes from the live view**, so changing it repaints without a refetch.
    // **Reduced through the shared helper**, which sets `components: 1` - what a tint needs.
    const look = legendOf(useStore.getState().results.view);
    const tint = patchTint(reducedForTint(values, field), range, meta.vertices, look.bands, look.map, look.reverse);
    if (tint === null && values !== null && !this.warned.has(surface.source)) {
      this.warned.add(surface.source);
      useStore.getState().log("error", `results: ${field} on ${surface.name} has ${values.count} values for ${meta.vertices} vertices; it keeps its own colour`);
    }
    return tint;
  }

  // ------------------------------------------------------------------ the fields
  /** Tells the store how many field requests are on the way (`viewSettled` waits for none). */
  private publishLoading(): void {
    useStore.setState((s) => {
      const loading = patchLoading(s.loading, { fields: this.inflight });
      return loading === null ? s : { loading };
    });
  }

  private request(run: string, seq: number, surface: Surface, field: string): void {
    if (this.inflight >= MAX_FETCHES) return;
    const key = requestKeyOf(seq, surface.target, field);
    this.cache.set(key, "loading");
    this.inflight++;
    this.publishLoading();
    const generation = this.generation;
    void (async () => {
      let value: Cached = null;
      try {
        // **The requested name comes from the SHARED helper** (item 2 of the three closures): a
        // mid-plane showing speed asks for `U`, and so does one showing a component - `Ux` is one
        // number of `U` - so **one request serves the colouring, the components and the arrows**.
        const requested = requestedFieldOf(surface.target, field);
        const bytes = await fetchResultBytes(this.client, fieldRoute(run, surface.target, requested), generation.signal);
        if (bytes !== null) value = parseField(bytes);
      } catch (err) {
        if (generation.signal.aborted) return;
        useStore.getState().log("error", `results: ${field} on ${surface.name}: ${(err as Error).message}`);
      }
      if (generation.signal.aborted || this.disposed) return;
      this.inflight--;
      this.publishLoading();
      this.cache.set(key, value);
      this.sync(useStore.getState());
    })();
  }

  // ------------------------------------------------------------------ the slice
  /** Adds the slice to the surfaces when it is on and in the scene; fetches its surface and edges when it is not. */
  private syncSliceSource(state: StokesState, surfaces: Surface[]): void {
    const { results } = state;
    if (!results.view.slice || results.data === null || !results.data.slice) {
      this.removeSlice();
      return;
    }
    if (this.slice === null) this.loadSlice(results.data.run, results.seq);
    const edges = results.view.sliceEdges === true;
    if (this.slice?.state === "ready" && !this.scene.hasSource(SLICE_SOURCE)) {
      this.scene.setSource(SLICE_SOURCE, this.slice.groups, [SLICE_NAME], SLICE_STYLE);
      this.scene.setSourceVertexTint(SLICE_SOURCE, null);
      // **Own edges, off unless the Results panel's Cell edges switch is on** (`setSourceCellEdges` `own`).
      this.scene.setSourceCellEdges(SLICE_SOURCE, edges ? this.slice.edges : null, true);
      this.sliceEdgesDrawn = edges;
    } else if (this.slice?.state === "ready" && this.scene.hasSource(SLICE_SOURCE) && edges !== this.sliceEdgesDrawn) {
      this.scene.setSourceCellEdges(SLICE_SOURCE, edges ? this.slice.edges : null, true);
      this.sliceEdgesDrawn = edges;
    }
    if (this.slice?.state === "ready" && this.scene.hasSource(SLICE_SOURCE) && this.cut === null) this.engageCut(state, this.slice.groups);
    if (this.scene.hasSource(SLICE_SOURCE)) surfaces.push({ target: SLICE_TARGET, source: SLICE_SOURCE, faces: Number.MAX_SAFE_INTEGER, name: SLICE_NAME });
  }

  private loadSlice(run: string, seq: number): void {
    const generation = this.generation;
    const mine: SliceGeometry = { seq, state: "loading", groups: [], edges: null };
    this.slice = mine;
    void (async () => {
      try {
        const [surface, edges] = await Promise.all([
          fetchResultBytes(this.client, sliceRoute(run), generation.signal),
          fetchResultBytes(this.client, sliceEdgesRoute(run), generation.signal).catch(() => null),
        ]);
        if (generation.signal.aborted || this.disposed) return;
        if (surface === null) {
          mine.state = "missing";
        } else {
          mine.groups = parseGeometry(surface).groups;
          mine.edges = edges === null ? null : parseMeshEdges(edges);
          mine.state = "ready";
        }
      } catch (err) {
        if (generation.signal.aborted || this.disposed) return;
        mine.state = "missing";
        useStore.getState().log("error", `results: slice: ${(err as Error).message}`);
      }
      this.sync(useStore.getState());
    })();
  }

  /**
   * Drops the section the slice's cut would give back when it goes, or, with no cut engaged yet, the one it
   * would remember when it engages. The Looks card asks it before a press (`dropSavedSection`); the flag of a
   * slice that never comes is cleared by the next `removeSlice`, so it cannot reach a later slice.
   */
  forgetSavedSection(): void {
    if (this.cut !== null) this.cut = forgetSaved(this.cut);
    else this.forgetNext = true;
  }

  private removeSlice(): void {
    if (this.cut === null) this.forgetNext = false;
    this.releaseCut();
    if (this.scene.hasSource(SLICE_SOURCE)) this.scene.removeSource(SLICE_SOURCE);
    this.applied.delete(SLICE_SOURCE);
  }

  /** Cuts the patches at the slice plane, once per slice: a later move of the section by the user is theirs. */
  private engageCut(state: StokesState, groups: readonly LoadedGroup[]): void {
    const settings = findSolverSettings(state.tree);
    const plane = slicePlaneOf(settings?.params as Record<string, unknown> | undefined, boundsOf(groups));
    if (plane === null) {
      // Marked as done so the message is said once; the section is left as the user has it.
      this.cut = { saved: state.section, applied: state.section ?? { axis: "z", offset: 0, invert: false } };
      this.noCut = true;
      useStore.getState().log("error", "results: the slice is not along an axis, so the Section view cannot expose it; cut the model by hand");
      return;
    }
    const model = this.scene.modelBounds();
    const widest = model === null ? 0 : Math.max(model.max[0] - model.min[0], model.max[1] - model.min[1], model.max[2] - model.min[2]);
    const position = this.scene.camera.position;
    const along = plane.axis === "x" ? position.x : plane.axis === "y" ? position.y : position.z;
    const wanted = sliceSection(plane, along, widest * 1e-3);
    const { section, cut } = engageSliceCut(this.cut, state.section, wanted, this.forgetNext);
    this.cut = cut;
    this.forgetNext = false;
    this.noCut = false;
    useStore.setState({ section });
  }

  /** The section the user had comes back, unless they changed it while the slice was on. */
  private releaseCut(): void {
    if (this.cut === null) return;
    const wasNoCut = this.noCut;
    this.noCut = false;
    const held = this.cut;
    this.cut = null;
    if (wasNoCut) return;
    const { section } = releaseSliceCut(held, useStore.getState().section);
    if (section !== undefined) useStore.setState({ section });
  }

  /**
   * An imported case's mesh with no field chosen (any stage, or a mesh-only run with no fields): the slice
   * in its own grey, its cell edges with the Mesh step's "Show mesh cells" switch or the slice's own Edges.
   * A tutorial has no wall patches, so this slice is all there is to see of its mesh until a field colours it.
   */
  private showBareSlice(state: StokesState): void {
    const data = state.results.data;
    if (data === null) return;
    if (this.slice === null) this.loadSlice(data.run, state.results.seq);
    if (this.slice?.state !== "ready") return;
    const edges = state.meshDisplay === "cells" || state.results.view.sliceEdges === true;
    if (!this.scene.hasSource(SLICE_SOURCE)) {
      this.scene.setSource(SLICE_SOURCE, this.slice.groups, [SLICE_NAME], SLICE_STYLE);
      this.scene.setSourceCellEdges(SLICE_SOURCE, edges ? this.slice.edges : null, true);
      this.sliceEdgesDrawn = edges;
    } else if (edges !== this.sliceEdgesDrawn) {
      this.scene.setSourceCellEdges(SLICE_SOURCE, edges ? this.slice.edges : null, true);
      this.sliceEdgesDrawn = edges;
    }
    if (this.applied.has(SLICE_SOURCE)) {
      this.scene.setSourceVertexTint(SLICE_SOURCE, null);
      this.applied.delete(SLICE_SOURCE);
    }
  }

  /** No colouring: every patch we coloured goes back to its own colour and the slice goes. */
  private clear(): void {
    for (const source of this.applied.keys()) if (this.scene.hasSource(source)) this.scene.setSourceVertexTint(source, null);
    this.applied.clear();
    this.removeSlice();
  }
}

/** The box the groups' vertices fill, or null for no vertices. */
function boundsOf(groups: readonly LoadedGroup[]): { min: [number, number, number]; max: [number, number, number] } | null {
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (const group of groups) {
    const p = group.positions;
    for (let i = 0; i + 2 < p.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        if (p[i + k] < min[k]) min[k] = p[i + k];
        if (p[i + k] > max[k]) max[k] = p[i + k];
      }
    }
  }
  return min[0] > max[0] ? null : { min, max };
}
