/**
 * The mouse schemes (ADR-0021 3.4 as amended): the list the Modes island offers, and the cycle.
 *
 * The bug these guard against: as first written the left button could not rotate, so a user could
 * only orbit by pressing on a part; and with CAD as the default (2026-10-06 amendment) a left drag
 * on the empty background still drew a selection box, so the walkthrough of 2026-11-05 found the
 * view "broken" and a stray box changing the face selection. Every scheme must rotate, and the
 * DEFAULT must rotate on a plain left drag from anywhere, with the box on Shift.
 */
import { describe, expect, it, vi } from "vitest";

// `state/store` reads `window` as it loads (treeSync's dev hook) and this test has no DOM; the hook
// only reads it, so the global itself stands in. Hoisted, so it runs before the imports.
vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

import { NAV_MODES, cycleNavMode } from "./actions";
import { useStore, type NavMode } from "../state/store";

describe("the mouse schemes", () => {
  it("offers the four schemes, each naming what rotates and what the left drag does", () => {
    expect(NAV_MODES.map((m) => m.id)).toEqual(["cad", "paraview", "blender", "toggle"]);
    for (const mode of NAV_MODES) {
      expect(mode.label).not.toBe("");
      // The summary is the menu's hint: it has to say how to rotate, which is the whole point.
      expect(mode.summary.toLowerCase()).toContain("rotat");
    }
  });

  it("rotates on a plain left drag in the default scheme, and keeps the box on Shift", () => {
    expect(useStore.getState().navMode).toBe("paraview");
    const orbit = NAV_MODES.find((m) => m.id === "paraview");
    const text = (orbit?.summary ?? "").toLowerCase();
    expect(text).toContain("left-drag rotates anywhere");
    expect(text).toContain("shift");
    // CAD stays on offer for people who rotate with the middle button.
    expect(NAV_MODES.map((m) => m.id)).toContain("cad");
  });

  it("walks the schemes in order and comes back around", () => {
    const seen: NavMode[] = [];
    useStore.getState().setNavMode("cad");
    for (let i = 0; i < NAV_MODES.length + 1; i++) {
      seen.push(useStore.getState().navMode);
      cycleNavMode();
    }
    expect(seen).toEqual(["cad", "paraview", "blender", "toggle", "cad"]);
  });

  it("resets the Select/Orbit switch when the scheme changes", () => {
    useStore.getState().setNavMode("toggle");
    useStore.getState().setOrbitMode(true);
    expect(useStore.getState().orbitMode).toBe(true);
    useStore.getState().setNavMode("cad");
    expect(useStore.getState().orbitMode).toBe(false);
  });
});
