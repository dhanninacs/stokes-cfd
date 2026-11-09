/** Finding a finished mesh: the tree opens to its patches, and "is it on screen" has an answer. */
import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { meshPatchesOf } from "./meshPatches";
import { hiddenPatchIds, meshOnScreen, newMeshResults, resultsWithPatches, revealIds } from "./meshReveal";

function node(id: string, type: string, path: string, params: Record<string, unknown> = {}, children: NodeSummary[] = [], name = id): NodeSummary {
  return { id, type, name, status: "ok", path, params: params as NodeSummary["params"], children_count: children.length, children };
}

function patch(id: string, name: string, visible = true): NodeSummary {
  return node(id, "mesh_patch", `/mesh/results/blockmesh/${name}`, { visible, color: "#336699", opacity: 1, faces: 10 }, [], name);
}

/** The tree as the server sends it after a mesh: /mesh/results/blockmesh with its patches (or none yet). */
function tree(patches: NodeSummary[]): NodeSummary {
  const result = node("n_res", "mesh_result", "/mesh/results/blockmesh", { state: "done" }, patches, "blockmesh");
  const results = node("g_results", "group", "/mesh/results", {}, [result], "results");
  const mesh = node("g_mesh", "group", "/mesh", {}, [node("n_set", "mesh_settings", "/mesh/settings"), results], "mesh");
  return node("root", "root", "/", {}, [mesh]);
}

describe("a mesh that has just been made opens the tree to its patches", () => {
  it("is new when its patches arrive after a tree without them, and not when it was there at the first look", () => {
    const before = tree([]);
    const after = tree([patch("n_a", "inlet"), patch("n_b", "walls")]);
    const seen = new Set(resultsWithPatches(before));
    expect(newMeshResults(after, seen)).toEqual(["n_res"]);
    expect(newMeshResults(after, new Set(resultsWithPatches(after)))).toEqual([]);
    // Before the first tree has been looked at, nothing is "new": a project that opens with a mesh keeps the tree the stage gave it.
    expect(newMeshResults(after, null)).toEqual([]);
  });

  it("opens the results group and the result, and /mesh too in the Mesh stage; the patch rows then exist", () => {
    const after = tree([patch("n_a", "inlet")]);
    expect(revealIds(after, ["n_res"], "Mesh", {})).toEqual(["g_mesh", "g_results", "n_res"]);
    expect(revealIds(after, ["n_res"], "Geometry", {})).toEqual(["g_results", "n_res"]);
    expect(meshPatchesOf(after).map((p) => p.name)).toEqual(["inlet"]);
  });

  it("leaves alone what is open already, and does nothing for a result it cannot find", () => {
    const after = tree([patch("n_a", "inlet")]);
    expect(revealIds(after, ["n_res"], "Mesh", { g_mesh: true, g_results: true })).toEqual(["n_res"]);
    expect(revealIds(after, ["n_res"], "Mesh", { g_mesh: true, g_results: true, n_res: true })).toEqual([]);
    expect(revealIds(after, ["n_gone"], "Mesh", {})).toEqual([]);
    expect(revealIds(null, ["n_res"], "Mesh", {})).toEqual([]);
    expect(revealIds(after, [], "Mesh", {})).toEqual([]);
  });
});

describe("meshOnScreen", () => {
  const visible = meshPatchesOf(tree([patch("n_a", "inlet"), patch("n_b", "walls")]));
  const hidden = meshPatchesOf(tree([patch("n_a", "inlet", false), patch("n_b", "walls", false)]));

  it("is true in the Mesh stage with a visible patch, and false in every stage that draws the geometry", () => {
    expect(meshOnScreen("Mesh", visible, false)).toBe(true);
    expect(meshOnScreen("Geometry", visible, false)).toBe(false);
    expect(meshOnScreen("Physics", visible, false)).toBe(false);
    expect(meshOnScreen("Results", visible, false)).toBe(false);
    expect(meshOnScreen("Results", visible, true)).toBe(true);
  });

  it("is false when every patch has been switched off, and when there are none", () => {
    expect(meshOnScreen("Mesh", hidden, false)).toBe(false);
    expect(meshOnScreen("Mesh", [], false)).toBe(false);
    expect(hiddenPatchIds(hidden)).toEqual(["n_a", "n_b"]);
    expect(hiddenPatchIds(visible)).toEqual([]);
  });
});
