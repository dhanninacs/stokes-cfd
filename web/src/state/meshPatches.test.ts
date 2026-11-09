/** The mesh patches of the tree (T-211): which nodes count, how they read, and when the mesh is on screen. */
import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { activeResultOf, bareMeshOf, isMeshSource, layerFilter, meshIdOfSource, meshPatchName, meshPatchesOf, meshResultsOf, meshShown, meshSourceOf, patchStyleKey, planMeshSync, selectKeepsStage } from "./meshPatches";

function node(id: string, type: string, path: string, params: Record<string, unknown> = {}, children: NodeSummary[] = [], name = id): NodeSummary {
  return { id, type, name, status: "ok", path, params: params as NodeSummary["params"], children_count: children.length, children };
}

function patch(id: string, name: string, extra: Record<string, unknown> = {}): NodeSummary {
  return node(id, "mesh_patch", `/mesh/results/blockmesh/${name}`, { visible: true, color: "#336699", opacity: 1, faces: 100, ...extra }, [], name);
}

function tree(results: NodeSummary[], extra: NodeSummary[] = []): NodeSummary {
  const group = node("g_results", "group", "/mesh/results", {}, results, "results");
  const mesh = node("g_mesh", "group", "/mesh", {}, [node("n_set", "mesh_settings", "/mesh/settings"), group], "mesh");
  return node("root", "root", "/", {}, [mesh, ...extra]);
}

describe("meshPatchesOf", () => {
  it("finds the patches under a result, in tree order, with their result", () => {
    const result = node("n_res", "mesh_result", "/mesh/results/blockmesh", { state: "done" }, [patch("n_a", "inlet"), patch("n_b", "walls", { color: "#aabbcc", opacity: 0.5, faces: 40 })]);
    const found = meshPatchesOf(tree([result]));
    expect(found.map((p) => p.id)).toEqual(["n_a", "n_b"]);
    expect(found[0]).toEqual({ id: "n_a", resultId: "n_res", name: "inlet", visible: true, color: "#336699", opacity: 1, faces: 100 });
    expect(found[1]).toMatchObject({ color: "#aabbcc", opacity: 0.5, faces: 40 });
  });

  it("ignores other node types, a patch outside a result and nodes outside /mesh", () => {
    const stray = patch("n_stray", "stray");
    const result = node("n_res", "mesh_result", "/mesh/results/blockmesh", {}, [node("n_x", "tag", "/mesh/results/blockmesh/x"), patch("n_a", "inlet")]);
    const outside = node("g_geo", "group", "/geometry", {}, [node("n_part", "part", "/geometry/part"), patch("n_far", "far")]);
    const found = meshPatchesOf(tree([result, stray], [outside]));
    expect(found.map((p) => p.id)).toEqual(["n_a"]);
  });

  it("falls back to safe values for parameters that are missing or malformed", () => {
    const odd = node("n_p", "mesh_patch", "/mesh/results/blockmesh/odd", { visible: false, color: "red", opacity: 7, faces: -3 });
    const result = node("n_res", "mesh_result", "/mesh/results/blockmesh", {}, [odd]);
    const [entry] = meshPatchesOf(tree([result]));
    expect(entry).toMatchObject({ visible: false, color: "#9aa3ad", opacity: 1, faces: 0 });
  });

  it("gives nothing for no tree, no /mesh group or no result", () => {
    expect(meshPatchesOf(null)).toEqual([]);
    expect(meshPatchesOf(node("root", "root", "/"))).toEqual([]);
    expect(meshPatchesOf(tree([]))).toEqual([]);
  });

  it("draws only the result in use when several meshers' results are kept (ADR-0034)", () => {
    const tet = node("n_tet", "mesh_result", "/mesh/results/cfmesh_tet", { state: "done", mesher: "cfmesh_tet" }, [patch("n_t1", "inlet")]);
    const poly = node("n_poly", "mesh_result", "/mesh/results/cfmesh_poly", { state: "done", mesher: "cfmesh_poly", active: true }, [patch("n_p1", "inlet")]);
    expect(meshPatchesOf(tree([tet, poly])).map((p) => p.id)).toEqual(["n_p1"]);
    // Two meshes of one part drawn over each other would be one unreadable picture.
    const tetInUse = { ...tet, params: { ...tet.params, active: true } };
    const polyKept = { ...poly, params: { ...poly.params, active: false } };
    expect(meshPatchesOf(tree([tetInUse, polyKept])).map((p) => p.id)).toEqual(["n_t1"]);
  });
});

describe("activeResultOf", () => {
  it("is the marked result, else the latest, as the server's active_result decides it", () => {
    const a = node("n_a", "mesh_result", "/mesh/results/blockmesh", {});
    const b = node("n_b", "mesh_result", "/mesh/results/snappy", {});
    expect(activeResultOf(tree([a, b]))?.id).toBe("n_b"); // a project saved before the mark: the latest
    expect(activeResultOf(tree([{ ...a, params: { active: true } }, b]))?.id).toBe("n_a");
    expect(activeResultOf(tree([]))).toBeNull();
    expect(meshResultsOf(tree([a, b])).map((r) => r.id)).toEqual(["n_a", "n_b"]);
  });
});

