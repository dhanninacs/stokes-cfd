import { describe, expect, it } from "vitest";
import { placeAt } from "./menuPlacement";

const viewport = { w: 1000, h: 800 };
const size = { w: 200, h: 300 };

describe("placeAt", () => {
  it("puts the top-left corner at the point when the surface fits", () => {
    expect(placeAt({ x: 100, y: 120 }, size, viewport)).toEqual({ x: 100, y: 120 });
  });

  it("flips to the left of the point when it would pass the right edge", () => {
    expect(placeAt({ x: 900, y: 120 }, size, viewport)).toEqual({ x: 700, y: 120 });
  });

  it("flips above the point when it would pass the bottom edge", () => {
    expect(placeAt({ x: 100, y: 700 }, size, viewport)).toEqual({ x: 100, y: 400 });
  });

  it("stays inside the padding on every edge", () => {
    // flipping would leave the left or top edge: the surface is held 8 px inside instead
    expect(placeAt({ x: 150, y: 150 }, { w: 400, h: 400 }, { w: 300, h: 300 })).toEqual({ x: 8, y: 8 });
    // a point at the far corner flips on both axes, then is held 8 px inside the right and bottom edges
    expect(placeAt({ x: 995, y: 795 }, size, viewport)).toEqual({ x: 792, y: 492 });
    // a point outside the window is pulled in
    expect(placeAt({ x: -50, y: -50 }, size, viewport)).toEqual({ x: 8, y: 8 });
  });
});
