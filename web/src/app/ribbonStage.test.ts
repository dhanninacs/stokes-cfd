import { describe, expect, it } from "vitest";
import { ribbonClosesOnStage } from "./ribbonStage";

describe("the build ribbon and the stages", () => {
  it("folds away on leaving Geometry for a stage with its own tools", () => {
    for (const next of ["Physics", "Mesh", "Solve", "Results", "Explore"]) expect(ribbonClosesOnStage("Geometry", next)).toBe(true);
  });

  it("stays on in Geometry, and when the stage does not change", () => {
    expect(ribbonClosesOnStage("Mesh", "Geometry")).toBe(false);
    expect(ribbonClosesOnStage("Physics", "Physics")).toBe(false);
    expect(ribbonClosesOnStage("Geometry", "Geometry")).toBe(false);
  });
});
