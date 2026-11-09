/** The Boundaries page's rule (brief M-502 §UI 5.3): the row hides at zero, and the button's one call. */
import { describe, expect, it } from "vitest";
import { keepAsWallPayload, unassignedRowVisible } from "./meshTaskPages/boundariesModel";

describe("unassignedRowVisible", () => {
  it("shows the row while faces have no boundary, and never at zero", () => {
    expect(unassignedRowVisible(12)).toBe(true);
    expect(unassignedRowVisible(0)).toBe(false);
  });
});

describe("keepAsWallPayload", () => {
  it("is exactly mesh.configure with accept_unassigned: true", () => {
    expect(keepAsWallPayload()).toEqual({ accept_unassigned: true });
  });
});
