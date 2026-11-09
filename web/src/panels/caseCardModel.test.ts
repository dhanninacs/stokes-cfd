/**
 * The case card's rules: what typed text becomes, which files are offered, how rows indent.
 *
 * **Why this needs a test.** `case.set` takes typed tokens, so a list typed as text, or a quantity sent as a
 * number, would change what the case means. The server refuses what it must; these check the card sends
 * what the user typed, in the shape the server reads.
 */
import { describe, expect, it } from "vitest";
import { depthOf, editableFiles, linePoints, recipeSummary, valueFor, visibleRows, type CaseFile, type SettingsRow } from "./caseCardModel";

const row = (path: string[], extra: Partial<SettingsRow> = {}): SettingsRow => ({
  path,
  kind: "entry",
  text: "",
  truncated: false,
  overridden: false,
  ...extra,
});

describe("valueFor", () => {
  it("reads numbers, booleans, words, lists and several tokens", () => {
    expect(valueFor(row(["nCorrectors"]), "3")).toEqual({ value: 3 });
    expect(valueFor(row(["x"]), " 1e-05; ")).toEqual({ value: 1e-5 });
    expect(valueFor(row(["momentumPredictor"]), "false")).toEqual({ value: false });
    expect(valueFor(row(["default"]), "Euler")).toEqual({ value: "Euler" });
    expect(valueFor(row(["g"]), "(0 -9.81 0)")).toEqual({ value: [0, -9.81, 0] });
    expect(valueFor(row(["value"]), "uniform (1 0 0)")).toEqual({ value: { tokens: ["uniform", [1, 0, 0]] } });
    expect(valueFor(row(["div(phi,U)"]), "Gauss linear")).toEqual({ value: { tokens: ["Gauss", "linear"] } });
    expect(valueFor(row(["l"]), "((1 2) (3 4))")).toEqual({ value: [[1, 2], [3, 4]] });
  });

  it("sends a known physical key as its text, so the server reads the unit", () => {
    expect(valueFor(row(["endTime"], { dimension: "time", unit: "s" }), "500 ms")).toEqual({ value: "500 ms" });
  });

  it("refuses what no typed token can hold, and says why", () => {
    for (const typed of ["", "#include x", "$var", "{ a 1; }", '"q"', "(1 2", "1)"]) {
      expect("problem" in valueFor(row(["x"]), typed)).toBe(true);
    }
  });
});

describe("files and rows", () => {
  const file = (path: string, kind: string, parsed = true, area = "source"): CaseFile => ({ path, kind, parsed, area, size: 1 });

  it("offers parsed dictionaries and fields, system first, and never scripts or meshes", () => {
    const files = [
      file("0/U", "field"),
      file("constant/physicalProperties", "dict"),
      file("system/controlDict", "dict"),
      file("system/blockMeshDict", "dict"),
      file("constant/polyMesh/points", "mesh"),
      file("Allrun", "script", false, "scripts"),
      file("system/big", "dict", false),
    ];
    expect(editableFiles(files).map((f) => f.path)).toEqual(["system/controlDict", "system/blockMeshDict", "constant/physicalProperties", "0/U"]);
  });

  it("hides the file header and indents by depth", () => {
    const rows = [row(["FoamFile", "class"]), row(["PISO"], { kind: "block" }), row(["PISO", "nCorrectors"]), row(["PISO"], { kind: "directive" })];
    expect(visibleRows(rows).map((r) => r.path.join("/"))).toEqual(["PISO", "PISO/nCorrectors", "PISO"]);
    expect(rows.slice(1).map(depthOf)).toEqual([0, 1, 1]);
  });

  it("summarises a recipe as its commands", () => {
    expect(recipeSummary({ steps: [{ argv: ["blockMesh"] }, { argv: ["foamRun"] }] })).toBe("blockMesh  →  foamRun");
    expect(recipeSummary(null)).toBe("no steps");
  });
});

describe("linePoints", () => {
  it("scales a series into its box with y up", () => {
    expect(linePoints([0, 1, 2], [0, 5, 10], 100, 50)).toBe("0.0,50.0 50.0,25.0 100.0,0.0");
  });

  it("draws a flat series along the middle and nothing for one point", () => {
    expect(linePoints([0, 1], [3, 3], 10, 10)).toBe("0.0,5.0 10.0,5.0");
    expect(linePoints([0], [1], 10, 10)).toBe("");
  });
});
