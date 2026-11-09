import { describe, expect, it } from "vitest";
import { NO_LOADING, anyLoading, patchLoading } from "./loadingState";

describe("loadingState", () => {
  it("says whether anything is on the way", () => {
    expect(anyLoading(NO_LOADING)).toBe(false);
    expect(anyLoading({ ...NO_LOADING, cuts: 1 })).toBe(true);
    expect(anyLoading({ ...NO_LOADING, sliceFields: 2 })).toBe(true);
  });

  it("patches one layer's counts and leaves the others", () => {
    expect(patchLoading({ fields: 1, cuts: 2, sliceFields: 3 }, { cuts: 0, sliceFields: 1 })).toEqual({ fields: 1, cuts: 0, sliceFields: 1 });
  });

  it("returns null when nothing changes, so the store is not written", () => {
    expect(patchLoading(NO_LOADING, { fields: 0 })).toBeNull();
    expect(patchLoading(undefined, { cuts: 0, sliceFields: 0 })).toBeNull();
    expect(patchLoading(undefined, { fields: 2 })).toEqual({ fields: 2, cuts: 0, sliceFields: 0 });
  });
});
