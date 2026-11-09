/**
 * The slices of the Results stage in the scene: cuts of the run's results at any plane, drawn beside the
 * wall patches. `FieldsController` (fields.ts) owns one of these and hands it the store each time something
 * changes; this class does the rest, through the same hooks the patches use and nothing else:
 *
 * * for each visible slice of `state.slices` it asks for the cut at the slice's plane,
 *   `GET /api/results/slice/{run}?plane=` (transport version 2, read by `parseGeometry` like a patch) and
 *   `.../slice-edges/{run}?plane=`, at most two at a time (the server computes two cuts at once), and keeps
 *   the answer by plane: two slices on one plane, or a plane visited again, ask once. The cut becomes the
 *   scene source `mesh:slice:<id>`;
 * * its field is `GET /api/results/field/{run}/slice?plane=&field=`, tinted from the colour map over the same
 *   range as the walls (the run's own, or the user's fixed range for that field), so every slice and every
 *   wall share one scale;
 * * "Bands" cuts the cut along the band boundaries (`state/contours.ts` `bandSurface`), so the fill is flat
 *   colours with crisp edges, and "Lines" draws the contour lines (`contourSegments`, marching triangles on
 *   the slice's own vertex values) as scene lines lifted a hair off the plane on both sides, coloured by the
 *   map or one neutral colour; both follow the range the walls use;
 * * a slice moved to another plane keeps showing the old cut until the new one has arrived (the status says
 *   it is cutting); a plane that misses the model (404 `results.slice_empty`) removes its cut and says so;
 * * while any slice (or the iso-surface, `viewport/isoLayer.ts`) is shown and the walls are to fade, the wall
 *   patches are drawn see-through (`FADED_WALL_OPACITY`), so what lies inside the body can be seen; they come
 *   back as the tree has them when the last of them goes.
 *
 * The Section view's cap (`CAP_SOURCE`): while the Section view is on in the Results stage the layer also cuts
 * the results at the section plane and paints that cut with the field the walls show, in the walls' range, so
 * the cut face continues the surface colours and the legend applies to it. The plane is asked of the server
 * 300 ms after the handle rests (the previous cap stays until the new one has arrived), the cut is lifted a hair
 * to the side the section keeps so the clip plane does not eat it, and turning the section off, leaving the
 * stage or an error takes it away (the walls then show the hollow they always did). It is not drawn while the
 * mid-plane slice is exposing itself with the section.
 *
 * What each cut is doing is written to `state.slices.status` (loading, ready, empty, host, error) for the
 * panel. Nothing here is a project command.
 */

import type { StokesClient } from "../api/client";
import { fetchCut, sliceEdgesRoute, sliceFieldRoute, sliceRoute, type RunFields } from "../panels/resultsApi";
import { NO_FIELD, componentRangeOrNull, derivedComponentOf, patchTint, rangeForField, type Range, type ResultsView } from "../panels/fieldsModel";
import { arrowLength, arrowSeeds, volumeArrowsShown } from "../panels/arrowModel";
import { SLICE_TARGET } from "../panels/resultsApi";
import { arrowMesh } from "./arrowLayer";
import { reducedForTint, requestedFieldOf } from "./sliceValues";
import { cutSpan } from "../panels/arrowModel";
import { CAP_SOURCE, CAP_STATUS_KEY, FADED_WALL_OPACITY, SLICE_DEBOUNCE_MS, clearSliceStatus, contourLevels, createDebounce, setSliceStatus, sliceFieldOf, sliceName, sliceSourceOf, visibleSlices, type SliceSpec, type SliceState, wallsFaded } from "../panels/sliceModel";
import { bandColor, levelColor } from "../state/colormap";
import { legendOf } from "../panels/legendModel";
import { bandSurface, contourSegments, liftedLines } from "../state/contours";
import { parseField, type FieldValues } from "../state/fieldCodec";
import { meshSourceOf, type MeshPatchEntry } from "../state/meshPatches";
import type { SectionPlane } from "../state/sectionState";
import { planeKey, planeOfSection, type Plane } from "../state/slicePlane";
import { useStore, type StokesState } from "../state/store";
import { patchLoading } from "../state/loadingState";
import { parseGeometry, type LoadedGroup } from "./geometry";
import { parseMeshEdges } from "./meshEdges";
import type { PartStyle, SceneManager } from "./scene";

