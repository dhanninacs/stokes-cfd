/**
 * Asks for the volume mesh at the section plane and hands it to the scene (`meshCut.ts`).
 *
 * Active while a section is on and the mesh is what the view shows: the Mesh stage, or "Mesh with cells"
 * anywhere. A move of the plane is debounced; the first cut of a large mesh answers `cutting` (the host is
 * reading the mesh) and is asked again until it is in. Leaving those conditions takes the cut away. Nothing
 * here is a project command: `mesh.section` reads.
 */
import type { StokesClient } from "../api/client";
import { useStore, type StokesState } from "../state/store";
import { readMeshCut, wantedCut, type MeshCut } from "./meshCut";

export type MeshSectionScene = { setMeshCut(cut: MeshCut | null): void };

const DEBOUNCE_MS = 250;
const POLL_MS = 1000;
const POLL_LIMIT = 300;

export class MeshSectionController {
  private readonly unsubscribe: () => void;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private seq = 0;
  private key = "";

  constructor(
    private readonly client: StokesClient,
    private readonly scene: MeshSectionScene,
  ) {
    this.unsubscribe = useStore.subscribe((state, previous) => {
      if (state.section !== previous.section || state.stage !== previous.stage || state.meshDisplay !== previous.meshDisplay) this.sync(state);
    });
    this.sync(useStore.getState());
  }

  private sync(state: StokesState): void {
    const wanted = wantedCut(state);
    const key = wanted === null ? "" : `${wanted.axis}:${wanted.offset}:${state.section?.invert === true}`;
    if (key === this.key) return;
    this.key = key;
    if (wanted === null) {
      this.seq += 1;
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = null;
      this.scene.setMeshCut(null);
      return;
    }
    this.schedule(wanted);
  }

  private schedule(wanted: { axis: "x" | "y" | "z"; offset: number }): void {
    if (this.timer !== null) clearTimeout(this.timer);
    const mine = ++this.seq;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.ask(wanted, mine, 0);
    }, DEBOUNCE_MS);
  }

  private async ask(wanted: { axis: "x" | "y" | "z"; offset: number }, mine: number, tries: number): Promise<void> {
    let raw: unknown;
    try {
      raw = await this.client.call("mesh.section", { axis: wanted.axis, offset: wanted.offset });
    } catch {
      if (mine === this.seq) this.scene.setMeshCut(null);
      return;
    }
    if (mine !== this.seq) return;
    const state = typeof raw === "object" && raw !== null ? (raw as { state?: unknown }).state : null;
    if (state === "cutting" && tries < POLL_LIMIT) {
      this.timer = setTimeout(() => {
        this.timer = null;
        void this.ask(wanted, mine, tries + 1);
      }, POLL_MS);
      return;
    }
    this.scene.setMeshCut(readMeshCut(raw));
  }

  dispose(): void {
    this.seq += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.unsubscribe();
  }
}