describe("selectKeepsStage", () => {
  it("a patch row selected in the Results stage does not leave it; anywhere else the stage follows as before", () => {
    expect(selectKeepsStage("Results", "mesh_patch")).toBe(true);
    expect(selectKeepsStage("Results", "tag")).toBe(false);
    expect(selectKeepsStage("Geometry", "mesh_patch")).toBe(false);
    expect(selectKeepsStage("Mesh", "mesh_patch")).toBe(false);
  });
});

describe("meshShown and the layer filter", () => {
  it("is true only in the Mesh stage with at least one patch", () => {
    expect(meshShown("Mesh", true)).toBe(true);
    expect(meshShown("Mesh", false)).toBe(false);
    for (const stage of ["Geometry", "Physics", "Solve", "Results", "Explore"] as const) expect(meshShown(stage, true)).toBe(false);
    // The Results stage shows the mesh while a field colours it (T-218), and only with patches to draw.
    expect(meshShown("Results", true, true)).toBe(true);
    expect(meshShown("Results", false, true)).toBe(false);
    expect(meshShown("Solve", true, true)).toBe(false);
    expect(layerFilter("Results", true, true)).toEqual({ geometry: false, mesh: true });
  });

  it("hides the geometry exactly while the mesh is shown, and never leaves the Mesh stage empty", () => {
    expect(layerFilter("Mesh", true)).toEqual({ geometry: false, mesh: true });
    expect(layerFilter("Mesh", false)).toEqual({ geometry: true, mesh: false });
    expect(layerFilter("Geometry", true)).toEqual({ geometry: true, mesh: false });
  });

  it("keeps an imported case's slice (its only mesh view) on in every stage", () => {
    expect(bareMeshOf(0, true)).toBe(true);
    expect(bareMeshOf(3, true)).toBe(false);
    expect(bareMeshOf(0, false)).toBe(false);
    for (const stage of ["Geometry", "Mesh", "Solve", "Results"] as const) {
      expect(layerFilter(stage, false, false, true)).toEqual({ geometry: true, mesh: true });
    }
  });
});

describe("mesh sources and names", () => {
  it("maps a node id to its scene source and back, and refuses a part's source", () => {
    expect(meshSourceOf("n_a")).toBe("mesh:n_a");
    expect(meshIdOfSource("mesh:n_a")).toBe("n_a");
    expect(meshIdOfSource("n_a")).toBeNull();
    expect(meshIdOfSource("mesh:")).toBeNull();
    expect(meshIdOfSource(null)).toBeNull();
    expect(isMeshSource("mesh:n_a")).toBe(true);
    expect(isMeshSource("@sample")).toBe(false);
  });

  it("draws a patch under a name no part has", () => {
    const [entry] = meshPatchesOf(tree([node("n_res", "mesh_result", "/mesh/results/blockmesh", {}, [patch("n_a", "walls")])]));
    expect(meshPatchName(entry)).toBe("walls (mesh)");
  });
});

describe("planMeshSync", () => {
  const entries = () => meshPatchesOf(tree([node("n_res", "mesh_result", "/mesh/results/blockmesh", {}, [patch("n_a", "inlet"), patch("n_b", "walls")])]));

  it("fetches what the scene has not asked for, and nothing else", () => {
    const [a, b] = entries();
    expect(planMeshSync([a, b], new Map())).toEqual({ fetch: ["n_a", "n_b"], remove: [], restyle: [] });
    expect(planMeshSync([a, b], new Map([[a.id, patchStyleKey(a)]]))).toEqual({ fetch: ["n_b"], remove: [], restyle: [] });
    expect(planMeshSync([a, b], new Map([[a.id, patchStyleKey(a)], [b.id, patchStyleKey(b)]]))).toEqual({ fetch: [], remove: [], restyle: [] });
  });

  it("removes what the tree no longer has", () => {
    const [a, b] = entries();
    const held = new Map([[a.id, patchStyleKey(a)], [b.id, patchStyleKey(b)], ["n_gone", "x"]]);
    expect(planMeshSync([a], held)).toEqual({ fetch: [], remove: ["n_b", "n_gone"], restyle: [] });
  });

  it("restyles a changed colour, visibility, opacity or name without a refetch", () => {
    const [a] = entries();
    const held = new Map([[a.id, patchStyleKey(a)]]);
    for (const change of [{ color: "#ffffff" }, { visible: false }, { opacity: 0.4 }, { name: "renamed" }]) {
      expect(planMeshSync([{ ...a, ...change }], held)).toEqual({ fetch: [], remove: [], restyle: ["n_a"] });
    }
  });
});
