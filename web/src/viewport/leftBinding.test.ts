import { describe, expect, it, vi } from "vitest";
import * as THREE from "three";
vi.mock("../app/prefs", () => ({ prefersReducedMotion: () => false }));
vi.mock("three/addons/controls/OrbitControls.js", () => ({
  OrbitControls: class {
    target = new THREE.Vector3(); mouseButtons: Record<string, unknown> = {}; touches = {};
    minDistance = 0; maxDistance = Infinity; minZoom = 0; maxZoom = Infinity;
    constructor(public object: THREE.Camera) {}
    addEventListener() {} update() { return false; } dispose() {}
  },
}));
import { CameraRig } from "./camera";

/**
 * 2026-11-05: under cad, blender and toggle the left button was bound to nothing, and OrbitControls ignores such a
 * button, so a left drag the scene handed to the camera (a press on a part) never turned it. Only the middle button
 * rotated. The scene already switches the controls off for a box, a lasso, a stroke and a handle drag, so the
 * binding can be ROTATE everywhere.
 */
describe("the left button's binding", () => {
  it.each(["cad", "paraview", "blender", "toggle"] as const)("rotates under %s", (mode) => {
    const rig = new CameraRig({} as HTMLCanvasElement);
    rig.setNavMode(mode);
    expect((rig.controls.mouseButtons as { LEFT: unknown }).LEFT).toBe(THREE.MOUSE.ROTATE);
  });
});
