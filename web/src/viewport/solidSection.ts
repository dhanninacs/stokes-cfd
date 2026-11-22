/** Asks `geometry.section` for the plane on screen and hands the bodies' filled sections to the scene (`solidCut.ts`). */
import type { StokesClient } from "../api/client";
import { useStore, type StokesState } from "../state/store";
import { readSolidCuts, wantSolidCut, type SolidCut } from "./solidCut";

export type SolidSectionScene = { setSolidCuts(cuts: SolidCut[] | null): void };

const DEBOUNCE_MS = 200;

/** Asks `geometry.section` for the plane on screen and hands the fills to the scene. */
export class SolidSectionController {
  private readonly unsubscribe: () => void;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private seq = 0;

  constructor(
    private readonly client: StokesClient,
    private readonly scene: SolidSectionScene,
  ) {
    this.unsubscribe = useStore.subscribe((state, previous) => {
      if (state.section !== previous.section || state.stage !== previous.stage || state.meshDisplay !== previous.meshDisplay || state.tree !== previous.tree || state.hiddenNodes !== previous.hiddenNodes) this.sync(state);
    });
    this.sync(useStore.getState());
  }

  private sync(state: StokesState): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const mine = ++this.seq;
    if (!wantSolidCut(state) || state.section === null) {
      this.scene.setSolidCuts(null);
      return;
    }
    const { axis, offset } = state.section;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.client
        .call("geometry.section", { axis, offset })
        .then((raw) => {
          if (mine === this.seq) this.scene.setSolidCuts(readSolidCuts(raw, axis, offset));
        })
        .catch(() => {
          if (mine === this.seq) this.scene.setSolidCuts(null);
        });
    }, DEBOUNCE_MS);
  }

  dispose(): void {
    this.seq += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.unsubscribe();
  }
}
