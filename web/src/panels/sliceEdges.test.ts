import { describe, expect, it } from "vitest";
import { NO_RESULTS, setSliceEdges } from "./fieldsModel";

describe("setSliceEdges (the mid-plane slice's cell edges)", () => {
  it("are off in a view that has never heard of them, and turn on and off", () => {
    expect(NO_RESULTS.view.sliceEdges).toBeUndefined();
    const on = setSliceEdges(NO_RESULTS, true);
    expect(on.view.sliceEdges).toBe(true);
    expect(setSliceEdges(on, false).view.sliceEdges).toBe(false);
  });

  it("keeps the same object when nothing changes, so no reader wakes for nothing", () => {
    expect(setSliceEdges(NO_RESULTS, false)).toBe(NO_RESULTS);
    const on = setSliceEdges(NO_RESULTS, true);
    expect(setSliceEdges(on, true)).toBe(on);
  });
});
