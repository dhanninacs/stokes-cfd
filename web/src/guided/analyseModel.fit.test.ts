import { describe, expect, it } from "vitest";
import { fitSurfaces } from "./analyseModel";

describe("what a Fit spreads the colours over", () => {
  it("is the slice on screen, with its plane when it has its own", () => {
    expect(fitSurfaces(true, null, ["n_1"])).toEqual({ surfaces: ["slice"] });
    expect(fitSurfaces(true, "0,0,0,1,0,0", [])).toEqual({ surfaces: ["slice"], plane: "0,0,0,1,0,0" });
  });

  it("is every boundary with no slice, and nothing with none", () => {
    expect(fitSurfaces(false, null, ["n_1", "n_2"])).toEqual({ surfaces: ["n_1", "n_2"] });
    expect(fitSurfaces(false, null, [])).toBeNull();
  });
});