/** Cuts asked of the server at once: it computes two at a time and queues the rest. */
const MAX_CUTS = 2;
/** Field requests in flight at once. */
const MAX_FIELD_FETCHES = 4;
/** Cuts kept by plane; the server keeps 16 per run, the client keeps a few more. */
const MAX_CACHED_CUTS = 24;
/** How far the section's cap sits off its plane, to the kept side, as a share of the model's widest extent. */
const CAP_LIFT = 1e-4;
/** How far the contour lines sit off the plane, as a share of the model's widest extent. */
const LINE_LIFT = 2e-4;

/** Every cut is unlit (materials.ts `createLook`): the colour map is the information, shading hides it. */
const SLICE_STYLE: PartStyle = { color: "#9aa3ad", visible: true, opacity: 1, unlit: true };

/** What the server answered for one plane. */
interface Cut {
  state: "loading" | "ready" | "empty" | "host" | "error";
  message: string;
  groups: LoadedGroup[];
  vertices: number;
  edges: Float32Array | null;
}

type Cached = FieldValues | null | "loading";


/** What a slice shows in the scene now. */
interface Shown {
  /** The cache key of the cut on screen. */
  cut: string;
  /** The settings of the colouring on screen. */
  stamp: string;
  /** The scene holds the banded pieces of the cut, not the cut itself. */
  banded: boolean;
  /** The cut's cell edges are in the scene (the slice's Edges switch). */
  edges: boolean;
}

/** What the layer needs from the results each time it syncs. */
export interface SliceContext {
  run: string;
  seq: number;
  data: RunFields;
  view: ResultsView;
}

export class SliceLayer {
  private readonly cuts = new Map<string, Cut>();
  private readonly values = new Map<string, Cached>();
  private readonly shown = new Map<string, Shown>();
  private cutsInflight = 0;
  private fieldsInflight = 0;
  private generation = new AbortController();
  private seen = -1;
  /** The wall sources whose opacity this layer lowered. */
  private faded = new Set<string>();
  /** The section's cap: the plane last settled, the plane wanted, and what the scene shows of it. */
  private capPlane: Plane | null = null;
  private capWanted: string | null = null;
  private capShown: Shown | null = null;
  /** The side of the plane the cap on screen was lifted to (-1: the section keeps the other half). */
  private capSide = 1;
  private readonly capSettle = createDebounce<Plane>((plane) => {
    this.capPlane = plane;
    this.changed();
  }, SLICE_DEBOUNCE_MS);
  /** The wall entries of the last sync, to put the walls back when the layer goes. */
  private walls: readonly MeshPatchEntry[] = [];
  private disposed = false;

  constructor(
    private readonly client: StokesClient,
    private readonly scene: SceneManager,
    /** Asks the owner to sync again: an answer arrived. */
    private readonly changed: () => void,
  ) {}

  dispose(): void {
    this.disposed = true;
    this.capSettle.cancel();
    this.generation.abort();
    this.cutsInflight = 0;
    this.fieldsInflight = 0;
    this.publishLoading();
    this.removeAll();
    this.cuts.clear();
    this.values.clear();
  }

  /** New results (another run, or the old ones dropped): every cut and field of the old ones is forgotten. */
  renew(seq: number): void {
    if (seq === this.seen) return;
    this.seen = seq;
    this.generation.abort();
    this.generation = new AbortController();
    this.cuts.clear();
    this.values.clear();
    this.cutsInflight = 0;
    this.fieldsInflight = 0;
    this.publishLoading();
    this.capSettle.cancel();
    this.capPlane = null;
    this.capWanted = null;
    this.removeAll();
  }

