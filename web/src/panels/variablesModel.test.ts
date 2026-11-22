import { describe, expect, it } from "vitest";
import { clippedEnds, legendTickCount } from "./legendModel";
import { groupVariables, matches, prettyUnits, readVariables, searchVariables, variableOf, variableTitle, type VariableInfo } from "./variablesModel";

const reply = {
  run: "r_1",
  kinematic: true,
  density: 1.2,
  references: { U_ref: 0.3, p_ref: 0 },
  variables: [
    { name: "U_mag", label: "Velocity magnitude", symbol: "|U|", group: "Velocity", units: "m/s", description: "The speed of the flow.", source: "run", available: true },
    { name: "U_x", label: "Velocity X", symbol: "Ux", group: "Velocity", units: "m/s", description: "x component", source: "run", available: true },
    { name: "p", label: "Static pressure", symbol: "p", group: "Pressure", units: "m2/s2", description: "The solver's pressure.", source: "run", available: true },
    { name: "Cp", label: "Pressure coefficient", symbol: "Cp", group: "Pressure", units: "", description: "(p − p_ref)/…", source: "derived", available: false, why: "no inlet" },
    { name: "", label: "dropped" },
    { name: "q", group: "Functions", source: "function" },
  ],
};

describe("the catalogue of variables", () => {
  it("reads the server's reply, dropping rows without a name and filling the rest", () => {
    const read = readVariables(reply)!;
    expect(read.variables.map((v) => v.name)).toEqual(["U_mag", "U_x", "p", "Cp", "q"]);
    expect(read.variables[3]).toMatchObject({ available: false, why: "no inlet", source: "derived" });
    expect(read.variables[4]).toMatchObject({ label: "q", symbol: "q", source: "function", units: "" });
    expect(read.references).toEqual({ U_ref: 0.3, p_ref: 0 });
    expect(readVariables({ run: 3 })).toBeNull();
    expect(readVariables(null)).toBeNull();
  });

  it("groups in the server's order and searches every word", () => {
    const vs = readVariables(reply)!.variables;
    expect(groupVariables(vs).map((g) => g.group)).toEqual(["Velocity", "Pressure", "Functions"]);
    expect(searchVariables(vs, "pressure").flatMap((g) => g.items.map((v) => v.name))).toEqual(["p", "Cp"]);
    expect(searchVariables(vs, "velocity x").flatMap((g) => g.items.map((v) => v.name))).toEqual(["U_x"]);
    expect(matches(vs[0]!, "")).toBe(true);
    expect(searchVariables(vs, "nothing like this")).toEqual([]);
  });
});

describe("words for a variable", () => {
  it("raises powers in units", () => {
    expect(prettyUnits("m2/s2")).toBe("m²/s²");
    expect(prettyUnits("kg/m3")).toBe("kg/m³");
    expect(prettyUnits("1/s")).toBe("1/s");
    expect(prettyUnits("m2/s3")).toBe("m²/s³");
    expect(prettyUnits("m-1")).toBe("m⁻¹");
    expect(prettyUnits("")).toBe("");
  });

  it("titles a field by its label, its symbol where the label lacks it, and its units", () => {
    const vs = readVariables(reply)!.variables;
    expect(variableTitle(variableOf(vs, "U_mag"), "U_mag", "m/s")).toBe("Velocity magnitude |U| (m/s)");
    expect(variableTitle(variableOf(vs, "p"), "p", "m2/s2")).toBe("Static pressure (m²/s²)");
    expect(variableTitle(variableOf(vs, "Cp"), "Cp", "")).toBe("Pressure coefficient Cp");
    expect(variableTitle(null, "alphat", "m2/s")).toBe("alphat (m²/s)");
    const unknown: VariableInfo | null = variableOf(null, "x");
    expect(unknown).toBeNull();
  });
});

describe("the legend's labels and its clipped ends", () => {
  it("labels band edges, every other one past eight bands", () => {
    expect(legendTickCount(0)).toBe(6);
    expect(legendTickCount(8)).toBe(9);
    expect(legendTickCount(4)).toBe(5);
    expect(legendTickCount(16)).toBe(9);
    expect(legendTickCount(12)).toBe(7);
  });

  it("says which end of the colour range cuts the data off", () => {
    expect(clippedEnds({ min: 1.28, max: 2.07 }, { min: 0, max: 2.24 })).toEqual({ above: true, below: true });
    expect(clippedEnds({ min: 0, max: 2.24 }, { min: 0, max: 2.24 })).toEqual({ above: false, below: false });
    expect(clippedEnds({ min: 0, max: 1 }, { min: 0, max: 1 + 1e-9 })).toEqual({ above: false, below: false });
    expect(clippedEnds({ min: 0, max: 1 }, null)).toEqual({ above: false, below: false });
  });
});

describe("clip to range", () => {
  it("leaves a value past an end without a tint, and keeps the ends otherwise", async () => {
    const { tintFor } = await import("../state/colormap");
    const { legendOf, setClip } = await import("./legendModel");
    const clipped = tintFor([-1, 0.5, 2], 0, 1, 0, "viridis", false, true);
    expect([clipped[3], clipped[7], clipped[11]]).toEqual([0, 255, 0]);
    const ends = tintFor([-1, 0.5, 2], 0, 1, 0, "viridis", false, false);
    expect([ends[3], ends[7], ends[11]]).toEqual([255, 255, 255]);
    const view = { field: "p", lock: null, slice: false, bands: 0 };
    expect(legendOf(view).clip).toBe(false);
    expect(legendOf(setClip(view, true)).clip).toBe(true);
    expect(setClip(view, false)).toBe(view);
  });
});
