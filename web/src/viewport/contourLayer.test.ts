/** 3-D contours: the levels a set holds, the key it is fetched under, and the viewport's line about it. */
import { describe, expect, it } from "vitest";
import { contourSource, contourValues } from "../panels/isoModel";
import { contourKey, contoursLine } from "./contourLayerModel";

describe("3-D contours", () => {
  it("spaces the levels evenly inside the range, never at its ends", () => {
    expect(contourValues({ min: 300, max: 350 }, 4)).toEqual([310, 320, 330, 340]);
    expect(contourValues({ min: 1, max: 1 }, 3)).toEqual([]);
    expect(contourSource(2)).toBe("mesh:contour:2");
  });

  it("is a new set for new values, not for a new opacity", () => {
    const set = { run: "r1", field: "T", units: "K", values: [310, 320], opacity: 0.35 };
    expect(contourKey(set)).toBe(contourKey({ ...set, opacity: 1 }));
    expect(contourKey(set)).not.toBe(contourKey({ ...set, values: [310] }));
  });

  it("says how many levels are in and which are still being cut", () => {
    const set = { run: "r1", field: "T", units: "K", values: [310, 320, 330], opacity: 0.35 };
    expect(contoursLine(set, { key: "k", shown: 1, empty: 0, failed: 0, total: 3 })).toBe("contours T: 1 level, 310 to 330 K · cutting 2 of 3");
    expect(contoursLine(set, { key: "k", shown: 2, empty: 0, failed: 1, total: 3 })).toBe("contours T: 2 levels, 310 to 330 K · 1 not cut");
  });
});
