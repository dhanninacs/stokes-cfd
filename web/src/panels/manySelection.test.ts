import { describe, expect, it } from "vitest";
import { bulkActionsFor, bulkCall, editableParameters, fieldValues, manyTitle, sameType } from "./manySelection";

/**
 * The multi-selection page's rules (claude-win2's 1400 file):
 *
 * > show only the actions valid for every selected item ... Anything not supported for several is
 * > greyed out with the reason, or absent (my rule: no entry that will be refused).
 *
 * Pure, so the pane has no judgement of its own and these can be checked without a browser.
 */
describe("bulkActionsFor", () => {
  it("offers show, hide and delete for anything", () => {
    expect(bulkActionsFor(["boundary"])).toEqual(["show", "hide", "delete"]);
  });

  it("offers 'In simulation' only when EVERY row is a part", () => {
    expect(bulkActionsFor(["part", "part"])).toContain("simulate");
    // A part and a tag: simulate is a part's own state, so it would be refused for the tag.
    expect(bulkActionsFor(["part", "tag"])).not.toContain("simulate");
  });

  it("offers colour for parts and tags, but not a boundary", () => {
    expect(bulkActionsFor(["part", "tag"])).toContain("colour");
    expect(bulkActionsFor(["boundary"])).not.toContain("colour");
    expect(bulkActionsFor(["part", "boundary"])).not.toContain("colour");
  });

  it("has NO actions for an empty selection", () => {
    // **`every` is vacuously true on an empty set**, so the first version of this function offered
    // every action for a selection that did not exist - caught by this test asserting nothing.
    expect(bulkActionsFor([])).toEqual([]);
  });
});

describe("manyTitle", () => {
  it("says '3 parts' for one type", () => {
    expect(manyTitle(["part", "part", "part"], 3)).toBe("3 parts");
  });

  it("says '2 items' for a mixed selection, because no single type is true", () => {
    // A part and a tag cannot be edited together, so naming one of them would be a lie about the other.
    expect(manyTitle(["part", "tag"], 2)).toBe("2 items");
  });
});

describe("sameType", () => {
  it("is what decides whether properties can be edited together", () => {
    expect(sameType(["part", "part"])).toBe(true);
    expect(sameType(["part", "tag"])).toBe(false);
    expect(sameType([])).toBe(false);
  });
});

describe("fieldValues", () => {
  it("says 'mixed' when the rows disagree, with no value", () => {
    // An empty box would read as "not set"; "mixed" says the rows disagree, which is a different fact.
    expect(fieldValues(["2 [mm]", "4 [mm]"])).toEqual({ mixed: true, value: "" });
  });

  it("shows the shared value when they agree", () => {
    expect(fieldValues(["2 [mm]", "2 [mm]"])).toEqual({ mixed: false, value: "2 [mm]" });
  });

  it("is NOT mixed when there is nothing to compare", () => {
    expect(fieldValues([])).toEqual({ mixed: false, value: "" });
  });
});

describe("editableParameters", () => {
  it("keeps only the parameters every row has", () => {
    // A parameter one row lacks would be a field that silently skips it.
    expect(editableParameters([["color", "opacity"], ["color"], ["color", "opacity", "size"]])).toEqual(["color"]);
  });

  it("has nothing to offer for no rows", () => {
    expect(editableParameters([])).toEqual([]);
  });
});

describe("bulkCall", () => {
  it("hides and shows through geometry.style, which already takes a LIST", () => {
    // No new verb was needed for these: the command takes `nodes` as an array.
    expect(bulkCall("hide", ["a", "b"], { visible: true })).toEqual({
      command: "geometry.style",
      params: { nodes: ["a", "b"], visible: false },
    });
    expect(bulkCall("show", ["a"], { visible: false })?.params.visible).toBe(true);
  });

  it("puts bodies back in the simulation through geometry.simulate", () => {
    expect(bulkCall("simulate", ["a", "b"], { visible: true })).toEqual({
      command: "geometry.simulate",
      params: { nodes: ["a", "b"], simulate: true },
    });
  });

  it("leaves DELETE to the page, because it asks first", () => {
    // A confirmation with the count is the page's job, so there is no command shape to return.
    expect(bulkCall("delete", ["a"], { visible: true })).toBeNull();
  });

  it("has no call for an empty selection", () => {
    expect(bulkCall("hide", [], { visible: true })).toBeNull();
  });
});