  /**
   * Brings the scene to what `state.slices` lists. `context` is null when no slice is to be drawn (not the
   * Results stage, no results): every slice leaves the scene and the walls are not faded.
   */
  sync(state: StokesState, context: SliceContext | null, walls: readonly MeshPatchEntry[], capAllowed = true, midBuried = false): void {
    if (this.disposed) return;
    this.walls = walls;
    this.syncCap(state.section, context !== null && capAllowed ? context : null);
    const slices = context === null ? [] : visibleSlices(state.slices);
    const wanted = new Set(slices.map((s) => s.id));
    for (const id of [...this.shown.keys()]) if (!wanted.has(id)) this.removeSlice(id);
    if (context !== null) for (const spec of slices) this.syncSlice(spec, context);
    // An iso-surface lies inside the body as a slice does: the same "fade the walls" setting shows it.
    const isoInside = context !== null && state.isoDrawn?.state === "shown" && state.iso?.visible === true;
    // Streamlines and volume arrows fill the fluid: the same setting fades the walls to show them.
    const inside = context !== null && ((state.streamlines?.lines.length ?? 0) > 0 || (volumeArrowsShown(state.arrows) && state.vectorCloud?.phase === "ready"));
    this.fadeWalls(wallsFaded({ setting: state.slices.fadeWalls, slices: slices.length, iso: isoInside, midBuried: context !== null && midBuried, inside }), walls);
  }

  // ------------------------------------------------------------------ one slice
  private syncSlice(spec: SliceSpec, context: SliceContext): void {
    const key = planeKey(spec.plane);
    if (key === null) {
      this.report(spec.id, { status: "error", message: "This plane is not valid" });
      this.removeSlice(spec.id);
      return;
    }
    const cutKey = `${context.seq}|${key}`;
    let cut = this.cuts.get(cutKey);
    if (cut === undefined) cut = this.requestCut(context, cutKey, spec.plane, spec.id);
    if (cut === undefined || cut.state === "loading") {
      // The slice that was on screen stays until the new cut arrives.
      this.report(spec.id, { status: "loading", message: "" });
      return;
    }
    if (cut.state !== "ready") {
      this.removeSlice(spec.id);
      this.report(spec.id, { status: cut.state, message: cut.message });
      return;
    }
    this.report(spec.id, { status: "ready", message: "" });
    this.draw(spec, context, cutKey, cut);
  }

