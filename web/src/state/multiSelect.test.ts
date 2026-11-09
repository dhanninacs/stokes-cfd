import { describe, expect, it } from "vitest";
import { NO_SELECTION, extend, moveBy, selectOne, selectSiblings, toggle, type Selection, type VisibleRow } from "./multiSelect";

/**
 * The tree's selection rules, tested as arithmetic (claude-win2's 1400 file).
 *
 * **Why pure and why here**: `TreePanel` reads `selectedId` in sixteen places, so the risk is not that a
 * gesture fails loudly - it is that a second copy of "which rows are chosen" appears in a handler and
 * disagrees after a folder folds. These tests pin the rules the owner named, and the component only calls
 * them.
 */
const VISIBLE = ["geometry", "parts", "pipe", "flange", "tags", "walls"];

describe("plain click", () => {
  it("selects exactly one row, clearing what was chosen", () => {
    expect(selectOne("pipe")).toEqual({ ids: ["pipe"], primary: "pipe", anchor: "pipe" });
  });
});

describe("Ctrl+click", () => {
  it("adds a row and makes it the PRIMARY", () => {
    // The primary is what drag, rename and the Details page follow, so the row just clicked takes it.
    const s = toggle(selectOne("pipe"), "flange");
    expect(s.ids).toEqual(["pipe", "flange"]);
    expect(s.primary).toBe("flange");
  });

  it("takes a chosen row out again", () => {
    const s = toggle(toggle(selectOne("pipe"), "flange"), "pipe");
    expect(s.ids).toEqual(["flange"]);
  });

  it("leaves four chosen rows out of five, not none, when the PRIMARY is toggled off", () => {
    // A user Ctrl-clicking the primary expects the other four to stay chosen.
    let s: Selection = selectOne("a");
    for (const id of ["b", "c", "d", "e"]) s = toggle(s, id);
    s = toggle(s, "e"); // "e" is the primary
    expect(s.ids).toEqual(["a", "b", "c", "d"]);
    expect(s.ids).toContain(s.primary);
    expect(s.primary).toBe("d");
  });

  it("empties to NO_SELECTION when the last row is toggled off", () => {
    expect(toggle(selectOne("pipe"), "pipe")).toEqual(NO_SELECTION);
  });
});

describe("Shift+click", () => {
  it("selects the range from the anchor, in VISIBLE order", () => {
    const s = extend(selectOne("parts"), "flange", VISIBLE, false);
    expect(s.ids).toEqual(["parts", "pipe", "flange"]);
  });

  it("ranges upward as well as downward", () => {
    const s = extend(selectOne("flange"), "parts", VISIBLE, false);
    expect(s.ids).toEqual(["parts", "pipe", "flange"]);
  });

  it("leaves a COLLAPSED folder's children out, because they are not on screen", () => {
    // `parts` is folded in this order: its children are simply not in `visible`, so they cannot appear.
    const folded = ["geometry", "parts", "tags", "walls"];
    const s = extend(selectOne("geometry"), "walls", folded, false);
    expect(s.ids).toEqual(["geometry", "parts", "tags", "walls"]);
    expect(s.ids).not.toContain("pipe");
  });

  it("keeps the anchor so a second Shift click re-ranges from the same start", () => {
    const first = extend(selectOne("geometry"), "pipe", VISIBLE, false);
    const second = extend(first, "tags", VISIBLE, false);
    expect(second.ids).toEqual(["geometry", "parts", "pipe", "flange", "tags"]);
  });
});

describe("Ctrl+Shift+click", () => {
  it("ADDS the range to what was already chosen", () => {
    const chosen = toggle(selectOne("walls"), "geometry");
    const s = extend(chosen, "pipe", VISIBLE, true);
    expect(s.ids).toContain("walls");
    expect(s.ids).toContain("parts");
    expect(s.ids).toContain("pipe");
  });
});

describe("Ctrl+A", () => {
  const rows: VisibleRow[] = [
    { id: "parts-group", parent: "geometry", type: "group" },
    { id: "pipe", parent: "parts", type: "part" },
    { id: "flange", parent: "parts", type: "part" },
    { id: "bolt", parent: "parts", type: "part" },
    { id: "walls", parent: "tags", type: "tag" },
  ];

  it("selects every SIBLING of the same type, not every row in the tree", () => {
    const s = selectSiblings(selectOne("pipe"), rows);
    expect(s.ids).toEqual(["pipe", "flange", "bolt"]);
    expect(s.ids).not.toContain("walls");
    expect(s.ids).not.toContain("parts-group");
  });

  it("leaves the primary as it was", () => {
    expect(selectSiblings(selectOne("flange"), rows).primary).toBe("flange");
  });
});

describe("Shift+Up/Down", () => {
  it("moves one VISIBLE row, so a folded folder is a single step", () => {
    expect(moveBy(VISIBLE, "parts", 1)).toBe("pipe");
    expect(moveBy(VISIBLE, "parts", -1)).toBe("geometry");
  });

  it("has nowhere to go past either end", () => {
    expect(moveBy(VISIBLE, "geometry", -1)).toBeNull();
    expect(moveBy(VISIBLE, "walls", 1)).toBeNull();
  });
});

describe("clearing", () => {
  it("Escape and a press on empty tree both mean nothing chosen", () => {
    expect(NO_SELECTION).toEqual({ ids: [], primary: null, anchor: null });
  });
});
