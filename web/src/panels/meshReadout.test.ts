import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { NO_LIVE, readResult } from "./meshPanelModel";
import { kCells, meshReadout } from "./meshReadout";

function result(params: Record<string, unknown>, status: NodeSummary["status"] = "ok"): NodeSummary {
  return { id: "n_1", type: "mesh_result", name: "blockmesh", status, path: "/mesh/results/blockmesh", children_count: 0, params: { mesher: "blockmesh", state: "done", cells: 25000, ok: true, ...params } as NodeSummary["params"] };
}

describe("kCells", () => {
  it("prints thousands and millions short", () => {
    expect(kCells(25000)).toBe("25 k");
    expect(kCells(24500)).toBe("24.5 k");
    expect(kCells(800)).toBe("800");
    expect(kCells(1_200_000)).toBe("1.2 M");
    expect(kCells(250_400)).toBe("250 k");
  });
});

describe("meshReadout", () => {
  const read = (node: NodeSummary | null, live = NO_LIVE) => meshReadout(readResult(node), live);

  it("shows nothing until there is a result", () => {
    expect(read(null)).toBeNull();
  });

  it("reads a good mesh", () => {
    expect(read(result({}))).toMatchObject({ text: "Mesh · 25 k cells · ok", state: "ok" });
  });

  it("reads a mesh that missed its quality bar, a stale mesh and a failed one", () => {
    expect(read(result({ ok: false }, "warning"))).toMatchObject({ text: "Mesh · 25 k cells · quality not met", state: "warn" });
    expect(read(result({}, "stale"))).toMatchObject({ text: "Mesh · stale", state: "stale" });
    expect(read(result({ state: "failed", error: "boom" }, "failed"))).toMatchObject({ text: "Mesh · failed", state: "failed", detail: "boom" });
  });

  it("reads a running mesh with its step's progress", () => {
    const node = result({ state: "running", cells: 0 });
    expect(read(node)?.text).toBe("Mesh · meshing");
    const live = { nodeId: "n_1", step: "checkMesh", stage: "running", at: 1, logs: [] };
    expect(read(node, live)?.text).toBe("Mesh · meshing 50 %");
  });
});
