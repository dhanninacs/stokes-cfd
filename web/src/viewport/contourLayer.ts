/**
 * The 3-D contours in the scene: several iso-surfaces of one field (`state.contours`, the Iso look with a Count
 * above one), each coloured by its value on the colour map and drawn see-through so the inner levels show
 * through the outer ones, as CFD-Post's and Tecplot's volume contours do.
 *
 * Each level is fetched from the same route as the single iso-surface (`GET /api/results/iso/{run}`), which cuts
 * on the run's host when it is not cached; the levels are fetched one after another (the host keeps the case's
 * reader between them, so each after the first is quick) and each enters the scene as it arrives. Lit, unlike
 * the single surface: shading is what lets several translucent shells read as shapes. A new set, leaving the
 * Results stage or another look takes them away. What it drew goes to `state.contoursDrawn` for the card.
 */

import type { StokesClient } from "../api/client";
import { rangeForField } from "../panels/fieldsModel";
import { contourSource, isoRoute, type ContourSet } from "../panels/isoModel";
import { legendOf } from "../panels/legendModel";
import { fetchCut } from "../panels/resultsApi";
import { useStore, type StokesState } from "../state/store";
import { parseGeometry } from "./geometry";
import { isoTint } from "./isoLayer";
import { NO_CONTOURS_DRAWN, contourKey, type ContoursDrawn } from "./contourLayerModel";
import type { PartStyle, SceneManager } from "./scene";

export type ContourScene = Pick<SceneManager, "setSource" | "removeSource" | "hasSource" | "setSourceVertexTint" | "setSourceStyle">;

interface Level {
  value: number;
  vertices: number;
}

export class ContourLayer {
  private key = "";
  private levels = new Map<number, Level>();
  private request: AbortController | null = null;
  private stamp = "";
  private readonly unsubscribe: () => void;
  private disposed = false;

  constructor(
    private readonly client: Pick<StokesClient, "apiBase" | "token">,
    private readonly scene: ContourScene,
  ) {
    this.unsubscribe = useStore.subscribe((state, previous) => {
      if (state.contours !== previous.contours || state.stage !== previous.stage || state.results !== previous.results) this.sync(state);
    });
    this.sync(useStore.getState());
  }

  dispose(): void {
    this.disposed = true;
    this.unsubscribe();
    this.remove();
  }

  private sync(state: StokesState): void {
    if (this.disposed) return;
    const set = state.contours;
    const data = state.results.data;
    if (set === null || data === null || data.run !== set.run || state.stage !== "Results") {
      this.remove();
      return;
    }
    const key = contourKey(set);
    if (key !== this.key) {
      this.remove();
      this.key = key;
      this.fetchAll(set, key);
      return;
    }
    this.restyle(set);
    this.colour(state, set);
  }

  private styleOf(set: ContourSet): PartStyle {
    return { color: "#9aa3ad", visible: true, opacity: set.opacity, unlit: false };
  }

  private fetchAll(set: ContourSet, key: string): void {
    const request = new AbortController();
    this.request = request;
    const drawn: ContoursDrawn = { key, shown: 0, empty: 0, failed: 0, total: set.values.length };
    this.report(drawn);
    void (async () => {
      for (const [index, value] of set.values.entries()) {
        if (request.signal.aborted || this.disposed) return;
        try {
          const answer = await fetchCut(this.client, isoRoute({ run: set.run, field: set.field, value }), request.signal);
          if (request.signal.aborted || this.disposed) return;
          if (answer.kind !== "ok") {
            if (answer.kind === "empty") drawn.empty += 1;
            else drawn.failed += 1;
          } else {
            const groups = parseGeometry(answer.bytes).groups;
            const source = contourSource(index);
            if (this.scene.hasSource(source)) this.scene.removeSource(source);
            this.scene.setSource(source, groups, groups.map(() => `Contour ${set.field} = ${value}`), this.styleOf(set));
            this.levels.set(index, { value, vertices: groups.reduce((n, g) => n + g.vertices, 0) });
            this.stamp = "";
            this.colour(useStore.getState(), set);
            drawn.shown += 1;
          }
        } catch (err) {
          if (request.signal.aborted) return;
          drawn.failed += 1;
          useStore.getState().log("error", `results: contour ${set.field} = ${value}: ${(err as Error).message}`);
        }
        this.report({ ...drawn });
      }
      if (this.request === request) this.request = null;
    })();
  }

  private restyle(set: ContourSet): void {
    for (const index of this.levels.keys()) {
      const source = contourSource(index);
      if (this.scene.hasSource(source)) this.scene.setSourceStyle(source, this.styleOf(set));
    }
  }

  /** Each level in the colour of its value, over the range the walls use for the field; once per range and map. */
  private colour(state: StokesState, set: ContourSet): void {
    const range = rangeForField(state.results.view, state.results.data, set.field);
    const look = legendOf(state.results.view);
    const stamp = range === null ? "none" : `${range.min}|${range.max}|${look.map}|${look.reverse}|${this.levels.size}`;
    if (stamp === this.stamp) return;
    this.stamp = stamp;
    for (const [index, level] of this.levels) {
      const source = contourSource(index);
      if (this.scene.hasSource(source)) this.scene.setSourceVertexTint(source, isoTint(level.vertices, level.value, range, look.map, look.reverse));
    }
  }

  private remove(): void {
    this.request?.abort();
    this.request = null;
    for (const index of this.levels.keys()) {
      const source = contourSource(index);
      if (this.scene.hasSource(source)) this.scene.removeSource(source);
    }
    this.levels.clear();
    this.key = "";
    this.stamp = "";
    this.report(NO_CONTOURS_DRAWN);
  }

  private report(drawn: ContoursDrawn): void {
    const was = useStore.getState().contoursDrawn;
    if (was.key === drawn.key && was.shown === drawn.shown && was.empty === drawn.empty && was.failed === drawn.failed && was.total === drawn.total) return;
    useStore.setState({ contoursDrawn: drawn });
  }
}
