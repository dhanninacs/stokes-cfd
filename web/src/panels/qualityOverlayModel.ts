/**
 * The quality overlay's pure state (brief M-507 §UI 3): which bins are lit, shift+click
 * accumulation, Esc clearing, and the disposal rules. **No fetch and no scene here** — the
 * viewer half routes the lit bins to `quality:<metric>:<bin>` sources when the faces route
 * ships; this module decides WHAT is lit, and every rule is test-pinned.
 */

/** One lit selection: a metric's bin range. */
export interface LitBin {
  metric: string;
  from: number;
  to: number;
}

export interface QualityOverlayState {
  /** The lit bins, in selection order (shift+click appends). */
  lit: LitBin[];
  /** Whether the overlay draws through walls (depth test off). */
  throughWalls: boolean;
}

export const EMPTY_OVERLAY: QualityOverlayState = { lit: [], throughWalls: false };

/** Same metric and same bin range. */
function sameBin(a: LitBin, b: LitBin): boolean {
  return a.metric === b.metric && a.from === b.from && a.to === b.to;
}

/**
 * A click on a bar: the bin becomes lit; **shift+click adds the bin** to a growing selection;
 * a plain click on a lit bin clears it; a plain click otherwise replaces the selection.
 */
export function clickBin(
  state: QualityOverlayState,
  metric: string,
  from: number,
  to: number,
  additive: boolean,
): QualityOverlayState {
  const bin: LitBin = { metric, from, to };
  if (state.lit.some((existing) => sameBin(existing, bin))) {
    // A second click on a lit bin always clears it — additive or not.
    return { ...state, lit: state.lit.filter((existing) => !sameBin(existing, bin)) };
  }
  if (additive) {
    return { ...state, lit: [...state.lit, bin] };
  }
  return { ...state, lit: [bin] };
}

/** Esc clears the overlay (brief M-507 §UI 3). */
export function clearOverlay(state: QualityOverlayState): QualityOverlayState {
  return { ...state, lit: [] };
}

/** The overlay is removed on a stage change or a new result (brief M-507 §UI 3). */
export function overlayForNewResult(state: QualityOverlayState): QualityOverlayState {
  // The through-walls toggle is the user's display preference within one result; the lit
  // selection belongs to the result's own numbers, so it goes. The toggle resets too: a new
  // mesh's faces are a new question, and the default is the honest depth-tested view.
  return { ...EMPTY_OVERLAY };
}

/** The scene source name for a lit bin, as the viewer half will register it. */
export function sourceName(bin: LitBin): string {
  return `quality:${bin.metric}:${bin.from}:${bin.to}`;
}

/** Whether the overlay has anything to draw. */
export function hasLit(state: QualityOverlayState): boolean {
  return state.lit.length > 0;
}