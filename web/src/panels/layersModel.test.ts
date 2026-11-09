/**
 * The Boundary layers page's pure model (brief M-505): the placeholders, the coverage tone, the
 * request-until-measured rule, and the helper's lines.
 */
import { describe, expect, it } from "vitest";
import {
  HELPER_ASSUMPTIONS,
  addLayersVisible,
  addThreeLayersPayload,
  coverageView,
  helperLine,
  inheritedFrom,
  nonWallLine,
  requestedWords,
  wallViews,
  type LayerSettings,
} from "./layersModel";

const SETTINGS: LayerSettings = {
  layers: 3,
  layer_mode: "final_ratio",
  expansion_ratio: 1.2,
  final_ratio: 0.3,
  first_layer_height: 0,
  min_thickness_ratio: 0.1,
};

describe("inheritedFrom", () => {
  it("names where an inherited count comes from", () => {
    expect(inheritedFrom(null, 3, false)).toBe("from the mesh settings: 3");
    expect(inheritedFrom(null, 5, true)).toBe("from the body");
    expect(inheritedFrom(2, 3, false)).toBe(""); // the field holds its own value
  });
});

describe("wallViews", () => {
  it("the override maps to its word: 0 is none, empty is inherit", () => {
    const views = wallViews(
      [
        { name: "walls", override: null },
        { name: "hot", override: 0 },
      ],
      SETTINGS,
      false,
    );
    expect(views[0].effective).toBe(3);
    expect(views[0].overrideWord).toBe("inherit");
    expect(views[1].effective).toBe(0);
    expect(views[1].overrideWord).toBe("none");
  });
});

describe("nonWallLine", () => {
  it("lists the boundaries layers cannot grow on", () => {
    expect(nonWallLine(["inlet", "outlet"])).toBe("inlet and outlet: no layers (not walls)");
    expect(nonWallLine(["inlet"])).toBe("inlet: no layers (not walls)");
    expect(nonWallLine([])).toBe("");
  });
});

describe("coverageView", () => {
  it("warning tone under 80 %, plain at or above", () => {
    const coverage = [
      { patch: "walls", coverage_percent: 64 },
      { patch: "hot", coverage_percent: 96 },
    ];
    expect(coverageView("walls", coverage)).toEqual({
      text: "layers on 64 % of this wall",
      warning: true,
    });
    expect(coverageView("hot", coverage)).toEqual({
      text: "layers on 96 % of this wall",
      warning: false,
    });
  });

  it("no mesh, no coverage column at all", () => {
    expect(coverageView("walls", null)).toBeNull();
    expect(coverageView("unmeshed", [{ patch: "walls", coverage_percent: 90 }])).toBeNull();
  });
});

describe("requestedWords — the 'layers added' rule", () => {
  it("says REQUESTED until a mesh measures coverage, and never says added", () => {
    const line = requestedWords(SETTINGS, 2);
    expect(line).toBe("3 layers requested on 2 walls");
    expect(line.toLowerCase()).not.toContain("added");
    expect(requestedWords({ ...SETTINGS, layers: 0 }, 2)).toBe("");
  });
});

describe("the Add 3 layers button", () => {
  it("one mesh.configure with the three values, visible only when layers are off", () => {
    expect(addThreeLayersPayload()).toEqual({
      layers: 3,
      layer_mode: "final_ratio",
      expansion_ratio: 1.2,
      final_ratio: 0.3,
    });
    expect(addLayersVisible({ ...SETTINGS, layers: 0 })).toBe(true);
    expect(addLayersVisible(SETTINGS)).toBe(false);
  });
});

describe("the y+ helper's lines", () => {
  it("the result line carries the height, the Reynolds number and the method", () => {
    expect(helperLine({ first_height: 7.16e-5, reynolds: 636216, method: "flat plate" })).toBe(
      "First layer height 71.6 µm — Re 636k · flat plate",
    );
  });

  it("the assumptions line is ALWAYS under the result", () => {
    expect(HELPER_ASSUMPTIONS).toContain("flat plate");
    expect(HELPER_ASSUMPTIONS).toContain("check it against your geometry");
  });
});