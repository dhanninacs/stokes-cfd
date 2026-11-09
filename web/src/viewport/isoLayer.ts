/**
 * The iso-surface in the scene (ADR-0026 amendment 1): what the Iso-surface control made ready, drawn beside the
 * walls and the slices. The layer follows `state.iso` (which surface, shown or not; `panels/isoModel.ts`) and
 * the results, and does nothing else:
 *
 * * a surface to draw is fetched once from `GET /api/results/iso/{run}?field=&value=` with the page's token
 *   (`fetchCut`, as the slices are), transport version 2 read by `parseGeometry`, which makes views on the
 *   answer's buffer and copies nothing, so there is nothing for a worker to take off the main thread;
 * * the mesh enters the scene in ONE `setSource`, in an animation frame of its own, after the previous
 *   surface has been removed (its geometry freed) in that same frame: the render loop never waits on a fetch,
 *   and the scene never holds two iso-surfaces;
 * * it is coloured as the slices are: the colour map over the range the walls use for that field (the run's,
 *   or the user's fixed range). An iso-surface is one value of its field, so every vertex takes the colour of
 *   that value; the front end computes no field, it places the value the server named on the map;
 * * the visibility switch restyles the source (the mesh stays); Hide, a new run, leaving the Results stage or
 *   a refusal removes it.
 *
 * What the layer did is written to `state.isoDrawn` for the viewport's line and the control. Nothing here is
 * a project command.
 */

import type { StokesClient } from "../api/client";
import { rangeForField, type Range } from "../panels/fieldsModel";
import { ISO_SOURCE, NO_ISO_DRAWN, isoKey, isoRoute, type IsoDrawn, type IsoSummary } from "../panels/isoModel";
import { fetchCut } from "../panels/resultsApi";
import { tintFor, type ColormapId } from "../state/colormap";
import { legendOf } from "../panels/legendModel";
import { useStore, type StokesState } from "../state/store";
import { parseGeometry, type LoadedGroup } from "./geometry";
import type { PartStyle, SceneManager } from "./scene";

/** The scene calls the layer makes: a subset of `SceneManager`, so a test can hand it a recorder. */
export type IsoScene = Pick<SceneManager, "setSource" | "removeSource" | "hasSource" | "setSourceVertexTint" | "setSourceStyle">;

/** Runs `fn` in a later frame; answers a function that cancels it. */
export type Schedule = (fn: () => void) => () => void;

const nextFrame: Schedule = (fn) => {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(() => fn());
    return () => cancelAnimationFrame(id);
  }
  const id = setTimeout(fn, 0);
  return () => clearTimeout(id);
};

const ISO_COLOR = "#9aa3ad";
const ISO_NAME = "Iso-surface (mesh)";

/**
 * The tint of a surface where the field equals `value`: RGBA8, one colour for every vertex, from the colour map
 * over `range`. Null without a range (the surface keeps its own colour).
 */
export function isoTint(vertices: number, value: number, range: Range | null, map?: ColormapId, reverse = false): Uint8Array | null {
  if (range === null || vertices <= 0) return null;
  // **One colour, so never banded**: the surface is at one value, and a band's middle would be a different one.
  const one = tintFor([value], range.min, range.max, 0, map, reverse);
  const out = new Uint8Array(vertices * 4);
  const word = new Uint32Array(one.buffer, 0, 1)[0];
  new Uint32Array(out.buffer).fill(word);
  return out;
}

/** The surface on screen, or on its way. */
interface Held {
  key: string;
  summary: IsoSummary;
  groups: LoadedGroup[] | null;
  vertices: number;
  triangles: number;
  /** The colouring on screen: the range it was made over. */
  stamp: string;
}

export class IsoLayer {
  private held: Held | null = null;
  /** The key of the surface last asked for. */
  private wanted: string | null = null;
  private request: AbortController | null = null;
  private cancelAdd: (() => void) | null = null;
  private visible = true;
  private readonly unsubscribe: () => void;
  private disposed = false;

  constructor(
    private readonly client: Pick<StokesClient, "apiBase" | "token">,
    private readonly scene: IsoScene,
    private readonly schedule: Schedule = nextFrame,
  ) {
    this.unsubscribe = useStore.subscribe((state, previous) => {
      if (state.iso !== previous.iso || state.stage !== previous.stage || state.results !== previous.results) this.sync(state);
    });
    this.sync(useStore.getState());
  }

  dispose(): void {
    this.disposed = true;
    this.unsubscribe();
    this.remove();
    this.report(NO_ISO_DRAWN);
  }

