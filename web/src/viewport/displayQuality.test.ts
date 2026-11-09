import { describe, expect, it } from "vitest";
import { effectiveDisplayQuality } from "./displayQuality";

describe("reduced-detail viewport profile", () => {
  it("keeps full display preferences when disabled", () => {
    expect(effectiveDisplayQuality({ edges: true, shadow: true, reflections: true })).toEqual({
      axes: true, edges: true, shadow: true, reflections: true,
    });
  });

  it("temporarily removes costly overlays and lighting while preserving user preferences", () => {
    expect(effectiveDisplayQuality({ lowDetail: true, edges: true, shadow: true, reflections: true, axisLines: false })).toEqual({
      axes: false, edges: false, shadow: false, reflections: false,
    });
    expect(effectiveDisplayQuality({ lowDetail: false, edges: true, shadow: true, reflections: true })).toMatchObject({
      edges: true, shadow: true, reflections: true,
    });
  });
});
