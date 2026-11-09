import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { twoDDescription } from "../panels/TwoDControls";
import { faceOnView, twoDOf, twoDOfTree } from "./twoD";

function node(path: string, type: string, params: Record<string, unknown> = {}, children: NodeSummary[] = []): NodeSummary {
  return { id: path, path, name: path.split("/").pop() ?? "", type, params, children } as unknown as NodeSummary;
}

function treeWith(params: Record<string, unknown>): NodeSummary {
  return node("/", "root", {}, [node("/mesh", "group", {}, [node("/mesh/settings", "mesh_settings", params)])]);
}

describe("twoD", () => {
  it("reads off and missing as 3D", () => {
    expect(twoDOf(null)).toBeNull();
    expect(twoDOfTree(treeWith({ two_d: "off", plane: "xz" }))).toBeNull();
    expect(twoDOfTree(treeWith({}))).toBeNull();
    expect(twoDOfTree(null)).toBeNull();
  });

  it("reads the mode and the plane, xy when the plane is unknown", () => {
    expect(twoDOfTree(treeWith({ two_d: "planar", plane: "yz" }))).toEqual({ mode: "planar", plane: "yz" });
    expect(twoDOfTree(treeWith({ two_d: "axisymmetric", plane: "??" }))).toEqual({ mode: "axisymmetric", plane: "xy" });
  });

  it("looks along the plane's normal with its second axis up", () => {
    for (const plane of ["xy", "xz", "yz"] as const) {
      const { from, up } = faceOnView(plane);
      const normal = 3 - "xyz".indexOf(plane[0]) - "xyz".indexOf(plane[1]);
      expect(Math.abs(from[normal])).toBe(1);
      expect(from.filter((v) => v !== 0)).toHaveLength(1);
      expect(up["xyz".indexOf(plane[1])]).toBe(1);
    }
  });

  it("describes what the switch means", () => {
    expect(twoDDescription(null, "xy", "snappy")).toMatch(/one cell thick/i);
    expect(twoDDescription("planar", "xz", "snappy")).toContain("slab");
    expect(twoDDescription("planar", "xz", "blockmesh")).toContain("XZ");
    expect(twoDDescription("axisymmetric", "xy", "blockmesh")).toBe("A wedge about x, radius y");
  });
});