  private sync(state: StokesState): void {
    if (this.disposed) return;
    const surface = state.iso.surface;
    const data = state.results.data;
    // Drawn in the Results stage, on the run the results show; anything else takes it away.
    if (surface === null || surface.state !== "ready" || data === null || data.run !== surface.run || state.stage !== "Results") {
      this.remove();
      this.report(NO_ISO_DRAWN);
      return;
    }
    const key = isoKey(surface);
    this.visible = state.iso.visible;
    if (this.held === null || this.held.key !== key) {
      // Asked once per surface: a refusal stays a refusal until another surface is asked for.
      if (this.wanted !== key) {
        this.wanted = key;
        this.fetch(surface, key);
      }
      return;
    }
    this.style();
    this.colour(state);
  }

  /** Asks for the surface; the one on screen stays until the new one is added. */
  private fetch(summary: IsoSummary, key: string): void {
    this.request?.abort();
    this.cancelAdd?.();
    this.cancelAdd = null;
    const request = new AbortController();
    this.request = request;
    this.report({ state: "loading", key, triangles: 0, message: "" });
    void (async () => {
      let message = "";
      let groups: LoadedGroup[] | null = null;
      try {
        const answer = await fetchCut(this.client, isoRoute(summary), request.signal);
        if (answer.kind === "ok") groups = parseGeometry(answer.bytes).groups;
        else message = answer.kind === "host" ? "The run's host cannot cut this surface" : "The server has no surface for this value";
      } catch (err) {
        if (request.signal.aborted) return;
        message = `The iso-surface could not be fetched: ${(err as Error).message}`;
        useStore.getState().log("error", `results: iso ${key}: ${(err as Error).message}`);
      }
      if (request.signal.aborted || this.disposed) return;
      this.request = null;
      if (groups === null) {
        // The old surface is not this value's: it goes. `wanted` stays, so the refusal is not asked again.
        if (this.scene.hasSource(ISO_SOURCE)) this.scene.removeSource(ISO_SOURCE);
        this.held = null;
        this.report({ state: "error", key, triangles: 0, message });
        return;
      }
      const ready = groups;
      // One add, in a frame of its own: the previous surface goes and the new one comes in the same frame.
      this.cancelAdd = this.schedule(() => {
        this.cancelAdd = null;
        if (this.disposed) return;
        this.add(summary, key, ready);
      });
    })();
  }

  private add(summary: IsoSummary, key: string, groups: LoadedGroup[]): void {
    if (this.scene.hasSource(ISO_SOURCE)) this.scene.removeSource(ISO_SOURCE);
    const vertices = groups.reduce((n, g) => n + g.vertices, 0);
    const triangles = groups.reduce((n, g) => n + g.triangles, 0);
    this.held = { key, summary, groups, vertices, triangles, stamp: "" };
    const state = useStore.getState();
    this.scene.setSource(ISO_SOURCE, groups, groups.map(() => ISO_NAME), this.styleOf());
    this.colour(state);
    this.report({ state: "shown", key, triangles, message: "" });
  }

  private styleOf(): PartStyle {
    return { color: ISO_COLOR, visible: this.visible, opacity: 1, unlit: true };
  }

  private style(): void {
    if (this.scene.hasSource(ISO_SOURCE)) this.scene.setSourceStyle(ISO_SOURCE, this.styleOf());
  }

  /** The colour of the surface's value in the walls' range for its field; once per range. */
  private colour(state: StokesState): void {
    const held = this.held;
    if (held === null || held.groups === null || !this.scene.hasSource(ISO_SOURCE)) return;
    const range = rangeForField(state.results.view, state.results.data, held.summary.field);
    // **The map is part of the stamp**: the surface is coloured once per stamp, so a change of map or of its
    // direction must read as a new colouring.
    const look = legendOf(state.results.view);
    const stamp = range === null ? "none" : `${range.min}|${range.max}|${look.map}|${look.reverse}`;
    if (stamp === held.stamp) return;
    held.stamp = stamp;
    this.scene.setSourceVertexTint(ISO_SOURCE, isoTint(held.vertices, held.summary.value, range, look.map, look.reverse));
  }

  private remove(): void {
    this.request?.abort();
    this.request = null;
    this.cancelAdd?.();
    this.cancelAdd = null;
    if (this.scene.hasSource(ISO_SOURCE)) this.scene.removeSource(ISO_SOURCE);
    this.held = null;
    this.wanted = null;
  }

  private report(drawn: IsoDrawn): void {
    const was = useStore.getState().isoDrawn;
    if (was.state === drawn.state && was.key === drawn.key && was.triangles === drawn.triangles && was.message === drawn.message) return;
    useStore.setState({ isoDrawn: drawn });
  }
}
