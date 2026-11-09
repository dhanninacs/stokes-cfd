/**
 * The Related ▾ menu's rules (ADR-0022 §6.4, T-051).
 *
 * §6.4's interaction — hover previews, click adds, Shift-click replaces — is the part a menu gets
 * wrong quietly, and the relation list must match the server's own or the menu offers a button that
 * always errors. Both are pinned here, against the ids `stokes/api/commands/selection.py` accepts.
 */

import { describe, expect, it } from "vitest";

import {
  RELATED_RELATIONS,
  relatedHow,
  relatedItems,
  relationById,
  relatedSummary,
} from "./related";

/** The relations the server accepts, copied from `RELATIONS` in selection.py. */
const SERVER_RELATIONS = ["same_area", "coplanar", "parallel", "coaxial", "tangent", "same_color"];

describe("RELATED_RELATIONS", () => {
  it("offers exactly the relations the server accepts, in the same order", () => {
    expect(RELATED_RELATIONS.map((r) => r.id)).toEqual(SERVER_RELATIONS);
  });

  it("gives every relation a label and a detail for its tooltip", () => {
    for (const relation of RELATED_RELATIONS) {
      expect(relation.label.length).toBeGreaterThan(0);
      expect(relation.detail.length).toBeGreaterThan(0);
    }
  });

  it("states each relation's tolerance unit as the server's code takes it (A7)", () => {
    // Corrected against `selection.RELATIONS` and `related.py`. The previous version of this test
    // pinned `coplanar` and `tangent` to radians, which was WRONG: `coplanar` takes a fraction of
    // the part's bounding-box diagonal, and `tangent` takes degrees (it calls `grow_parts`, whose
    // angle is validated as 0 < a <= 180).
    expect(relationById("coplanar")?.toleranceUnit).toBe("diagonal fraction");
    expect(relationById("parallel")?.toleranceUnit).toBe("radians");
    expect(relationById("coaxial")?.toleranceUnit).toBe("radians");
    expect(relationById("tangent")?.toleranceUnit).toBe("degrees");
    expect(relationById("same_area")?.toleranceUnit).toBe("relative");
    expect(relationById("same_color")?.toleranceUnit).toBe("relative");
  });

  it("resolves an id and refuses one this build does not offer", () => {
    expect(relationById("coaxial")?.label).toBe("Coaxial");
    expect(relationById("nonsense")).toBeUndefined();
  });
});

describe("relatedHow", () => {
  it("adds on a plain click, so the seed is kept", () => {
    expect(relatedHow(false)).toBe("add");
  });

  it("replaces on a Shift-click, as §6.4 specifies", () => {
    expect(relatedHow(true)).toBe("replace");
  });
});

describe("relatedItems", () => {
  it("expands the server's canonical ranges to face ids", () => {
    expect(relatedItems([{ part: "n_1", faces: [[0, 2], [5, 5]] }])).toEqual([
      { part: "n_1", faces: [0, 1, 2, 5] },
    ]);
  });

  it("keeps several parts apart", () => {
    expect(relatedItems([
      { part: "n_1", faces: [[0, 0]] },
      { part: "n_2", faces: [[3, 4]] },
    ])).toEqual([
      { part: "n_1", faces: [0] },
      { part: "n_2", faces: [3, 4] },
    ]);
  });

  it("drops a whole-part member: a relations answer is faces", () => {
    expect(relatedItems([{ part: "n_1", faces: "all" }])).toEqual([]);
  });

  it("drops an empty range list rather than adding a bare part", () => {
    expect(relatedItems([{ part: "n_1", faces: [] }])).toEqual([]);
  });

  it("has nothing for an empty answer", () => {
    expect(relatedItems([])).toEqual([]);
  });
});

describe("relatedSummary", () => {
  it("counts faces and parts, in the singular and the plural", () => {
    expect(relatedSummary([{ faces: [1] }])).toBe("1 face on 1 part");
    expect(relatedSummary([{ faces: [1, 2, 3] }, { faces: [4] }])).toBe("4 faces on 2 parts");
  });

  it("says so plainly when nothing matched", () => {
    expect(relatedSummary([])).toBe("no related faces");
  });
});
