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
import { CameraRig } from "./camera";

const points = [-2, 2].flatMap(x => [-0.3, 0.3].flatMap(y => [-0.3, 0.3].map(z => new THREE.Vector3(x, y, z))));
const free = { x: 580, y: 20, width: 1280, height: 860 };
function check(rig: CameraRig) {
  rig.camera.updateMatrixWorld();
  const projected = points.map(p => p.clone().project(rig.camera));
  const xs = projected.map(p => (p.x + 1) * 950);
  const ys = projected.map(p => (1 - p.y) * 475);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  expect((minX + maxX) / 2).toBeCloseTo(free.x + free.width / 2, 5);
  expect((minY + maxY) / 2).toBeCloseTo(free.y + free.height / 2, 5);
  expect(minX).toBeGreaterThan(free.x); expect(maxX).toBeLessThan(free.x + free.width);
  expect(minY).toBeGreaterThan(free.y); expect(maxY).toBeLessThan(free.y + free.height);
}
describe("camera fit projection wiring", () => {
  for (const ortho of [false, true]) {
    it(`centres first load and repeated fits with late panels, orthographic=${ortho}`, () => {
      const rig = new CameraRig({} as HTMLCanvasElement);
      rig.setSize(1900, 950);
      rig.setContent(points, new THREE.Sphere(new THREE.Vector3(), 2.1));
      rig.setOrthographic(ortho);
      rig.intro();
      rig.setFreeRect(free);
      rig.update(1);
      check(rig);
      for (let i = 0; i < 3; i++) { rig.fit(); rig.update(1); check(rig); }
      rig.animateTo(rig.fitState()!, 0, true); check(rig);
    });
  }
});

describe("a fit stays a fit when the panels arrive after it ended", () => {
  for (const ortho of [false, true]) {
    it(`re-frames for a free area set once the opening move is over, orthographic=${ortho}`, () => {
      // The free area comes on a later animation frame, and a hidden window holds those back: the
      // opening move had ended and the model stayed framed for the whole canvas (the elbow spilled 16 %).
      const rig = new CameraRig({} as HTMLCanvasElement);
      rig.setSize(1900, 950);
      rig.setContent(points, new THREE.Sphere(new THREE.Vector3(), 2.1));
      rig.setOrthographic(ortho);
      rig.intro();
      rig.update(1);
      rig.setFreeRect(free);
      check(rig);
    });
  }

  it("leaves a view the user was given back where it is", () => {
    const rig = new CameraRig({} as HTMLCanvasElement);
    rig.setSize(1900, 950);
    rig.setContent(points, new THREE.Sphere(new THREE.Vector3(), 2.1));
    rig.fit();
    rig.update(1);
    const saved = { target: new THREE.Vector3(1, 0, 0), dir: new THREE.Vector3(0, 0, 1), span: 0.5 };
    rig.restore(saved);
    const before = rig.camera.position.clone();
    rig.setFreeRect(free);
    rig.setSize(1600, 900);
    expect(rig.camera.position.distanceTo(before)).toBeLessThan(1e-9);
  });
});

describe("a fit aims at the centre it is given with its points (R4c)", () => {
  it("frames the points' own box, not the sphere of the last flush", () => {
    const rig = new CameraRig({} as HTMLCanvasElement);
    rig.setSize(1900, 950);
    // The last flush saw only the outlet end, 10 m away from where the body's patches are.
    rig.setContent([new THREE.Vector3(10, 0, 0)], new THREE.Sphere(new THREE.Vector3(10, 0, 0), 0.3));
    rig.setFitPoints(points, new THREE.Vector3(0, 0, 0));
    const state = rig.fitState(new THREE.Vector3(0, 0, 1))!;
    expect(state.target.x).toBeCloseTo(0, 9);
    // Without the centre the same points would be aimed at the stale one.
    const stale = new CameraRig({} as HTMLCanvasElement);
    stale.setSize(1900, 950);
    stale.setContent([new THREE.Vector3(10, 0, 0)], new THREE.Sphere(new THREE.Vector3(10, 0, 0), 0.3));
    stale.setFitPoints(points);
    expect(stale.fitState(new THREE.Vector3(0, 0, 1))!.target.x).toBeCloseTo(10, 9);
  });
});

describe("a face-on fit turns the camera's up so the long axis lies across the screen (R4e)", () => {
  const pipe = [-0.05, 0.05].flatMap((x) => [-0.05, 0.05].flatMap((y) => [-1, 1].map((z) => new THREE.Vector3(x, y, z))));
  const extentOnScreen = (rig: CameraRig): { w: number; h: number } => {
    rig.camera.updateMatrixWorld();
    const p = pipe.map((v) => v.clone().project(rig.camera));
    const xs = p.map((v) => v.x), ys = p.map((v) => v.y);
    return { w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
  };
  for (const ortho of [false, true]) {
    it(`a pipe along z from +y: with up +x it lies across the screen, with world Y it stands on end, orthographic=${ortho}`, () => {
      const rig = new CameraRig({} as HTMLCanvasElement);
      rig.setSize(1900, 950);
      rig.setContent(pipe, new THREE.Sphere(new THREE.Vector3(), 1.1));
      rig.setOrthographic(ortho);
      rig.fit(new THREE.Vector3(0, 1, 0), new THREE.Vector3(1, 0, 0));
      rig.update(1);
      const turned = extentOnScreen(rig);
      expect(turned.w).toBeGreaterThan(turned.h * 4);
      rig.fit(new THREE.Vector3(0, 1, 0));
      rig.update(1);
      const plain = extentOnScreen(rig);
      expect(plain.h).toBeGreaterThan(plain.w * 4);
    });
  }
});