  /** Puts the cut in the scene (once per cut) and colours it (once per setting). */
  private draw(spec: SliceSpec, context: SliceContext, cutKey: string, cut: Cut): void {
    const source = sliceSourceOf(spec.id);
    let held = this.shown.get(spec.id);
    if (held === undefined || held.cut !== cutKey || !this.scene.hasSource(source)) {
      this.scene.setSource(source, cut.groups, [sliceName(spec)], SLICE_STYLE);
      this.scene.setSourceVertexTint(source, null);
      this.scene.setSourceCellEdges(source, spec.edges ? cut.edges : null, true);
      this.scene.setSourceContours(source, null, null);
      held = { cut: cutKey, stamp: "", banded: false, edges: spec.edges };
      this.shown.set(spec.id, held);
    }
    // The Edges switch moved with the cut still on screen: only the edges change, not the fill.
    if (held.edges !== spec.edges) {
      this.scene.setSourceCellEdges(source, spec.edges ? cut.edges : null, true);
      held.edges = spec.edges;
    }
    const field = sliceFieldOf(spec, context.view.field);
    const baseRange = field === NO_FIELD ? null : rangeForField(context.view, context.data, field);
    /*
      **Arrows read the field the colouring already fetched, when it is a VECTOR** (claude-win2's item
      2). `U` is a legitimate colouring field - the header's min/max range over its MAGNITUDE
      (`agent/stokes_agent/extract.py` computes `np.linalg.norm` for `U`), and `vertexValues` reduces it
      to one number per vertex - so choosing `U` colours the cut with speed **and** gives the arrows
      their directions **from one request**. No second fetch, which is what the slice tests were right
      to refuse last round.
    */
    const arrowsOn = useStore.getState().arrows?.shown === true;
    /*
      **A speed field is coloured from a VECTOR, so its directions are that vector's.** `U_mag` and `U`
      are the same motion, and the header ranges `U` over its magnitude - so choosing `|U|` says nothing
      about direction being unavailable. The vector is therefore fetched from the SAME `values` the
      colouring already has when the field IS the vector, and asked for separately only when the field is
      its magnitude. **Both cases are one fetch**: the entry asked for is the one the colouring needs.
    */
    let values: FieldValues | null = null;
    let pending = false;
    /*
      **The request and its key come from the SHARED helper** (`sliceValues.ts`), so this path and
      `FieldsController`'s ask for the same bytes under the same key. This is the ADDED-slice site
      (line 237; confirmed against `sliceSourceOf` and by claude-win2's 0430 answer).
    */
    const arithmetic = requestedFieldOf(SLICE_TARGET, field);
    /*
      **The fetch does NOT wait for a range.** A derived component has none until its samples arrive -
      `rangeForField` answers null for it on purpose - so gating the request on `range !== null` would
      mean the values that PROVIDE the range were never fetched. The range is resolved after them.
    */
    const needsValues = (spec.fill || spec.lines || arrowsOn) && field !== NO_FIELD;
    if (needsValues && (baseRange !== null || derivedComponentOf(field) !== null)) {
      // **Keyed on the REQUESTED name**: keying on `U_mag` while fetching `U` stores the answer where
      // nobody reads it.
      const valueKey = `${cutKey}|${arithmetic}`;
      const cached = this.values.get(valueKey);
      if (cached === undefined) {
        this.requestField(context, valueKey, spec.plane, sliceName(spec), arithmetic);
        pending = true;
      } else if (cached === "loading") {
        pending = true;
      } else {
        values = cached;
      }
    }
    /*
      **The stamp includes the ARROWS.** Measured: switching them on produced an identical stamp, so
      `draw` returned at the guard below and `drawArrows` was never called - the toggle worked, the
      state changed, and nothing was drawn. A stamp that omits an input of the drawing is a stamp that
      skips a redraw the user asked for.
    */
    /*
      **A DERIVED COMPONENT is ranged from its own sampled values**, never from the vector's magnitude.
      `rangeForField` answers null for a component precisely so this branch runs: a magnitude range is
      non-negative while a component is signed, so using it would clip reversed flow at the bottom of the
      colour map. **The user's lock still wins** - it is checked before the sample is consulted.
    */
    // **The reduction happens first**: a component's range is read from the SAME one-per-vertex array
    // the tint gets, so the legend and the colours cannot be measured from different numbers.
    const reducedValues = reducedForTint(values, field);
    const sampledAxis = derivedComponentOf(field);
    const range = sampledAxis !== null && reducedValues !== null
      ? (useStore.getState().results.view.lock?.field === field
          ? rangeForField(context.view, context.data, field)
          : componentRangeOrNull(field, { component: sampledAxis, values: reducedValues.values }))
      : baseRange;
    // **The map and its direction are part of the stamp**, or a change of either would draw nothing.
    const look = legendOf(context.view);
    const stamp = [
      field,
      range === null ? "" : `${range.min}|${range.max}`,
      look.map,
      look.reverse,
      spec.fill,
      spec.bands,
      spec.lines,
      spec.lineColour,
      spec.levels,
      arrowsOn ? "arrows" : "",
      pending ? "pending" : values === null ? "none" : "values",
    ].join("|");
    if (held.stamp === stamp) return;
    held.stamp = stamp;
    /*
      **A vector is usable for colouring now**: `vertexValues` reduces it to its magnitude per vertex,
      which is the same number the header's min/max were measured on. Before that, `components === 1`
      was the gate, because nothing downstream could read a 3-float-per-vertex array.
    */
    /*
      **`usable` is the REDUCED field**: the tint, the contour lines and the band surface all index one
      value per vertex, and a three-component field holds three. Measured: the contour count came out
      **0** instead of 48 when the lines were fed the raw vector.
    */
    const usable =
      reducedValues !== null && range !== null && reducedValues.count === cut.vertices
        ? reducedValues
        : null;
    this.paint(spec, source, cut, held, usable, range, values, field);
    // **The raw vector goes to the arrows** - the magnitudes that colour the cut carry no direction.
    /*
      **The arrows take `values`, the RAW cache entry** - not `usable`, which is reduced to one number
      per vertex and carries no direction. Measured: with `usable` the arrows received `null` on every
      draw, because the reduced field is never three-component.
    */
    this.drawArrows(spec, cut, arrowsOn && values !== null && values.components === 3 ? values : null);
    this.drawLines(spec, source, cut, usable, range);
  }

