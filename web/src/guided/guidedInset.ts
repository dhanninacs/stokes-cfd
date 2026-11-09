/**
 * How much of the viewport Guided mode's furniture covers, so the camera's fit keeps the figure clear of it (R4f).
 *
 * **Measured in the browser (pipe3, 19:15):** the face-on fit framed the whole viewport width, so the step card
 * (the column at the left, under the strip) covered the pipe's left end. The `ContextToolbar` was already inset
 * by these two numbers; the fit's free rectangle takes them from the same place now.
 *
 * Full mode has neither card nor strip clearance to add: its docks already set the free rectangle.
 *
 * Import rule: no React, no store, no DOM (the numbers match `guided.css`: the 300 px stack at 12 px from the slot,
 * and the strip's 52 px row).
 */
import type { GuidedMode } from "./modeModel";

/** The card column: the stack's 300 px plus its 12 px margin (`.guided-stack` in guided.css). */
export const GUIDED_CARD_INSET = 312;
/** The strip's row across the top of the viewport: none, since the strip moved into the top bar (2026-11-03). */
export const GUIDED_STRIP_INSET = 0;

export interface GuidedInset {
  /** Pixels the card's column takes from the left of the free area (beyond the left dock). */
  left: number;
  /** Pixels the strip's row takes from the top of the free area. */
  top: number;
}

/** The inset for a mode: the card column and strip row in Guided mode (which shows its stack), nothing in Full. */
export function guidedInset(mode: GuidedMode): GuidedInset {
  return mode === "guided" ? { left: GUIDED_CARD_INSET, top: GUIDED_STRIP_INSET } : { left: 0, top: 0 };
}
