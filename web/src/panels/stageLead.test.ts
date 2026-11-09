import { describe, expect, it } from "vitest";
import { STAGE_LEAD, STAGE_WORDS, stageOfStep } from "./stageLead";

describe("a stage's lead line", () => {
  it("names the Mesh stage for a mesh step, so the card can tell it is a step from elsewhere", () => {
    // **The case Claude saw**: under a GEOMETRY heading the card said "The mesh is out of date" - a
    // step from the NEXT stage taken as the geometry stage's answer.
    expect(stageOfStep("mesh.generate")).toBe("Mesh");
    expect(stageOfStep("geometry.import")).toBe("Geometry");
    expect(stageOfStep("solve.run")).toBe("Solve");
  });

  it("says nothing it does not know, rather than guessing a stage", () => {
    // An unknown prefix must not be attributed to the current stage: a wrong attribution would
    // suppress the lead line exactly when it is needed.
    expect(stageOfStep("something.else")).toBeNull();
    expect(stageOfStep("")).toBeNull();
  });

  it("has a line for every stage", () => {
    for (const stage of ["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"] as const) {
      expect(STAGE_LEAD[stage].length).toBeGreaterThan(20);
    }
  });

  it("has a few words for every stage, which the card shows while the sentence is its tooltip", () => {
    for (const stage of ["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"] as const) {
      expect(STAGE_WORDS[stage].split(/\s+/).length).toBeLessThanOrEqual(4);
      expect(STAGE_WORDS[stage].length).toBeLessThan(STAGE_LEAD[stage].length);
    }
  });
});
