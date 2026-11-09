/**
 * Guided mode's own small state: Guided or Full, the step whose card is open, and whether that card
 * is folded to its title. View state only, like the pick mode: nothing here is written to the project.
 *
 * **`openGuidedStep(name)` is the one door other code uses** (the Next-step chip opens the Boundaries
 * card through it). It returns false when the window is in Full mode or the name is not a step, so a
 * caller can fall back to what it did before.
 *
 * Kept free of the card components on purpose: `guided/index.ts` re-exports this file, and the files
 * that import the index (the Next-step button) must not pull the cards in with it.
 */
import { create } from "zustand";
import { useStore } from "../state/store";
import { useWorkspace } from "../workspace/store";
import { STEP_STAGE, stepFromName, type GuidedStepId } from "./guidedModel";
import { effectiveMode, readStoredMode, writeStoredMode, type GuidedMode } from "./modeModel";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

interface GuidedUi {
  /** The user's choice of mode, or null while they have made none (the document's default stands). */
  mode: GuidedMode | null;
  /** The step whose card is open, or null before the first one is chosen for the project. */
  step: GuidedStepId | null;
  /** The card is folded to its title. */
  folded: boolean;
}

export const useGuided = create<GuidedUi>(() => ({ mode: readStoredMode(storage()), step: null, folded: false }));

/** The mode now, for a component: the user's choice, else the workspace document's, else Guided. */
export function useGuidedMode(): GuidedMode {
  const chosen = useGuided((s) => s.mode);
  const document = useWorkspace((s) => s.document.mode);
  return effectiveMode(chosen, document);
}

/** The mode now, outside React. */
export function currentMode(): GuidedMode {
  return effectiveMode(useGuided.getState().mode, useWorkspace.getState().document.mode);
}

/** Choose a mode and keep the choice. Putting the tree away or bringing it back is `treeToggle.ts`'s. */
export function chooseMode(mode: GuidedMode): void {
  writeStoredMode(storage(), mode);
  useGuided.setState({ mode });
}

/** Open a step's card and put the stage it works in in front. */
export function selectStep(id: GuidedStepId): void {
  useGuided.setState({ step: id, folded: false });
  const stage = STEP_STAGE[id];
  const state = useStore.getState();
  if (state.stage !== stage) state.setStage(stage);
}

/**
 * Open the card of a step, by name: a guided step's id, or the older names `define` and `import`.
 * Returns true when the card was opened; false in Full mode or for a name that is no step.
 */
export function openGuidedStep(name: string): boolean {
  const id = stepFromName(name);
  if (id === null || currentMode() !== "guided") return false;
  selectStep(id);
  return true;
}
