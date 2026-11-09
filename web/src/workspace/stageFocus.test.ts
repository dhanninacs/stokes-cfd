/**
 * The stage-focus rule (T-220).
 *
 * The plan's shape, as a table:
 *
 * ```
 * Mesh stage                        Physics stage                       Results stage
 * [Properties][Mesh]                [Properties][Conditions][Materials] [Properties][Results]
 * ```
 *
 * The tests are mostly about the two cases where filtering goes WRONG rather than about the simple
 * case: a slot emptied entirely, and a panel that must survive a stage it does not belong to.
 */
import { describe, expect, it } from "vitest";
import { panelsForStage, type FocusablePanel } from "./stageFocus";

const panel = (id: string, stages?: readonly ("Mesh" | "Physics" | "Results" | "Geometry")[]) => ({
  def: { id, ...(stages === undefined ? {} : { stages }) },
});

const EVERYTHING: FocusablePanel[] = [
  panel("properties"), // always-on: no `stages`
  panel("mesh", ["Mesh"]),
  panel("conditions", ["Physics"]),
  panel("solve", ["Physics"]),
  panel("results", ["Results"]),
];

const ids = (panels: readonly FocusablePanel[]) => panels.map((p) => p.def.id);

describe("panelsForStage", () => {
  it("shows the always-on panels and the stage's own", () => {
    expect(ids(panelsForStage(EVERYTHING, "Mesh").shown)).toEqual(["properties", "mesh"]);
    expect(ids(panelsForStage(EVERYTHING, "Physics").shown)).toEqual(["properties", "conditions", "solve"]);
    expect(ids(panelsForStage(EVERYTHING, "Results").shown)).toEqual(["properties", "results"]);
  });

  it("reports what it hid, so a caller can say how many rather than only that something went", () => {
    const { hidden } = panelsForStage(EVERYTHING, "Results");
    expect(ids(hidden)).toEqual(["mesh", "conditions", "solve"]);
  });

  it("keeps the slot's own order rather than the stage's", () => {
    // The workspace document decides where a panel sits; the filter removes, it does not reorder.
    const { shown } = panelsForStage(EVERYTHING, "Physics");
    expect(ids(shown)).toEqual(["properties", "conditions", "solve"]);
  });

  it("KEEPS a pinned panel that the stage excludes", () => {
    // The tree must not refold under the pointer: a row that opens the Mesh panel while in Geometry
    // shows it until the stage changes again.
    const { shown } = panelsForStage(EVERYTHING, "Geometry", "mesh");
    expect(ids(shown)).toEqual(["properties", "mesh"]);
  });

  it("ignores a pin that names no panel in this slot", () => {
    // A pin outliving its panel must not show nothing.
    const { shown } = panelsForStage(EVERYTHING, "Mesh", "gone");
    expect(ids(shown)).toEqual(["properties", "mesh"]);
  });

  it("NEVER returns an empty slot, even when nothing belongs to the stage", () => {
    // **The failure this exists to prevent**: a slot whose panels all belong elsewhere would render
    // a card with no tabs, and the user would have no way back. A wrong tab is recoverable; an empty
    // slot is not. Measured need: a left slot holding only stage panels.
    const onlyStagePanels = [panel("mesh", ["Mesh"])];
    const { shown, hidden } = panelsForStage(onlyStagePanels, "Results");
    expect(ids(shown)).toEqual(["mesh"]);
    expect(hidden).toEqual([]);
  });

  it("treats an empty slot as empty rather than inventing a panel", () => {
    expect(panelsForStage([], "Mesh").shown).toEqual([]);
  });

  it("keeps a panel that lists SEVERAL stages", () => {
    // The Materials section joins Physics and Solve, so a single-stage rule would be wrong.
    const many = [panel("materials", ["Physics", "Solve"])];
    expect(ids(panelsForStage(many, "Physics").shown)).toEqual(["materials"]);
    expect(ids(panelsForStage(many, "Solve").shown)).toEqual(["materials"]);
    expect(ids(panelsForStage(many, "Mesh").shown)).toEqual(["materials"]); // the empty-slot fallback
  });
});
