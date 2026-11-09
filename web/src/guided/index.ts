/**
 * Guided mode's public face. Other code imports from here and from nowhere deeper:
 *
 * * `openGuidedStep(name)` opens a step's card (`geometry`, `fluid`, `boundaries`, `mesh`, `run`, `results`,
 *   or the older names `import` and `define`); it returns false in Full mode, so a caller can fall back.
 * * `useGuidedMode()` / `currentMode()` say which mode the window is in.
 *
 * The components (`GuidedLayer`, `TreeToggle`) are imported by the app shell directly, so this file stays
 * light enough for the files that only want to open a card.
 */
export { currentMode, openGuidedStep, useGuidedMode } from "./modeStore";
export type { GuidedStepId } from "./guidedModel";
export type { GuidedMode } from "./modeModel";