  /** The fill: the cut in smooth colours, in flat bands, or its own colour. */
  private paint(spec: SliceSpec, source: string, cut: Cut, held: Shown, usable: FieldValues | null, range: Range | null, values: FieldValues | null, field: string): void {
    const banded = spec.fill && spec.bands && usable !== null && range !== null && cut.groups.length === 1;
    if (banded) {
      const group = cut.groups[0];
      const look = legendOf(useStore.getState().results.view);
      // The bands are equal in VALUE and coloured on the map, centred for a diverging one over this range.
      const colourOf = (band: number, bands: number) => bandColor(band, bands, look.map, look.reverse, range);
      const surface = bandSurface(group.positions, group.normals, group.indices, usable.values, range, spec.levels, colourOf);
      const pieces: LoadedGroup = { ...group, positions: surface.positions, normals: surface.normals, indices: surface.indices, vertices: surface.positions.length / 3, triangles: surface.indices.length / 3, faceIds: group.faceIds === null ? null : new Uint32Array(surface.indices.length / 3) };
      this.scene.setSource(source, [pieces], [sliceName(spec)], SLICE_STYLE);
      this.scene.setSourceVertexTint(source, null);
      this.scene.setSourceCellEdges(source, spec.edges ? cut.edges : null, true);
      this.scene.setSourceVertexTint(source, surface.tint);
      held.banded = true;
      return;
    }
    if (held.banded) {
      this.scene.setSource(source, cut.groups, [sliceName(spec)], SLICE_STYLE);
      this.scene.setSourceVertexTint(source, null);
      this.scene.setSourceCellEdges(source, spec.edges ? cut.edges : null, true);
      held.banded = false;
    }
    /*
      **`vertexValues` is what makes a VECTOR field colourable**: `tintOf` and `contourLevels` index one
      float per vertex, and a 3-component field holds three. The magnitudes it produces are what the
      header's min/max were measured on, so the range and the values agree.
    */
    // **`usable`, not `values`**: `usable` is the reduced field (one component per vertex) and `values`
    // is whatever the cache holds. Measured: `patchTint` returned null for the raw vector even though
    // the counts matched, because a three-component field is not a tint.
    const reduced = usable;
    this.scene.setSourceVertexTint(source, spec.fill ? this.tintOf(reduced, range, cut.vertices, spec, field) : null);
  }

  /**
   * One instanced arrow mesh for a slice, or none (claude-win2's item 2).
   *
   * **The vectors are the colouring's own field**, fetched once - see the note in `draw`.
   */
  private drawArrows(spec: SliceSpec, cut: Cut, vectors: FieldValues | null): void {
    const arrows = useStore.getState().arrows ?? { shown: false, density: 0, scale: 1 };
    // The volume's arrows are the viewport's (`vectorCloud`); a slice carries none while they show.
    if (!arrows.shown || arrows.where === "volume" || vectors === null || cut.groups.length === 0) {
      this.scene.setSliceArrows(spec.id, null);
      return;
    }
    /*
      **Every group of the cut, not the first** (2026-11-04). The cut is one group per region it crosses, and the
      field's values run on across them in order (`draw` reads `values.subarray(at, at + group.vertices)`). Reading
      `groups[0]` alone drew arrows over one region and none over the rest of the plane.
    */
    const points: [number, number, number][] = [];
    const dirs: [number, number, number][] = [];
    const total = Math.floor(vectors.values.length / 3);
    let at = 0;
    for (const group of cut.groups) {
      const n = Math.min(group.vertices, total - at);
      for (let i = 0; i < n; i++) {
        points.push([group.positions[i * 3]!, group.positions[i * 3 + 1]!, group.positions[i * 3 + 2]!]);
        const k = (at + i) * 3;
        dirs.push([vectors.values[k]!, vectors.values[k + 1]!, vectors.values[k + 2]!]);
      }
      at += group.vertices;
    }
    const seeds = arrowSeeds(points, dirs, arrows.density);
    if (seeds.length === 0) {
      this.scene.setSliceArrows(spec.id, null);
      return;
    }
    const speeds = seeds.map((s) => s.speed);
    const look = legendOf(useStore.getState().results.view);
    /*
      **The span is the CUT's, not the model's.** Measured on the audit's duct: its slice is **20 mm**
      across while the duct is **0.5 m** long, so a model-relative arrow was **50 mm** - two and a half
      times the cut - and forty of them overlapped into one blob that read as "no arrows". An arrow
      belongs to the surface it sits on, and that is the surface whose size sets its length.
    */
    const span = cutSpan(points);
    this.scene.setSliceArrows(
      spec.id,
      arrowMesh(seeds, arrowLength(span, arrows.scale), { min: Math.min(...speeds), max: Math.max(...speeds) }, look.map, look.reverse),
    );
  }

