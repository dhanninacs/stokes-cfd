import { describe, expect, it } from "vitest";
import { lineEnds } from "./lineLayer";

describe("lineEnds", () => {
  it("is the segment and the two ends as floats", () => {
    const drawn = lineEnds([0, 0, 0], [1, 2, 3]);
    expect(Array.from(drawn!.segment)).toEqual([0, 0, 0, 1, 2, 3]);
    expect(Array.from(drawn!.ends)).toEqual([0, 0, 0, 1, 2, 3]);
  });
  it("is nothing for a missing end, a zero length, or a point that is not a number", () => {
    expect(lineEnds(null, [1, 2, 3])).toBeNull();
    expect(lineEnds([1, 2, 3], null)).toBeNull();
    expect(lineEnds([1, 2, 3], [1, 2, 3])).toBeNull();
    expect(lineEnds([0, 0, 0], [Number.NaN, 0, 0])).toBeNull();
  });
});
