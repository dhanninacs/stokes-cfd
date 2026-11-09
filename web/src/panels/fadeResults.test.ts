import { describe, expect, it } from "vitest";
import { fadeAvailable, fadeTargets } from "./fadeResults";

/**
 * G-301's D4: the fade is one `geometry.style` write with a LIST, so the only new logic is WHICH nodes
 * go in that list - and getting it wrong fades the fluid the colours are drawn on.
 */
describe("the results fade", () => {
  const parts = [
    { id: "pipe", simulate: true },
    { id: "inlet_flange", simulate: true },
    { id: "spare", simulate: false },
  ];

  it("fades every body that is in the simulation", () => {
    expect(fadeTargets(parts)).toEqual(["pipe", "inlet_flange"]);
  });

  it("leaves out a body the user took out of the simulation", () => {
    // It is not drawn in the result, so an opacity written to it is a model edit with nothing to show.
    expect(fadeTargets([{ id: "spare", simulate: false }])).toEqual([]);
  });

  it("is unavailable with no results, or with nothing to fade", () => {
    expect(fadeAvailable(true, ["pipe"])).toBe(true);
    expect(fadeAvailable(false, ["pipe"])).toBe(false);
    expect(fadeAvailable(true, [])).toBe(false);
  });
});
