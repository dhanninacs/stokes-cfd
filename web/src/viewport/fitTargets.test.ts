import { describe, expect, it } from "vitest";
import { fitCandidates, fitSourcesFor, fitTargetIds, isCutSource, missingPatchSources, namesOfSources } from "./fitTargets";
import { CAP_SOURCE, USER_SLICE_PREFIX } from "../panels/sliceModel";
import { SLICE_SOURCE } from "../panels/fieldsModel";
import { ISO_SOURCE } from "../panels/isoModel";

const pipe = { id: "pipe", regionBody: false };
const flange = { id: "flange", regionBody: false };
const fluid = { id: "fluid", regionBody: true };

describe("what Fit frames", () => {
  it("includes the visible fluid domain alongside solids", () => {
    expect(fitCandidates([pipe, fluid, flange]).map((b) => b.id)).toEqual(["pipe", "fluid", "flange"]);
  });

  it("frames EVERYTHING when there are no solids", () => {
    expect(fitCandidates([fluid]).map((b) => b.id)).toEqual(["fluid"]);
    expect(fitCandidates([])).toEqual([]);
  });

  it("frames everything when nothing is marked as a region body", () => {
    expect(fitCandidates([pipe, flange]).map((b) => b.id)).toEqual(["pipe", "flange"]);
    expect(fitTargetIds([pipe, fluid]).has("fluid")).toBe(true);
    expect(fitTargetIds([fluid]).has("fluid")).toBe(true);
  });
});

describe("which scene sources are cuts", () => {
  it("matches the names the layers use (the list here is spelled out, so a rename shows up)", () => {
    for (const source of [SLICE_SOURCE, `${USER_SLICE_PREFIX}s1`, CAP_SOURCE, ISO_SOURCE]) expect(isCutSource(source)).toBe(true);
  });
  it("leaves a patch and a part alone", () => {
    expect(isCutSource("mesh:n12")).toBe(false);
    expect(isCutSource("n12")).toBe(false);
  });
});

describe("what a Looks fit frames", () => {
  const patches = ["mesh:in", "mesh:out", "mesh:wall"];
  it("is the fluid's patches in the Results stage, and nothing the cuts add alone", () => {
    expect(fitSourcesFor([...patches, ISO_SOURCE, CAP_SOURCE], "fluid")).toEqual(patches);
  });
  it("keeps a slice that is on inside the frame, the mid-plane's and the list's", () => {
    expect(fitSourcesFor([...patches, SLICE_SOURCE, `${USER_SLICE_PREFIX}s1`, ISO_SOURCE], null)).toEqual([...patches, SLICE_SOURCE, `${USER_SLICE_PREFIX}s1`]);
  });
  it("is the fluid part before there are patches, whatever else the scene holds", () => {
    expect(fitSourcesFor(["solid", "fluid", "flange"], "fluid")).toEqual(["fluid"]);
  });
  it("is the scene's own rule (null) with neither patches nor a fluid part in the scene", () => {
    expect(fitSourcesFor(["solid"], "fluid")).toBeNull();
    expect(fitSourcesFor(["solid"], null)).toBeNull();
    expect(fitSourcesFor([SLICE_SOURCE], "fluid")).toBeNull();
    expect(fitSourcesFor([], null)).toBeNull();
  });
});

describe("the parts a source selection names", () => {
  const parts = [
    { name: "inlet", source: "mesh:in" },
    { name: "wall", source: "mesh:wall" },
    { name: "Slice z (mesh)", source: SLICE_SOURCE },
    { name: "pipe", source: "n1" },
  ];
  it("takes the parts of the chosen sources", () => {
    expect([...(namesOfSources(parts, new Set(["mesh:in", "mesh:wall"])) ?? [])]).toEqual(["inlet", "wall"]);
  });
  it("falls back (null) with no selection or a selection that matches nothing, so a fit never frames nothing", () => {
    expect(namesOfSources(parts, null)).toBeNull();
    expect(namesOfSources(parts, new Set(["mesh:gone"]))).toBeNull();
    expect(namesOfSources([], new Set(["mesh:in"]))).toBeNull();
  });
});

describe("which listed patches the scene still lacks", () => {
  const listed = ["mesh:in", "mesh:out", "mesh:wall"];
  it("names the patches not in the scene yet, in the tree's order", () => {
    expect(missingPatchSources(listed, ["mesh:out"])).toEqual(["mesh:in", "mesh:wall"]);
    expect(missingPatchSources(listed, [])).toEqual(listed);
  });
  it("is empty once the last patch has landed, whatever else the scene holds (the slice, the iso, a part)", () => {
    expect(missingPatchSources(listed, ["fluid", SLICE_SOURCE, "mesh:wall", "mesh:in", ISO_SOURCE, "mesh:out"])).toEqual([]);
  });
  it("is empty when the tree lists no patches (a project with only its geometry)", () => {
    expect(missingPatchSources([], ["fluid"])).toEqual([]);
  });
  it("does not count a cut as a patch that landed", () => {
    expect(missingPatchSources(["mesh:slice-like"], [SLICE_SOURCE])).toEqual(["mesh:slice-like"]);
  });
});
