import { describe, expect, it } from "vitest";
import { actionTargetsForContext, bulkTreeMenuActions, isCompleteActionSelection, shouldSelectContextTarget, visibilityChangePlan } from "./treeMenuModel";

describe("tree context menu selection", () => {
  it("preserves the full selection when its primary or another selected row opens the menu", () => {
    const ids = ["part-a", "part-b", "part-c"];
    expect(shouldSelectContextTarget("part-a", ids, "part-a")).toBe(false);
    expect(shouldSelectContextTarget("part-b", ids, "part-a")).toBe(false);
    expect(actionTargetsForContext("part-b", ids)).toEqual(ids);
  });

  it("selects an unselected context row alone and scopes its actions to that row", () => {
    const ids = ["part-a", "part-b"];
    expect(shouldSelectContextTarget("part-c", ids, "part-a")).toBe(true);
    expect(actionTargetsForContext("part-c", ids)).toEqual(["part-c"]);
  });

  it("refuses to treat a partially resolved selection as a complete bulk target", () => {
    expect(isCompleteActionSelection(["part-a", "virtual:section"], ["part-a"])).toBe(false);
    expect(isCompleteActionSelection(["part-a", "part-b"], ["part-b", "part-a"])).toBe(true);
  });
});

describe("bulk tree menu actions", () => {
  it("builds visibility and simulation commands for the intended selected nodes", () => {
    expect(bulkTreeMenuActions(["part-a", "part-b"], true, true)).toEqual([
      { id: "show-selected", label: "Show 2 selected", kind: "visibility", value: true, nodeIds: ["part-a", "part-b"] },
      { id: "hide-selected", label: "Hide 2 selected", kind: "visibility", value: false, nodeIds: ["part-a", "part-b"] },
      { id: "simulate-selected", label: "Use 2 selected in simulation", kind: "simulation", value: true, nodeIds: ["part-a", "part-b"] },
      { id: "exclude-selected", label: "Leave 2 selected out of simulation", kind: "simulation", value: false, nodeIds: ["part-a", "part-b"] },
    ]);
  });

  it("omits unsupported bulk actions and returns none for a single row", () => {
    expect(bulkTreeMenuActions(["part-a", "tag-b"], false, false)).toEqual([]);
    expect(bulkTreeMenuActions(["part-a", "part-b"], true, false).map((action) => action.id)).toEqual(["show-selected", "hide-selected"]);
    expect(bulkTreeMenuActions(["part-a"], true, true)).toEqual([]);
  });

  it("hides through the session filter and reveals both session-hidden and saved-hidden nodes", () => {
    expect(visibilityChangePlan(["part-a", "part-b"], false, ["part-b"], { "part-a": true, "part-b": true })).toEqual({
      hideInSession: ["part-a"],
      revealInSession: [],
      restoreModelVisibility: [],
    });
    expect(visibilityChangePlan(["part-a", "part-b"], true, ["part-a"], { "part-a": false, "part-b": true })).toEqual({
      hideInSession: [],
      revealInSession: ["part-a"],
      restoreModelVisibility: ["part-a"],
    });
  });
});
