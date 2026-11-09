/**
 * The pure tree-row helpers of `nodeRules.ts` (T-040): the name a new sibling gets, the muted value
 * on the right of a row, the spans of rows the panel keeps in the DOM, and which row the selection
 * lands on after a removal.
 */
import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { neighbourAfterRemoval, treeValue, uniqueName, windowSegments, type RowModel, isStructural } from "./nodeRules";

function node(name: string, extra: Partial<NodeSummary> = {}): NodeSummary {
  return { id: name, type: "parameter", name, status: "ok", params: {}, path: `/p/${name}`, children_count: 0, ...extra };
}

function row(id: string, depth: number, parentId: string | null): RowModel {
  return { node: node(id), depth, parentId, hasChildren: false, open: false };
}

describe("uniqueName", () => {
  it("keeps the base name when it is free", () => {
    expect(uniqueName("inlet", [node("outlet")])).toBe("inlet");
    expect(uniqueName("inlet", [])).toBe("inlet");
  });

  it("numbers the first name a sibling has not taken", () => {
    expect(uniqueName("inlet", [node("inlet")])).toBe("inlet_2");
    expect(uniqueName("inlet", [node("inlet"), node("inlet_2"), node("inlet_4")])).toBe("inlet_3");
  });
});

describe("treeValue", () => {
  it("shows the display-unit value with the expression after it", () => {
    expect(treeValue(node("velocity", { params: { value: { value: 2, unit: "m/s", expr: "inlet_u" } }, displays: { value: "2 m/s" } }))).toBe("2 m/s · inlet_u");
  });

  it("shows the display-unit value alone when there is no expression", () => {
    expect(treeValue(node("velocity", { params: { value: { value: 2, unit: "m/s" } }, displays: { value: "2 m/s" } }))).toBe("2 m/s");
  });

  it("formats the stored quantity itself when there is no display string", () => {
    expect(treeValue(node("volume", { params: { value: { value: 0.0056549, unit: "m**3/s" } } }))).toBe("0.0056549 m³/s");
  });

  it("says the mesher and the cells of the mesh nodes instead of their first display", () => {
    expect(treeValue(node("settings", { type: "mesh_settings", params: { mesher: "blockmesh" }, displays: { base_size: "5 mm", margin: "0 mm" } }))).toBe("blockmesh · 5 mm");
    expect(treeValue(node("blockmesh", { type: "mesh_result", params: { state: "done", cells: 12000 } }))).toBe("12,000 cells");
    expect(treeValue(node("blockmesh", { type: "mesh_result", params: { state: "running" } }))).toBe("Meshing");
  });

  it("gives a group its child count", () => {
    expect(treeValue(node("inlets", { type: "group", params: {}, children_count: 3 }))).toBe("3");
  });
});

describe("windowSegments", () => {
  it("is the window alone when every pin is inside it or past the rows", () => {
    expect(windowSegments(100, 10, 20, [12, 100, -1])).toEqual([[10, 20]]);
  });

  it("merges a pinned row into the window and keeps it sorted", () => {
    // A pin just outside the window stays a span of its own; the gap row is still left out.
    expect(windowSegments(100, 10, 20, [5])).toEqual([[5, 6], [10, 20]]);
    expect(windowSegments(100, 10, 20, [21, 4, 15])).toEqual([[4, 5], [10, 20], [21, 22]]);
  });

  it("clamps the window to the rows that exist", () => {
    expect(windowSegments(8, 0, 500, [])).toEqual([[0, 8]]);
  });
});

describe("neighbourAfterRemoval", () => {
  // a (group) > a1, a2 > b (group) > b1, b2
  const rows = [row("a", 0, null), row("a1", 1, "a"), row("a2", 1, "a"), row("b", 0, null), row("b1", 1, "b"), row("b2", 1, "b")];

  it("takes the next sibling", () => {
    expect(neighbourAfterRemoval(rows, 1)).toBe("a2");
    expect(neighbourAfterRemoval(rows, 0)).toBe("b"); // the next top-level group, no sibling of "a1" in the way
  });

  it("skips a following group's children, as they are not siblings", () => {
    expect(neighbourAfterRemoval(rows, 3)).toBe("a"); // "b" has no next sibling, so the previous one
  });

  it("takes the previous sibling when there is no next one", () => {
    expect(neighbourAfterRemoval(rows, 2)).toBe("a1");
    expect(neighbourAfterRemoval(rows, 5)).toBe("b1");
  });

  it("takes the parent when the row was the only child", () => {
    const lone = [row("a", 0, null), row("a1", 1, "a")];
    expect(neighbourAfterRemoval(lone, 1)).toBe("a");
  });

  it("is null for a row that is not there", () => {
    expect(neighbourAfterRemoval(rows, 99)).toBeNull();
  });
});
describe("isStructural - a fixed group of the project, judged by its PATH", () => {
  const node = (id: string, path: string) => ({ id, name: "x", type: "group", path }) as never;

  it("is true for a real fixed folder, EVEN when its row id is virtual", () => {
    // **Measured as a regression I introduced**: keying this on `virtual:` broke `/physics/parameters`,
    // because an outline row for a real folder carries a virtual ID and the REAL path. A row that shows
    // a folder of the project IS that folder, whatever its row is called.
    expect(isStructural(node("virtual:outline:Physics:parameters", "/physics/parameters"))).toBe(true);
    expect(isStructural(node("virtual:outline:Physics:materials", "/physics/materials"))).toBe(true);
  });

  it("is FALSE for a page and for a section, which are not groups of the project", () => {
    // Measured: every section and page row claimed "Fixed group of the project" in its menu.
    expect(isStructural(node("virtual:outline:Solve:run", "virtual:outline:Solve:run"))).toBe(false);
    expect(isStructural(node("virtual:outline:Solve", "virtual:outline:Solve"))).toBe(false);
  });

  it("leaves a deep working folder alone", () => {
    expect(isStructural(node("n_1", "/geometry/parts"))).toBe(false);
  });
});
