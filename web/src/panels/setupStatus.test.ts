/**
 * The set-up status readout's wording (ADR-0022 §11.1, T-049).
 *
 * The line is what a reviewer reads and what silently drifts when a count is reordered, so it is
 * tested directly: the label always leads, `next` is not repeated when it already says "Set-up", the
 * trailing figure is the first non-zero one in §11.1's order, and each state maps to its own colour.
 */

import { describe, expect, it } from "vitest";

import { SETUP_DOT, setupCountPhrase, setupReadout } from "./setupStatus";
import type { SetupCheck, SetupState } from "../api/types";

function check(over: Partial<SetupCheck> = {}): SetupCheck {
  return {
    state: "incomplete",
    next: "needs an outlet",
    counts: { regions: 1, materials: 1, boundaries: 2, uncovered_faces: 12, legacy: 0 },
    findings: [],
    ...over,
  };
}

describe("setupReadout", () => {
  it("is null before the first answer, so the readout claims nothing", () => {
    expect(setupReadout(null)).toBeNull();
  });

  it("reads 'Set-up · next · count', as §11.1 specifies", () => {
    expect(setupReadout(check())?.text).toBe("Set-up · needs an outlet · 12 faces unassigned");
  });

  it("always leads with the label, even with nothing else to say", () => {
    const bare = check({ next: "", counts: { regions: 0, materials: 0, boundaries: 0, uncovered_faces: 0, legacy: 0 } });
    expect(setupReadout(bare)?.text).toBe("Set-up");
  });

  it("does not repeat the label when `next` already begins with it", () => {
    // The server's `next` sometimes opens with "Set-up" itself; the label is not printed twice.
    expect(setupReadout(check({ next: "Set-up needs an outlet" }))?.text).toBe(
      "Set-up · 12 faces unassigned",
    );
    expect(setupReadout(check({ next: "Set up needs an outlet" }))?.text).toBe(
      "Set-up · 12 faces unassigned",
    );
    // A `next` that does not say it is kept in full.
    expect(setupReadout(check({ next: "needs an outlet" }))?.text).toBe(
      "Set-up · needs an outlet · 12 faces unassigned",
    );
  });

  it("prints the first non-zero figure in §11.1's order: faces, then a boundary problem, then regions", () => {
    const faces = check({ counts: { regions: 3, materials: 1, boundaries: 4, uncovered_faces: 7, legacy: 0 } });
    expect(setupCountPhrase(faces)).toBe("7 faces unassigned");
    // No uncovered faces but an error finding: the boundary count is the useful figure.
    const boundaries = check({
      counts: { regions: 1, materials: 1, boundaries: 3, uncovered_faces: 0, legacy: 0 },
      findings: [{ code: "setup.boundary_open", severity: "error", message: "an inlet has no faces" }],
    });
    expect(setupCountPhrase(boundaries)).toBe("3 boundaries");
  });

  it("calls the open faces of a ready set-up walls, which is what they become", () => {
    const ready = check({ state: "ready", next: "", counts: { regions: 1, materials: 1, boundaries: 4, uncovered_faces: 4, legacy: 0 } });
    expect(setupReadout(ready)?.text).toBe("Set-up · 4 faces left as wall");
  });

  it("counts a single face and a single boundary in the singular", () => {
    expect(setupCountPhrase(check({ counts: { regions: 0, materials: 0, boundaries: 0, uncovered_faces: 1, legacy: 0 } }))).toBe("1 face unassigned");
    const one = check({
      counts: { regions: 0, materials: 0, boundaries: 1, uncovered_faces: 0, legacy: 0 },
      findings: [{ code: "x", severity: "error", message: "m" }],
    });
    expect(setupCountPhrase(one)).toBe("1 boundary");
  });

  it("says nothing about a zero count", () => {
    const empty = check({
      next: "",
      counts: { regions: 0, materials: 0, boundaries: 0, uncovered_faces: 0, legacy: 0 },
    });
    expect(setupCountPhrase(empty)).toBe("");
  });

  it("mentions the regions only once the set-up is ready", () => {
    const ready = check({
      state: "ready",
      next: "ready to mesh",
      counts: { regions: 2, materials: 2, boundaries: 4, uncovered_faces: 0, legacy: 0 },
      findings: [],
    });
    expect(setupCountPhrase(ready)).toBe("2 regions");
  });

  it("describes the findings in the tooltip, or says all is well", () => {
    expect(setupReadout(check())?.detail).toBe("Everything needed for a run is defined.");
    const withFindings = check({
      findings: [
        { code: "a", severity: "error", message: "no outlet", suggestion: "add an outlet" },
        { code: "b", severity: "warning", message: "overlap" },
      ],
    });
    expect(setupReadout(withFindings)?.detail).toBe("add an outlet · overlap");
  });

  it("carries the state through unchanged, for the dot", () => {
    for (const state of ["not_defined", "incomplete", "stale", "ready"] as SetupState[]) {
      const readout = setupReadout(check({ state }));
      expect(readout?.state).toBe(state);
      expect(SETUP_DOT[state]).toMatch(/var\(--/);
    }
  });

  it("gives every state a distinct colour", () => {
    const colours = new Set(Object.values(SETUP_DOT));
    expect(colours.size).toBe(Object.keys(SETUP_DOT).length);
  });
});
