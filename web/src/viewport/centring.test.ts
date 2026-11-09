import { describe, expect, it } from "vitest";
import { asViewOffsetOrigin, centringShift, type CanvasPoint, type FreeRect } from "./centring";

/**
 * The closed form, tested in arithmetic (Claude's 2026-10-19 brief: "test first, no renderer").
 *
 * A pretend camera maps a 3D point to canvas pixels by a fixed projection plus the shift the offset
 * applies. That is exactly the property the real camera has - a principal-point shift translates every
 * point by the same vector - so the arithmetic here is the behaviour, not a model of it.
 */
const CANVAS = { width: 1600, height: 900 };

/** A stand-in projection: an oblique box, off-centre in the canvas, as the demo's pipe is. */
const projectWith =
  (shift: { dx: number; dy: number }) =>
  (point: unknown): CanvasPoint => {
    const [x, y, z] = point as [number, number, number];
    // Never a real perspective; only the INVARIANT matters - a translation moves every point equally.
    return { x: 700 + x * 3 + z * 1.5 + shift.dx, y: 380 + y * 2 - z * 2 + shift.dy };
  };

/** The eight corners of the demo's pipe, in metres. */
const PIPE: [number, number, number][] = [
  [0, 0, 0], [0.5, 0, 0], [0, 0.05, 0], [0.5, 0.05, 0],
  [0, 0, 0.05], [0.5, 0, 0.05], [0, 0.05, 0.05], [0.5, 0.05, 0.05],
];

const FREE_RECTS: FreeRect[] = [
  { x: 300, y: 0, width: 1300, height: 900 },
  { x: 0, y: 0, width: 1600, height: 900 },
  { x: 300, y: 0, width: 980, height: 900 },
];

describe("the centring shift", () => {
  it("puts the projected centre on the free centre, within 1 px, for every free rect", () => {
    for (const free of FREE_RECTS) {
      const { dx, dy } = centringShift(PIPE, projectWith({ dx: 0, dy: 0 }), free);
      // Apply the shift and re-measure: this is the assertion Claude asked for.
      const after = centringShift(PIPE, projectWith({ dx, dy }), free);
      expect(Math.abs(after.dx)).toBeLessThan(1);
      expect(Math.abs(after.dy)).toBeLessThan(1);
    }
  });

  it("is idempotent: applying it again asks for no further movement", () => {
    const free = FREE_RECTS[0]!;
    const first = centringShift(PIPE, projectWith({ dx: 0, dy: 0 }), free);
    const second = centringShift(PIPE, projectWith(first), free);
    // **The property my third attempt broke**: it measured with the offset still in place, so the
    // residual never reached zero and a second call would ask for more.
    expect(Math.hypot(second.dx, second.dy)).toBeLessThan(1);
  });

  it("asks for nothing when the box is already centred", () => {
    const free = { x: 0, y: 0, width: 1600, height: 900 };
    const centred = centringShift(PIPE, projectWith({ dx: 0, dy: 0 }), free);
    const again = centringShift(PIPE, projectWith(centred), free);
    expect(Math.abs(again.dx) + Math.abs(again.dy)).toBeLessThan(1e-6);
  });

  it("moves the image the opposite way from the origin three.js takes", () => {
    // **The sign that cost a round**: three.js moves the content by MINUS the origin, so a shift that
    // must move the image +10 px is written as an origin of -10.
    expect(asViewOffsetOrigin(10, -4)).toEqual({ x: -10, y: 4 });
  });

  it("has nothing to say about an empty model", () => {
    expect(centringShift([], projectWith({ dx: 0, dy: 0 }), FREE_RECTS[0]!)).toEqual({ dx: 0, dy: 0 });
  });
});
