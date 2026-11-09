/**
 * The left button under each mouse scheme. The 2026-11-05 walkthrough found a drag on the empty
 * background drawing a selection box (and changing the faces picked) under the default scheme, and
 * the Lasso toggle doing nothing under the orbit scheme.
 */
import { describe, expect, it } from "vitest";
import { leftPressOrbits, type LeftPress } from "./leftDrag";

const press = (over: Partial<LeftPress>): LeftPress => ({
  navMode: "paraview",
  orbitMode: false,
  lassoMode: false,
  shift: false,
  alt: false,
  onPatch: false,
  ...over,
});

describe("the Results stage", () => {
  it("orbits on a drag over a slice under every scheme, and still gives Shift the box", () => {
    for (const navMode of ["cad", "blender", "paraview"] as const) {
      expect(leftPressOrbits(press({ navMode, onPatch: false, orbitOnly: true }))).toBe(true);
      expect(leftPressOrbits(press({ navMode, onPatch: false, orbitOnly: true, shift: true }))).toBe(false);
    }
  });
});

describe("the default (orbit) scheme", () => {
  it("orbits on a plain drag from the empty background, where the hint says it does", () => {
    expect(leftPressOrbits(press({ onPatch: false }))).toBe(true);
    expect(leftPressOrbits(press({ onPatch: true }))).toBe(true);
  });

  it("draws the box on Shift, and the lasso on Alt or the Lasso toggle", () => {
    expect(leftPressOrbits(press({ shift: true }))).toBe(false);
    expect(leftPressOrbits(press({ alt: true }))).toBe(false);
    expect(leftPressOrbits(press({ lassoMode: true }))).toBe(false);
  });
});

describe("the CAD and Blender schemes", () => {
  for (const navMode of ["cad", "blender"] as const) {
    it(`${navMode}: orbits on a part, draws the box on empty space`, () => {
      expect(leftPressOrbits(press({ navMode, onPatch: true }))).toBe(true);
      expect(leftPressOrbits(press({ navMode, onPatch: false }))).toBe(false);
      expect(leftPressOrbits(press({ navMode, onPatch: true, alt: true }))).toBe(false);
    });
  }
});

describe("the Select / Orbit scheme", () => {
  it("follows the switch, and Shift still asks for the box", () => {
    expect(leftPressOrbits(press({ navMode: "toggle", orbitMode: true }))).toBe(true);
    expect(leftPressOrbits(press({ navMode: "toggle", orbitMode: false, onPatch: true }))).toBe(false);
    expect(leftPressOrbits(press({ navMode: "toggle", orbitMode: true, shift: true }))).toBe(false);
  });
});
