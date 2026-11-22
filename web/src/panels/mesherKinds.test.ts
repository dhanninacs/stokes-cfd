import { describe, expect, it } from "vitest";
import { coresDescription, followsShape, isCfMesh } from "./mesherKinds";
import { meshSettingIncluded } from "./settingsPresentation";
import { readMeshers } from "./meshPanelModel";

describe("mesher kinds (ADR-0032)", () => {
  it("the cfMesh fills follow the shape; blockMesh does not", () => {
    for (const id of ["snappy", "cfmesh", "cfmesh_tet", "cfmesh_poly", "polyhex"]) expect(followsShape(id)).toBe(true);
    expect(followsShape("blockmesh")).toBe(false);
    expect(isCfMesh("polyhex")).toBe(true);
    expect(isCfMesh("snappy")).toBe(false);
  });

  it("the Cores line says how the count is spent", () => {
    expect(coresDescription("snappy", "mpi")).toContain("MPI");
    expect(coresDescription("cfmesh_poly", "threads")).toContain("OpenMP threads");
    expect(coresDescription("blockmesh", "serial")).toContain("serially");
    // An older server without `parallel` falls back on the id.
    expect(coresDescription("cfmesh", "")).toContain("OpenMP");
  });

  it("layers and sizes show for cfMesh; snappy-only controls do not", () => {
    const ctx = { mesher: "cfmesh_poly", domain: "part", layerMode: "final_ratio" };
    expect(meshSettingIncluded("layers", ctx)).toBe(true);
    expect(meshSettingIncluded("surface_size", ctx)).toBe(true);
    expect(meshSettingIncluded("final_ratio", ctx)).toBe(false);
    expect(meshSettingIncluded("layer_feature_angle", ctx)).toBe(false);
    expect(meshSettingIncluded("margin", ctx)).toBe(false);
    expect(meshSettingIncluded("feature_angle", ctx)).toBe(false);
    expect(meshSettingIncluded("feature_angle", { ...ctx, mesher: "polyhex" })).toBe(true);
    expect(meshSettingIncluded("first_layer_height", { ...ctx, layerMode: "first_height" })).toBe(true);
  });

  it("Gmsh follows the shape and takes the layer, size and feature-angle controls (ADR-0043)", () => {
    expect(followsShape("gmsh")).toBe(true);
    expect(isCfMesh("gmsh")).toBe(false);
    expect(coresDescription("gmsh", "serial")).toBe("Gmsh runs serially. Choose snappyHexMesh or a cfMesh type to mesh with several cores.");
    const ctx = { mesher: "gmsh", domain: "part", layerMode: "final_ratio" };
    for (const name of ["layers", "surface_size", "expansion_ratio", "final_ratio", "feature_angle"]) expect(meshSettingIncluded(name, ctx)).toBe(true);
    expect(meshSettingIncluded("first_layer_height", { ...ctx, layerMode: "first_height" })).toBe(true);
    expect(meshSettingIncluded("final_ratio", { ...ctx, layerMode: "first_height" })).toBe(false);
    for (const name of ["min_thickness_ratio", "layer_feature_angle", "margin"]) expect(meshSettingIncluded(name, ctx)).toBe(false);
  });

  it("the catalogue's parallel word is read", () => {
    const [entry] = readMeshers({ meshers: [{ id: "polyhex", label: "Poly-hexcore", built: true, domains: ["part"], why: "", output_structures: ["poly-hexcore"], parallel: "threads" }] });
    expect(entry.parallel).toBe("threads");
    expect(entry.outputStructures).toEqual(["poly-hexcore"]);
  });
});
