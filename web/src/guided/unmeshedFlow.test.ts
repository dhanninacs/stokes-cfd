/** The Mesh card's lost-inlet check (the mixing elbow: cfMesh at 5 mm dropped the 25 mm hot inlet). */
import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { unmeshedFlowBoundaries } from "./guidedTree";

function node(type: string, name: string, path: string, params: NodeSummary["params"] = {}, children: NodeSummary[] = []): NodeSummary {
  return { id: `${type}:${name}`, type, name, status: "ok" as NodeSummary["status"], params, path, children_count: children.length, children };
}

function tree(patches: string[]): NodeSummary {
  const tags = ["cold_inlet", "hot_inlet", "outlet", "symmetry"].map((n) => node("tag", n, `/geometry/tags/${n}`));
  const boundary = (name: string, kind: string) => node("boundary", name, `/regions/fluid/${name}`, { kind, tag: `tag:${name}` });
  return node("project", "elbow", "/", {}, [
    node("group", "geometry", "/geometry", {}, tags),
    node("group", "mesh", "/mesh", {}, [
      node("mesh_result", "cfmesh", "/mesh/results/cfmesh", {}, patches.map((p) => node("mesh_patch", p, `/mesh/results/cfmesh/${p}`, { faces: 10 }))),
    ]),
    node("group", "regions", "/regions", {}, [
      node("region", "fluid", "/regions/fluid", {}, [
        boundary("cold_inlet", "velocity_inlet"),
        boundary("hot_inlet", "velocity_inlet"),
        boundary("outlet", "pressure_outlet"),
        boundary("symmetry", "symmetry"),
      ]),
    ]),
  ]);
}

describe("the inlets and outlets the mesh lost", () => {
  it("names a flow boundary whose tag has no patch in the mesh", () => {
    expect(unmeshedFlowBoundaries(tree(["cold_inlet", "outlet", "unassigned"]))).toEqual(["hot_inlet"]);
  });

  it("is quiet when every inlet and outlet has a patch, and ignores a lost symmetry plane", () => {
    expect(unmeshedFlowBoundaries(tree(["cold_inlet", "hot_inlet", "outlet"]))).toEqual([]);
  });

  it("says nothing before there is a mesh", () => {
    expect(unmeshedFlowBoundaries(tree([]))).toEqual([]);
  });
});
