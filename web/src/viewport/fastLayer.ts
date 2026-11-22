/**
 * Fast mode's plane in the scene (ADR-0047 decision 8). The server steps the lattice and serves the
 * shown plane; this layer only fetches and draws what it is given (ADR-0013):
 *
 * * on every `fast.frame` it asks for the plane's values, `GET /api/fast/field?field=`, and when the
 *   reply's `X-Fast-Key` differs from the mesh it holds (a re-classified grid, another plane) it asks
 *   for the mesh too, `GET /api/fast/slice` (transport version 2, read by `parseGeometry` like a
 *   results slice). Values whose count is not the mesh's are dropped and the mesh fetched again, so
 *   colours never land on the wrong vertices;
 * * one fetch at a time: a frame that arrives while one is under way is folded into the next;
 * * the colours are the results' colour map over the frame's own range (`patchTint`), and that range
 *   goes to the view store for the panel's legend;
 * * the plane is drawn in every stage (`fitTargets.ts` `isOverlaySource`): a preview runs while the
 *   body is being made, where the scene draws the geometry layer and no mesh source;
 * * while the plane is shown the bodies are drawn see-through (`FADED_WALL_OPACITY`, as a results slice
 *   fades the walls), since the plane lies inside them; they come back as the tree has them on stop;
 * * `fast.stopped` removes the plane, and a fetch still under way then draws nothing (it used to put
 *   the plane back after the stop). It is also the one place Fast events reach the view store;
 * * 204 from a route is "no plane now": the preview is stopping, or its plane holds no fluid yet.
 */

import type { StokesClient } from "../api/client";
import { FAST_SOURCE, readFrame, readStatus } from "../fast/fastModel";
import { useFastStore } from "../fast/fastStore";
import { SMOOTH, patchTint } from "../panels/fieldsModel";
import { FADED_WALL_OPACITY } from "../panels/sliceModel";
import { parseField } from "../state/fieldCodec";
import { useStore } from "../state/store";
import { parseGeometry } from "./geometry";
import { styleOf } from "./parts";
import type { PartStyle, SceneManager } from "./scene";

/** Unlit, as every results cut: the colour map is the information. */
const FAST_STYLE: PartStyle = { color: "#9aa3ad", visible: true, opacity: 1, unlit: true };

export class FastLayer {
  private key = "";
  private vertices = 0;
  private busy = false;
  private again = false;
  private disposed = false;
  /** True from a frame until the stop: what a fetch that returns late checks before it draws. */
  private live = false;
  private readonly faded = new Set<string>();
  private readonly offEvent: () => void;
  private readonly offStore: () => void;

  constructor(
    private readonly client: StokesClient,
    private readonly scene: SceneManager,
  ) {
    this.offEvent = client.onEvent((event, data) => {
      const store = useFastStore.getState();
      if (event === "fast.frame") {
        store.setFrame(readFrame(data));
        this.live = true;
        void this.refresh();
      } else if (event === "fast.stopped") {
        store.setStatus(readStatus(data));
        store.setFrame(null);
        this.live = false;
        this.clear();
      } else if (event === "fast.warning") {
        store.setWarning(typeof data.message === "string" ? data.message : "");
      }
    });
    this.offStore = useFastStore.subscribe((now, before) => {
      if (now.field !== before.field && this.live) void this.refresh();
    });
  }

  dispose(): void {
    this.disposed = true;
    this.live = false;
    this.offEvent();
    this.offStore();
    this.clear();
  }

  private async refresh(): Promise<void> {
    if (this.busy) {
      this.again = true;
      return;
    }
    this.busy = true;
    try {
      await this.draw();
    } catch {
      // A plane that could not be fetched keeps the last one; the next frame tries again.
    } finally {
      this.busy = false;
      if (this.again && !this.disposed) {
        this.again = false;
        void this.refresh();
      }
    }
  }

  private async draw(): Promise<void> {
    const field = useFastStore.getState().field;
    const values = await this.fetch(`/api/fast/field?field=${encodeURIComponent(field)}`);
    if (values === null || !this.live) return;
    const key = values.key;
    if (key !== this.key) {
      // The temperature has a mesh of its own (it covers the solids): the mesh is asked for by value.
      const mesh = await this.fetch(`/api/fast/slice?field=${encodeURIComponent(field)}`);
      if (mesh === null || !this.live || mesh.key !== key) return;
      const parsed = parseGeometry(mesh.bytes);
      this.scene.setSource(FAST_SOURCE, parsed.groups, ["Fast mode"], FAST_STYLE);
      this.vertices = parsed.groups.reduce((sum, g) => sum + g.vertices, 0);
      this.key = key;
    }
    const decoded = parseField(values.bytes);
    if (decoded.count !== this.vertices) {
      this.key = ""; // another mesh than the one held: fetch it next time
      return;
    }
    // The colour map, its direction and its bands are the Results looks' own (Display > colour map),
    // so a preview and a solved case are read in one palette.
    const view = useStore.getState().results.view;
    const tint = patchTint(decoded, { min: decoded.min, max: decoded.max }, this.vertices, view.bands ?? SMOOTH, view.map, view.reverse === true);
    this.scene.setSourceVertexTint(FAST_SOURCE, tint);
    useFastStore.getState().setRange({ field, min: decoded.min, max: decoded.max });
    this.fadeBodies();
  }

  /** The bodies see-through, so the plane inside them shows; again on every frame, as a tree change
   * restyles them. */
  private fadeBodies(): void {
    for (const node of useStore.getState().partNodes) {
      if (!this.scene.hasSource(node.id)) continue;
      const style = styleOf(node);
      this.scene.setSourceStyle(node.id, { ...style, opacity: Math.min(style.opacity, FADED_WALL_OPACITY) });
      this.faded.add(node.id);
    }
  }

  private restoreBodies(): void {
    const nodes = new Map(useStore.getState().partNodes.map((n) => [n.id, n]));
    for (const id of this.faded) {
      const node = nodes.get(id);
      if (node !== undefined && this.scene.hasSource(id)) this.scene.setSourceStyle(id, styleOf(node));
    }
    this.faded.clear();
  }

  private async fetch(route: string): Promise<{ bytes: ArrayBuffer; key: string } | null> {
    const res = await fetch(this.client.apiBase.replace(/\/$/, "") + route, {
      headers: { "X-Stokes-Token": this.client.token },
      cache: "no-store",
    });
    if (!res.ok || res.status === 204) return null;
    return { bytes: await res.arrayBuffer(), key: res.headers.get("X-Fast-Key") ?? "" };
  }

  private clear(): void {
    this.key = "";
    this.vertices = 0;
    if (this.scene.hasSource(FAST_SOURCE)) this.scene.removeSource(FAST_SOURCE);
    useFastStore.getState().setRange(null);
    this.restoreBodies();
  }
}
