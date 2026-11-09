/**
 * What a LEFT-button press means to the camera, by mouse scheme (ADR-0021 3.4, amended 2026-10-06 and
 * 2026-11-05). Pure, so the rule that decides "orbit or draw a box" can be tested without a canvas:
 * it was buried in the pointer handler, and its two faults (a drag on empty background drawing a box
 * under the default scheme; the Lasso toggle ignored under the orbit scheme) were found by hand.
 */
import type { NavMode } from "../state/store";

export interface LeftPress {
  navMode: NavMode;
  /** The Select / Orbit switch of the `toggle` scheme. */
  orbitMode: boolean;
  /** The Lasso toggle of the Modes island. */
  lassoMode: boolean;
  shift: boolean;
  alt: boolean;
  /** The press landed on a part (the surface under the pointer, or the Alt+wheel candidate). */
  onPatch: boolean;
  /**
   * Nothing here can be selected (the Results stage: the surface under the pointer is a slice or a cut, not a
   * patch), so a plain left drag is the camera's whatever the scheme. Shift still asks for the box.
   */
  orbitOnly?: boolean;
}

/** True when the press belongs to the camera: the drag orbits. False: it starts the box or lasso. */
export function leftPressOrbits(p: LeftPress): boolean {
  if (p.orbitOnly === true && !p.shift && !p.alt && !p.lassoMode) return true;
  switch (p.navMode) {
    // Orbit anywhere; the box is Shift, the lasso is Alt or the island's toggle.
    case "paraview":
      return !p.shift && !p.alt && !p.lassoMode;
    case "toggle":
      return p.orbitMode && !p.shift;
    // cad, blender: a press on a part orbits, a drag on empty space draws the box.
    default:
      return p.onPatch && !p.alt;
  }
}
