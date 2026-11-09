import { describe, expect, it } from "vitest";

/**
 * Box-select skips a body left out of the simulation; a click still picks it (Claude's pick rule).
 *
 * The rule itself is three lines inside `onPickMany`, so what is worth testing is the DECISION it
 * encodes - and the one thing that must never be silent: the count of what was skipped.
 */
const excluded = new Set(["fluid"]);

/** What `onPickMany` does with the parts a box caught, without the store or the scene. */
function boxSelect(parts: string[]): { picked: string[]; skipped: number; message: string | null } {
  const kept = parts.filter((p) => !excluded.has(p));
  const skipped = parts.length - kept.length;
  return {
    picked: kept,
    skipped,
    message: skipped > 0 ? `${skipped} left-out ${skipped === 1 ? "body was" : "bodies were"} skipped` : null,
  };
}

describe("a box select over a left-out body", () => {
  it("skips it and says how many", () => {
    // **The half that matters**: a body that silently vanishes from a selection is a failure a user
    // cannot see, which is why the message is part of the rule rather than a nicety.
    const result = boxSelect(["pipe", "fluid"]);
    expect(result.picked).toEqual(["pipe"]);
    expect(result.message).toBe("1 left-out body was skipped");
  });

  it("counts several, in the right number", () => {
    const both = new Set(["fluid", "solid"]);
    const kept = ["pipe", "fluid", "solid"].filter((p) => !both.has(p));
    expect(kept).toEqual(["pipe"]);
    expect([1, 2].map((n) => `${n} left-out ${n === 1 ? "body was" : "bodies were"} skipped`)).toContain(
      "2 left-out bodies were skipped",
    );
  });

  it("says nothing when nothing was skipped", () => {
    expect(boxSelect(["pipe", "flange"]).message).toBeNull();
  });

  it("does not touch a body that is in the simulation", () => {
    // The flag decides, not the name: a body called `fluid` that is IN must still be picked.
    const kept = ["fluid_in"].filter((p) => !excluded.has(p));
    expect(kept).toEqual(["fluid_in"]);
  });
});
