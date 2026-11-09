/**
 * The quality overlay's pure state (brief M-507 §UI 3): shift+click accumulation, Esc, and the
 * disposal rules — decided here, so the viewer half only draws.
 */
import { describe, expect, it } from "vitest";
import {
  EMPTY_OVERLAY,
  clickBin,
  clearOverlay,
  hasLit,
  overlayForNewResult,
  sourceName,
} from "./qualityOverlayModel";

describe("clickBin", () => {
  it("a plain click lights one bin", () => {
    const state = clickBin(EMPTY_OVERLAY, "non_ortho", 50, 60, false);
    expect(state.lit).toEqual([{ metric: "non_ortho", from: 50, to: 60 }]);
  });

  it("shift+click adds a bin to a growing selection", () => {
    let state = clickBin(EMPTY_OVERLAY, "non_ortho", 50, 60, true);
    state = clickBin(state, "non_ortho", 60, 70, true);
    expect(state.lit).toHaveLength(2);
  });

  it("a plain click replaces the selection", () => {
    let state = clickBin(EMPTY_OVERLAY, "non_ortho", 50, 60, false);
    state = clickBin(state, "skewness", 1, 2, false);
    expect(state.lit).toEqual([{ metric: "skewness", from: 1, to: 2 }]);
  });

  it("a second click on a lit bin clears it, additive or not", () => {
    let state = clickBin(EMPTY_OVERLAY, "non_ortho", 50, 60, true);
    state = clickBin(state, "non_ortho", 50, 60, true);
    expect(state.lit).toEqual([]);
  });
});

describe("the overlay's life", () => {
  it("Esc clears the lit bins", () => {
    const state = clickBin(EMPTY_OVERLAY, "non_ortho", 50, 60, false);
    expect(clearOverlay(state).lit).toEqual([]);
  });

  it("a new result disposes everything, the toggle with it", () => {
    let state = clickBin(EMPTY_OVERLAY, "non_ortho", 50, 60, false);
    state = { ...state, throughWalls: true };
    expect(overlayForNewResult(state)).toEqual(EMPTY_OVERLAY);
  });
});

describe("the source name", () => {
  it("the scene source kind is quality:<metric>:<from>:<to>", () => {
    expect(sourceName({ metric: "non_ortho", from: 50, to: 60 })).toBe("quality:non_ortho:50:60");
  });

  it("hasLit", () => {
    expect(hasLit(EMPTY_OVERLAY)).toBe(false);
    expect(hasLit(clickBin(EMPTY_OVERLAY, "skewness", 1, 2, false))).toBe(true);
  });
});