/**
 * The stage map of `stageMap.ts` (T-040): which stage owns a node path (the current stage wins a
 * tie), how many levels of a tree open when it loads, and which groups open when a stage is chosen.
 * The document's own table is in force, so the tests read it through the module.
 */
import { describe, expect, it } from "vitest";
import { expansionFor, setStageMap, stageExpanded, stageOfPath, stageOpens } from "./stageMap";

/** The stages as the store lists them. */
const STAGES = ["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"];

describe("stageOfPath", () => {
  it("gives a node under a top-level group its stage", () => {
    expect(stageOfPath("/geometry/box", "Physics", STAGES)).toBe("Geometry");
    expect(stageOfPath("/physics/materials/water", "Geometry", STAGES)).toBe("Physics");
  });

  it("gives a group of a stage's own path to that stage", () => {
    expect(stageOfPath("/mesh/snappy", "Physics", STAGES)).toBe("Mesh");
    expect(stageOfPath("/runs", "Solve", STAGES)).toBe("Solve");
  });

  it("keeps the current stage when both it and another own the path", () => {
    // /runs is in Solve's list and in Results': Results stays when it is current, and a run read
    // from the tree does not move the chip to Solve.
    expect(stageOfPath("/runs/base/residuals", "Results", STAGES)).toBe("Results");
    expect(stageOfPath("/runs", "Solve", STAGES)).toBe("Solve");
    // ... but only when the current stage owns it too.
    expect(stageOfPath("/runs", "Mesh", STAGES)).toBe("Solve");
  });

  it("is null for a path no stage works in", () => {
    expect(stageOfPath("/other", "Geometry", STAGES)).toBeNull();
    expect(stageOfPath("/geometryx", "Geometry", STAGES)).toBeNull();
  });
});

describe("stageOpens", () => {
  it("is true for a listed group and for one holding a listed sub-group", () => {
    expect(stageOpens("Physics", "/physics")).toBe(true);
    expect(stageOpens("Physics", "/regions")).toBe(true);
    expect(stageOpens("Physics", "/geometry")).toBe(false);
  });
});

describe("expansionFor", () => {
  const root = {
    id: "root",
    path: "",
    children: [
      { id: "physics", path: "/physics", children: [{ id: "parameters", path: "/physics/parameters", children: [{ id: "turbulence", path: "/physics/parameters/turbulence", children: [] }] }] },
      { id: "mesh", path: "/mesh", children: [] },
    ],
  };

  it("opens depth levels from the root down", () => {
    expect(expansionFor(root, 0)).toEqual({});
    expect(Object.keys(expansionFor(root, 1)).sort()).toEqual(["mesh", "physics"]);
  });

  it("opens the groups and the ones inside them at depth 2", () => {
    expect(expansionFor(root, 2)).toEqual({ physics: true, mesh: true, parameters: true });
  });

  it("has nothing to open with no tree", () => {
    expect(expansionFor(null, 2)).toEqual({});
  });
});

describe("stageExpanded", () => {
  const root = {
    id: "root",
    path: "",
    children: [
      { id: "physics", path: "/physics", children: [{ id: "materials", path: "/physics/materials", children: [{ id: "water", path: "/physics/materials/water", children: [] }] }] },
      { id: "regions", path: "/regions", children: [] },
      { id: "mesh", path: "/mesh", children: [] },
    ],
  };

  it("opens the stage's groups, folds the others, and leaves what is below as it was", () => {
    const next = stageExpanded("Physics", root, { water: false, mesh: true });
    expect(next.physics).toBe(true);
    expect(next.regions).toBe(true);
    expect(next.mesh).toBe(false); // the other stage folds
    expect(next.water).toBe(false); // untouched
  });

  it("folds every group of a stage that works in none of them", () => {
    const next = stageExpanded("Geometry", root, { materials: true });
    expect(next.physics).toBe(false);
    expect(next.mesh).toBe(false);
    expect(next.materials).toBe(true);
  });

  it("follows the document's table when it sets one", () => {
    setStageMap({ Study: ["/studies"] }, false, 1);
    try {
      expect(stageOfPath("/studies/run1", "Geometry", ["Geometry", "Study"])).toBe("Study");
      expect(stageOpens("Study", "/studies")).toBe(true);
      expect(stageExpanded("Study", root, {}).mesh).toBe(false);
    } finally {
      setStageMap(
        { Geometry: ["/geometry"], Mesh: ["/mesh"], Physics: ["/physics", "/regions"], Solve: ["/solver", "/monitors", "/runs"], Results: ["/scenes", "/runs"], Explore: ["/studies"] },
        true,
        2,
      );
    }
  });

  it("opens below a top-level group only as far as the table names", () => {
    // The recursion opens a child when the stage's table lists it, `under(group, child.path)`: a
    // listed sub-group opens, a group that merely lies below a listed path does not. T-040 recorded
    // this because the docstring on `stageExpanded` reads the other way round; see the report.
    setStageMap({ Physics: ["/physics", "/physics/materials"] }, true, 2);
    try {
      const next = stageExpanded("Physics", root, {});
      expect(next.materials).toBe(true); // named in the table
      expect(next.water).toBeUndefined(); // only below the named group
    } finally {
      setStageMap(
        { Geometry: ["/geometry"], Mesh: ["/mesh"], Physics: ["/physics", "/regions"], Solve: ["/solver", "/monitors", "/runs"], Results: ["/scenes", "/runs"], Explore: ["/studies"] },
        true,
        2,
      );
    }
  });
});