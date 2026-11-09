import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { findDetailsRow } from "./detailsRow";
import { openTargetOf } from "./stageTreeModel";

const root = { id: "root", name: "demo", type: "project", path: "/", children: [
  { id: "solver", name: "Solver", type: "group", path: "/solver", children: [] },
] } as unknown as NodeSummary;

describe("Details for outline selections", () => {
  it("opens the solver page for Controls even though it is not a stored node", () => {
    const row = findDetailsRow(root, "virtual:outline:Solve:controls", "outline", { "virtual:outline:Solve": true });
    expect(row?.name).toBe("Controls");
    expect(row && openTargetOf(row)?.panel).toBe("solve");
    expect(row && openTargetOf(row)?.section).toBe("solver-limits");
  });
  it("preserves real selections and does not invent outline pages in groups mode", () => {
    expect(findDetailsRow(root, "solver", "groups", {})).toBe(root.children?.[0]);
    expect(findDetailsRow(root, "virtual:outline:Solve:controls", "groups", {})).toBeNull();
    expect(findDetailsRow(null, "solver", "outline", {})).toBeNull();
    expect(findDetailsRow(root, "missing", "outline", {})).toBeNull();
  });
  it("resolves a page even when its parent is collapsed", () => {
    const row = findDetailsRow(root, "virtual:outline:Solve:controls", "outline", {});
    expect(row && openTargetOf(row)).toEqual({ panel: "solve", section: "solver-limits" });
  });
});
