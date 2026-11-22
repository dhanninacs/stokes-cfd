import { describe, expect, it } from "vitest";
import { isDirty, push, redo, refresh, start, undo, type SketchDoc } from "./history";

const empty: SketchDoc = { entities: [], bindings: {}, dims: [] };
const circle: SketchDoc = { entities: [{ kind: "circle", c: [0, 0], r: 5 }], bindings: {}, dims: [] };
const bound: SketchDoc = { ...circle, bindings: { "0.r": "R" }, dims: [{ name: "R", text: "5 [mm]" }] };
const badCell: SketchDoc = { ...bound, bindings: { "0.r": "abc" } };

describe("the sketch's history (review A1, A3)", () => {
  it("undoes the last edit whole: entities, bindings and dimensions together", () => {
    let h = start(empty);
    h = push(h, circle);
    h = push(h, bound);
    h = push(h, badCell);
    h = undo(h);
    // The review's case: one undo takes back the bad entry, not the circle.
    expect(h.present).toBe(bound);
    expect(h.present.entities).toHaveLength(1);
    h = undo(h);
    expect(h.present).toBe(circle);
    expect(h.present.bindings).toEqual({});
  });

  it("redoes what was undone, and an edit clears the redo stack", () => {
    let h = push(push(start(empty), circle), bound);
    h = undo(undo(h));
    expect(h.present).toBe(empty);
    h = redo(h);
    expect(h.present).toBe(circle);
    h = redo(h);
    expect(h.present).toBe(bound);
    expect(redo(h)).toBe(h);
    h = push(undo(h), badCell);
    expect(h.future).toEqual([]);
  });

  it("refreshes drawn values without an undo entry, and knows when Cancel must ask", () => {
    let h = start(circle);
    expect(isDirty(h)).toBe(false);
    h = refresh(h, { ...circle, entities: [{ kind: "circle", c: [0, 0], r: 6 }] });
    expect(h.past).toHaveLength(0);
    expect(isDirty(h)).toBe(false);
    h = push(h, bound);
    expect(isDirty(h)).toBe(true);
    expect(isDirty(undo(h))).toBe(false);
  });
});
