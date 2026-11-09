/**
 * The Sizing page's pure model (brief M-503): the placeholder words, the effective size, the
 * achieved level, the deviation flag, the estimate line, and the blockMesh note.
 */
import { describe, expect, it } from "vitest";
import {
  BLOCKMESH_NOTE,
  OVER_LIMIT_ADVICE,
  bodyViews,
  boundaryViews,
  estimateLine,
  formatMm,
  localSizesApply,
  placeholderFor,
} from "./sizingModel";

const INPUTS = { baseSize: 0.008, surfaceSize: 0, mesher: "snappy", levels: [] };

describe("placeholderFor", () => {
  it("says where an inherited size comes from, in the settings' words", () => {
    expect(placeholderFor(null, 0.004)).toBe("from the mesh settings: 4 mm");
    expect(placeholderFor(null, 0.0005)).toBe("from the mesh settings: 500 µm");
  });

  it("says nothing when the field holds its own value", () => {
    expect(placeholderFor(0.002, 0.004)).toBe("");
  });
});

describe("bodyViews", () => {
  it("resolves the domain body through the precedence and marks it editable", () => {
    const rows = [
      { id: "a", name: "housing", size: null, refinement: 0, isDomain: true },
      { id: "b", name: "cover", size: null, refinement: 0, isDomain: false },
    ];
    const views = bodyViews(rows, INPUTS, "a");
    expect(views[0].editable).toBe(true);
    expect(views[1].editable).toBe(false);
    // Nothing set: a wall-ish body at half the base... the body rule is surface_size > 0 else
    // base/2 for every body face — the same target the snapshot resolves.
    expect(views[0].effective).toBe(0.004);
    expect(views[0].fromSettings).toBe(true);
  });

  it("shows the achieved size and flags a deviation above a fifth", () => {
    // A 3 mm ask from an 8 mm base lands on 4 mm (level 1): 33 % off.
    const rows = [{ id: "a", name: "housing", size: 0.003, refinement: 0, isDomain: true }];
    const views = bodyViews(rows, INPUTS, "a");
    expect(views[0].achieved).toBe(0.004);
    expect(views[0].deviation).toBe(true);
  });

  it("a clean rounding is not a deviation", () => {
    const rows = [{ id: "a", name: "housing", size: 0.002, refinement: 0, isDomain: true }];
    expect(bodyViews(rows, INPUTS, "a")[0].deviation).toBe(false);
  });
});

describe("boundaryViews", () => {
  it("the boundary's own size wins; empty inherits", () => {
    const views = boundaryViews(
      [
        { name: "walls", size: 0.001 },
        { name: "inlet", size: null },
      ],
      INPUTS,
    );
    expect(views[0].effective).toBe(0.001);
    expect(views[1].effective).toBe(0.004); // the rule: half the base
    expect(views[1].level).toBe(1);
  });
});

describe("estimateLine", () => {
  it("says the band and the limit", () => {
    expect(estimateLine({ low: 180_000, high: 310_000, max_cells: 5_000_000, over: false })).toBe(
      "About 180k-310k cells (limit 5M)",
    );
  });
});

describe("the over-the-limit line", () => {
  it("carries the advice the brief gives", () => {
    expect(OVER_LIMIT_ADVICE).toBe("Raise the base size or the limit");
  });
});

describe("localSizesApply", () => {
  it("every shape-following mesher, with blockMesh told honestly", () => {
    expect(localSizesApply("snappy")).toBe(true);
    for (const id of ["cfmesh", "cfmesh_tet", "cfmesh_poly", "polyhex"]) expect(localSizesApply(id)).toBe(true);
    expect(localSizesApply("blockmesh")).toBe(false);
    expect(BLOCKMESH_NOTE).toBe("Local sizes apply to snappyHexMesh and the cfMesh types only");
  });
});

describe("formatMm", () => {
  it("millimetres when they read well, microns below", () => {
    expect(formatMm(0.004)).toBe("4 mm");
    expect(formatMm(0.0025)).toBe("2.5 mm");
    expect(formatMm(0.0005)).toBe("500 µm");
  });
});