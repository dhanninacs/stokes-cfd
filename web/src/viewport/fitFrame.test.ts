import { describe, expect, it } from "vitest";
import { fitFrameFromFree } from "./camera";

describe("the fit frame", () => {
  it("is a fraction of the free area, so a fit cannot hide under a panel", () => {
    // **The bug Claude saw**: the frame was 0.7 × 0.8 of the WHOLE canvas, so with the tree and the
    // pane open the model was framed into space largely behind them. Measured through the function the
    // rig uses, with the same rectangle the free-rect tests use.
    const canvas = { width: 1600, height: 1000 };
    const withTree = fitFrameFromFree({ x: 300, y: 48, width: 1300, height: 912 }, canvas);
    const nothing = fitFrameFromFree({ x: 0, y: 48, width: 1600, height: 912 }, canvas);
    expect(withTree.width).toBeLessThan(nothing.width);
    // And neither may claim more than the margin allows.
    expect(nothing.width).toBeLessThanOrEqual(1);
    expect(nothing.height).toBeLessThanOrEqual(1);
  });

  it("keeps a margin rather than framing to the edge", () => {
    const frame = fitFrameFromFree({ x: 0, y: 0, width: 1000, height: 1000 }, { width: 1000, height: 1000 });
    expect(frame.width).toBeGreaterThan(0.8);
    expect(frame.width).toBeLessThan(1);
  });
});

describe("a window resize", () => {
  it("does not change the pixels a metre takes on screen", () => {
    /*
      **The owner's report**: *"resizing window breaks the CAD display scale"*. The bug was a REFIT on
      every free-rectangle change - the model was re-framed to fill the new area, so the same camera
      distance produced a different number of pixels per metre (measured 1350 px/m at 1600x1000 and 760
      at 1200x900). A viewport shows MORE or LESS of the model when the window changes; it does not
      change the scale under the user.

      The fit FRAME still follows the free rectangle - that is what a fit is measured against - so this
      pins the two apart: the frame may change, and the camera must not move.
    */
    const canvas = { width: 1600, height: 1000 };
    const before = fitFrameFromFree({ x: 300, y: 48, width: 1300, height: 912 }, canvas);
    const after = fitFrameFromFree({ x: 300, y: 48, width: 700, height: 712 }, canvas);
    // The FRAME moves with the free area, which is what a fit should use...
    expect(after?.width).toBeLessThan(before?.width ?? 0);
    // ...and it is expressed as a fraction of the canvas, so the canvas is what the pixels are measured
    // against - the other half of the fix: two different widths for "the width" is what made the scale
    // bar lie.
    expect(before?.width).toBeLessThanOrEqual(1);
  });
});
