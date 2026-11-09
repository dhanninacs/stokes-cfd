import { describe, expect, it, vi } from "vitest";
import * as THREE from "three";
vi.mock("../app/prefs", () => ({ prefersReducedMotion: () => false }));
vi.mock("three/addons/controls/OrbitControls.js", () => ({
  OrbitControls: class {
    target = new THREE.Vector3(); mouseButtons = {}; touches = {};
    minDistance = 0; maxDistance = Infinity; minZoom = 0; maxZoom = Infinity;
    constructor(public object: THREE.Camera) {}
    addEventListener() {} update() { return false; } dispose() {}
  },
}));
import { CameraRig, type ViewState } from "./camera";

const points = [-2, 2].flatMap((x) => [-0.3, 0.3].flatMap((y) => [-0.3, 0.3].map((z) => new THREE.Vector3(x, y, z))));

function rig(): CameraRig {
  const r = new CameraRig({} as HTMLCanvasElement);
  r.setSize(1900, 950);
  r.setContent(points, new THREE.Sphere(new THREE.Vector3(), 2.1));
  return r;
}

const STATE: ViewState = { target: new THREE.Vector3(0.2, -0.1, 0.4), dir: new THREE.Vector3(0.3, 0.8, 0.5).normalize(), span: 0.9, up: new THREE.Vector3(1, 0, 0) };

function expectSame(a: ViewState, b: ViewState): void {
  for (const k of ["x", "y", "z"] as const) {
    expect(a.target[k]).toBeCloseTo(b.target[k], 9);
    expect(a.dir[k]).toBeCloseTo(b.dir[k], 9);
    expect(a.up?.[k]).toBeCloseTo(b.up?.[k] ?? NaN, 9);
  }
  expect(a.span).toBeCloseTo(b.span, 9);
}

describe("restoring a saved camera (R4)", () => {
  for (const ortho of [false, true]) {
    it(`gives back the same target, direction, span and up, orthographic=${ortho}`, () => {
      const r = rig();
      r.setOrthographic(ortho);
      r.restore(STATE);
      expect(r.isOrthographic).toBe(ortho);
      expectSame(r.getState(), STATE);
    });
  }

  it("lands exactly even with a framing move under way, and the move does not carry on", () => {
    const r = rig();
    r.fit();
    r.update(0.05);
    expect(r.animating).toBe(true);
    r.restore(STATE);
    expect(r.animating).toBe(false);
    r.update(1);
    expectSame(r.getState(), STATE);
  });

  it("takes the camera as the user's, so content arriving later does not re-frame it", () => {
    const r = rig();
    r.fit();
    r.update(1);
    expect(r.touched).toBe(false);
    r.restore(STATE);
    expect(r.touched).toBe(true);
  });

  it("keeps the up vector it has when the state names none", () => {
    const r = rig();
    r.restore(STATE);
    const { up: _up, ...noUp } = STATE;
    r.restore({ ...noUp, span: 0.5 });
    expect(r.getState().up?.x).toBeCloseTo(1, 9);
    expect(r.getState().span).toBeCloseTo(0.5, 9);
  });
});