  /** The contour lines: the cut's own triangles and values, the plane's normal for the lift. */
  private drawLines(spec: SliceSpec, source: string, cut: Cut, usable: FieldValues | null, range: Range | null): void {
    if (!spec.lines || usable === null || range === null) {
      this.scene.setSourceContours(source, null, null);
      return;
    }
    const levels = contourLevels(range.min, range.max, spec.levels);
    const look = legendOf(useStore.getState().results.view);
    const box = this.scene.modelBounds();
    const widest = box === null ? 0 : Math.max(box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]);
    const parts: { positions: Float32Array; colours: Uint8Array | null }[] = [];
    let at = 0;
    for (const group of cut.groups) {
      const segments = contourSegments(group.positions, group.indices, usable.values.subarray(at, at + group.vertices), levels);
      parts.push(liftedLines(segments, spec.plane.normal, widest * LINE_LIFT, spec.lineColour === "map" ? (level) => levelColor(level, spec.levels, look.map, look.reverse, range) : null));
      at += group.vertices;
    }
    const length = parts.reduce((n, part) => n + part.positions.length, 0);
    const positions = new Float32Array(length);
    const colours = spec.lineColour === "map" ? new Uint8Array(length) : null;
    let offset = 0;
    for (const part of parts) {
      positions.set(part.positions, offset);
      if (colours !== null && part.colours !== null) colours.set(part.colours, offset);
      offset += part.positions.length;
    }
    this.scene.setSourceContours(source, positions, colours);
  }

  private tintOf(values: FieldValues | null, range: Range | null, vertices: number, spec: SliceSpec, field: string): Uint8Array | null {
    if (values === null || range === null) return null;
    const look = legendOf(useStore.getState().results.view);
    const tint = patchTint(values, range, vertices, 0, look.map, look.reverse);
    if (tint === null) useStore.getState().log("error", `results: ${field} on ${sliceName(spec)} has ${values.count} values for ${vertices} vertices; it keeps its own colour`);
    return tint;
  }

  private removeSlice(id: string): void {
    const source = sliceSourceOf(id);
    if (this.scene.hasSource(source)) this.scene.removeSource(source);
    this.shown.delete(id);
  }

  private removeAll(): void {
    this.removeCap();
    for (const id of [...this.shown.keys()]) this.removeSlice(id);
    this.restoreWalls();
  }

  // ------------------------------------------------------------------ the server
  /** Tells the store how many cuts and values are on the way (`viewSettled` waits for none). */
  private publishLoading(): void {
    useStore.setState((s) => {
      const loading = patchLoading(s.loading, { cuts: this.cutsInflight, sliceFields: this.fieldsInflight });
      return loading === null ? s : { loading };
    });
  }

  private requestCut(context: SliceContext, cutKey: string, plane: Plane, id: string): Cut | undefined {
    if (this.cutsInflight >= MAX_CUTS) return undefined;
    const surfaceRoute = sliceRoute(context.run, plane);
    const edgesRoute = sliceEdgesRoute(context.run, plane);
    const cut: Cut = { state: "loading", message: "", groups: [], vertices: 0, edges: null };
    if (surfaceRoute === null || edgesRoute === null) {
      cut.state = "error";
      cut.message = "This plane is not valid";
      this.cuts.set(cutKey, cut);
      return cut;
    }
    this.evict();
    this.cuts.set(cutKey, cut);
    this.cutsInflight++;
    this.publishLoading();
    const generation = this.generation;
    void (async () => {
      try {
        const [surface, edges] = await Promise.all([fetchCut(this.client, surfaceRoute, generation.signal), fetchCut(this.client, edgesRoute, generation.signal).catch(() => null)]);
        if (generation.signal.aborted || this.disposed) return;
        if (surface.kind === "ok") {
          cut.groups = parseGeometry(surface.bytes).groups;
          cut.vertices = cut.groups.reduce((n, g) => n + g.vertices, 0);
          cut.edges = edges !== null && edges.kind === "ok" ? parseMeshEdges(edges.bytes) : null;
          cut.state = "ready";
        } else if (surface.kind === "empty" || surface.kind === "host") {
          cut.state = surface.kind;
        } else {
          cut.state = "error";
          cut.message = "The server has no cut for this plane";
        }
      } catch (err) {
        if (generation.signal.aborted || this.disposed) return;
        cut.state = "error";
        cut.message = `The plane could not be cut: ${(err as Error).message}`;
        useStore.getState().log("error", `results: slice ${id}: ${(err as Error).message}`);
      }
      this.cutsInflight--;
      this.publishLoading();
      this.changed();
    })();
    return cut;
  }

  private requestField(context: SliceContext, valueKey: string, plane: Plane, label: string, field: string): void {
    if (this.fieldsInflight >= MAX_FIELD_FETCHES) return;
    const route = sliceFieldRoute(context.run, plane, field);
    if (route === null) {
      this.values.set(valueKey, null);
      return;
    }
    this.values.set(valueKey, "loading");
    this.fieldsInflight++;
    this.publishLoading();
    const generation = this.generation;
    void (async () => {
      let value: Cached = null;
      try {
        const answer = await fetchCut(this.client, route, generation.signal);
        if (answer.kind === "ok") value = parseField(answer.bytes);
      } catch (err) {
        if (generation.signal.aborted) return;
        useStore.getState().log("error", `results: ${field} on ${label}: ${(err as Error).message}`);
      }
      if (generation.signal.aborted || this.disposed) return;
      this.fieldsInflight--;
      this.publishLoading();
      this.values.set(valueKey, value);
      this.changed();
    })();
  }

  /** Keeps the cuts by plane to a bound: the oldest not on screen go first. */
  private evict(): void {
    if (this.cuts.size < MAX_CACHED_CUTS) return;
    const onScreen = new Set([...this.shown.values()].map((s) => s.cut));
    if (this.capShown !== null) onScreen.add(this.capShown.cut);
    for (const key of this.cuts.keys()) {
      if (this.cuts.size < MAX_CACHED_CUTS) break;
      const cut = this.cuts.get(key);
      if (onScreen.has(key) || cut?.state === "loading") continue;
      this.cuts.delete(key);
      for (const valueKey of [...this.values.keys()]) if (valueKey.startsWith(`${key}|`)) this.values.delete(valueKey);
    }
  }

  // ------------------------------------------------------------------ the section's cap
  /** Follows the Section view: the cut at its plane, painted with the walls' field. `context` is null when no cap is to be drawn. */
  private syncCap(section: SectionPlane | null, context: SliceContext | null): void {
    const field = context === null ? NO_FIELD : context.view.field;
    if (!section || context === null || field === NO_FIELD) {
      this.capSettle.cancel();
      this.capPlane = null;
      this.capWanted = null;
      this.removeCap();
      this.clearCapStatus();
      return;
    }
    const wanted = planeOfSection(section);
    const key = planeKey(wanted);
    if (key === null) {
      this.removeCap();
      this.report(CAP_STATUS_KEY, { status: "error", message: "The section plane is not valid" });
      return;
    }
    // The plane is asked of the server once the handle has rested; the first one at once.
    if (key !== this.capWanted) {
      this.capWanted = key;
      if (this.capPlane === null) {
        this.capSettle.cancel();
        this.capPlane = wanted;
      } else {
        this.capSettle.call(wanted);
      }
    }
    const plane = this.capPlane;
    const settled = plane === null ? null : planeKey(plane);
    if (plane === null || settled === null) return;
    const cutKey = `${context.seq}|${settled}`;
    let cut = this.cuts.get(cutKey);
    if (cut === undefined) cut = this.requestCut(context, cutKey, plane, CAP_STATUS_KEY);
    if (cut === undefined || cut.state === "loading") {
      this.report(CAP_STATUS_KEY, { status: "loading", message: "" });
      return;
    }
    if (cut.state !== "ready") {
      this.removeCap();
      this.report(CAP_STATUS_KEY, { status: cut.state, message: cut.message });
      return;
    }
    this.report(CAP_STATUS_KEY, { status: "ready", message: "" });
    this.drawCap(section, context, cutKey, plane, cut);
  }

  private drawCap(section: SectionPlane, context: SliceContext, cutKey: string, plane: Plane, cut: Cut): void {
    let held = this.capShown;
    const side = section.invert ? -1 : 1;
    if (held === null || held.cut !== cutKey || side !== this.capSide || !this.scene.hasSource(CAP_SOURCE)) {
      this.capSide = side;
      // Lifted to the side the section keeps, so the clip plane it lies on does not discard it.
      const box = this.scene.modelBounds();
      const widest = box === null ? 0 : Math.max(box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]);
      const lift = widest * CAP_LIFT * side;
      const axis = "xyz".indexOf(section.axis);
      const groups = cut.groups.map((group) => {
        const positions = new Float32Array(group.positions);
        for (let i = axis; i < positions.length; i += 3) positions[i] += lift;
        return { ...group, positions };
      });
      this.scene.setSource(CAP_SOURCE, groups, ["Section cut (mesh)"], SLICE_STYLE);
      this.scene.setSourceVertexTint(CAP_SOURCE, null);
      this.scene.setSourceCellEdges(CAP_SOURCE, null);
      held = { cut: cutKey, stamp: "", banded: false, edges: false };
      this.capShown = held;
    }
    const field = context.view.field;
    const range = rangeForField(context.view, context.data, field);
    let values: FieldValues | null = null;
    let pending = false;
    if (range !== null) {
      const arithmetic = requestedFieldOf(SLICE_TARGET, field);
      const valueKey = `${cutKey}|${arithmetic}`;
      const cached = this.values.get(valueKey);
      if (cached === undefined) {
        this.requestField(context, valueKey, plane, "the section cut", arithmetic);
        pending = true;
      } else if (cached === "loading") {
        pending = true;
      } else {
        values = cached;
      }
    }
    const look = legendOf(context.view);
    const stamp = [field, range === null ? "" : `${range.min}|${range.max}`, look.map, look.reverse, pending ? "pending" : values === null ? "none" : "values"].join("|");
    if (held.stamp === stamp) return;
    held.stamp = stamp;
    // **The cap follows the same rule**: its values come through the same cache, so they are reduced
    // before a tint can read them - `patchTint` is one byte per vertex.
    const tint = values === null || range === null ? null : patchTint(reducedForTint(values, field), range, cut.vertices, 0, look.map, look.reverse);
    this.scene.setSourceVertexTint(CAP_SOURCE, tint);
  }

  private removeCap(): void {
    if (this.scene.hasSource(CAP_SOURCE)) this.scene.removeSource(CAP_SOURCE);
    this.capShown = null;
  }

  private clearCapStatus(): void {
    if (useStore.getState().slices.status[CAP_STATUS_KEY] === undefined) return;
    useStore.setState((st) => {
      const next = clearSliceStatus(st.slices, CAP_STATUS_KEY);
      return next === st.slices ? st : { slices: next };
    });
  }

  // ------------------------------------------------------------------ the status
  private report(id: string, state: SliceState): void {
    const was = useStore.getState().slices.status[id];
    if (was !== undefined && was.status === state.status && was.message === state.message) return;
    useStore.setState((s) => {
      const next = setSliceStatus(s.slices, id, state);
      return next === s.slices ? s : { slices: next };
    });
  }

  // ------------------------------------------------------------------ the walls
  /** Draws the wall patches see-through (or as the tree has them) according to `on`. */
  private fadeWalls(on: boolean, walls: readonly MeshPatchEntry[]): void {
    if (!on) {
      this.restoreWalls(walls);
      return;
    }
    for (const entry of walls) {
      const source = meshSourceOf(entry.id);
      if (!this.scene.hasSource(source)) continue;
      this.scene.setSourceStyle(source, { color: entry.color, visible: entry.visible, opacity: Math.min(entry.opacity, FADED_WALL_OPACITY) });
      this.faded.add(source);
    }
  }

  private restoreWalls(walls: readonly MeshPatchEntry[] = this.walls): void {
    if (this.faded.size === 0) return;
    const entries = new Map(walls.map((e) => [meshSourceOf(e.id), e]));
    for (const source of this.faded) {
      const entry = entries.get(source);
      if (entry !== undefined && this.scene.hasSource(source)) this.scene.setSourceStyle(source, { color: entry.color, visible: entry.visible, opacity: entry.opacity });
    }
    this.faded.clear();
  }
}
