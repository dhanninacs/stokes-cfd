import { describe, expect, it } from "vitest";
import { buildOutline, flattenOutline, resolveIn, sectionId } from "./outlineModel";
import { focusOutlineStage } from "./outlineStageFocus";

describe("stage navigation in the outline tree", () => {
  const projection = buildOutline(null, resolveIn(null));

  it("opens the chosen virtual section and closes other stages", () => {
    const previous = { [sectionId("Geometry")]: true, "virtual:outline:Geometry:bodies": true, realMaterial: false };
    const focused = focusOutlineStage(projection, "Mesh", previous);
    const rows = flattenOutline(projection, { expanded: new Set(Object.keys(focused.expanded).filter((id) => focused.expanded[id])) });
    expect(focused.target).toBe(sectionId("Mesh"));
    expect(rows.find((row) => row.id === sectionId("Mesh"))?.open).toBe(true);
    expect(rows.find((row) => row.id === sectionId("Geometry"))?.open).toBe(false);
    expect(focused.expanded["virtual:outline:Geometry:bodies"]).toBe(true);
    expect(focused.expanded.realMaterial).toBe(false);
    expect(previous[sectionId("Geometry")]).toBe(true);
  });

  it("keeps nested choices when returning to a stage", () => {
    const initial = { "virtual:outline:Geometry:bodies": false, "virtual:outline:Geometry:tags": true };
    const mesh = focusOutlineStage(projection, "Mesh", initial);
    const geometry = focusOutlineStage(projection, "Geometry", mesh.expanded);
    expect(geometry.expanded[sectionId("Geometry")]).toBe(true);
    expect(geometry.expanded["virtual:outline:Geometry:bodies"]).toBe(false);
    expect(geometry.expanded["virtual:outline:Geometry:tags"]).toBe(true);
  });

  it("does not collapse the visible tree when a stage is hidden", () => {
    const hidden = buildOutline(null, resolveIn(null), ["physics", "regions"]);
    const expanded = { [sectionId("Geometry")]: true };
    expect(focusOutlineStage(hidden, "Physics", expanded)).toEqual({ target: null, expanded });
  });
});
