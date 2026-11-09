import { describe, expect, it } from "vitest";
import { panelsForStage } from "./stageFocus";

/**
 * The owner's report: *"hiding the tree and settings tabs is broken"*.
 *
 * Opening Mesh and Conditions from the palette in the Geometry stage placed both in the document and
 * showed **one** - `mesh` belongs to the Mesh stage and `conditions` to Physics, so stage focus folded
 * each as soon as the next open arrived. The panel a user asks for must stay until the stage changes.
 */
const panel = (id: string, stages?: string[]) => ({ def: stages === undefined ? { id } : { id, stages } });
const right = [panel("properties"), panel("mesh", ["Mesh"]), panel("conditions", ["Physics"])];

describe("panels a user opened", () => {
  it("stay on screen even when the stage does not hold them", () => {
    const { shown } = panelsForStage(right, "Geometry", ["mesh", "conditions"]);
    expect(shown.map((p) => p.def.id)).toEqual(["properties", "mesh", "conditions"]);
  });

  it("keep the panels that belong to the stage too", () => {
    // **The half that broke**: pinning Mesh must not drop Conditions when both were opened. The rule
    // is additive - `belongs || isPinned` - so an open adds to the strip rather than replacing it.
    const { shown } = panelsForStage(right, "Mesh", ["conditions"]);
    expect(shown.map((p) => p.def.id).sort()).toEqual(["conditions", "mesh", "properties"]);
  });

  it("show only what the stage holds when nothing was opened", () => {
    const { shown, hidden } = panelsForStage(right, "Geometry", null);
    expect(shown.map((p) => p.def.id)).toEqual(["properties"]);
    expect(hidden.map((p) => p.def.id)).toEqual(["mesh", "conditions"]);
  });

  it("never leave the strip empty, whatever the stage", () => {
    // The existing rule, kept: a wrong tab is recoverable, an empty strip is not.
    const onlyForeign = [panel("mesh", ["Mesh"])];
    expect(panelsForStage(onlyForeign, "Geometry", null).shown.map((p) => p.def.id)).toEqual(["mesh"]);
  });
});
