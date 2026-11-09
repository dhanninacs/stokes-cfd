import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { arrowMesh, legible } from "./arrowLayer";

/**
 * The arrow mesh (results item 2 of claude-win2's list), tested on the instance data rather than a
 * picture: an `InstancedMesh` carries its matrices and colours, so what would be drawn is readable.
 */
const seed = (speed: number, dir: [number, number, number] = [1, 0, 0]) => ({
  point: [0, 0, 0] as const,
  vector: dir,
  speed,
});

describe("arrowMesh", () => {
  it("has ONE instance per seed, in one mesh", () => {
    // One InstancedMesh, not one object per arrow: 200 arrows would otherwise be 200 draw calls.
    const mesh = arrowMesh([seed(1), seed(2), seed(3)], 0.002, { min: 1, max: 3 });
    expect(mesh).not.toBeNull();
    expect(mesh!.count).toBe(3);
    expect(mesh!.isInstancedMesh).toBe(true);
  });

  it("orients an arrow along its velocity", () => {
    // The direction is the half of the message a colour map cannot carry.
    const mesh = arrowMesh([seed(1, [0, 1, 0])], 0.002, { min: 0, max: 1 })!;
    const matrix = new THREE.Matrix4();
    mesh.getMatrixAt(0, matrix);
    const up = new THREE.Vector3(0, 0, 1).transformDirection(matrix);
    // +Z in the arrow's own space now points along +Y in the world.
    expect(up.y).toBeCloseTo(1, 5);
    expect(up.x).toBeCloseTo(0, 5);
  });

  it("draws a slower arrow SHORTER, so a sparse region reads as sparse", () => {
    const mesh = arrowMesh([seed(1), seed(3)], 0.002, { min: 1, max: 3 })!;
    const m = new THREE.Matrix4();
    mesh.getMatrixAt(0, m);
    const slow = new THREE.Vector3().setFromMatrixScale(m).z;
    mesh.getMatrixAt(1, m);
    const fast = new THREE.Vector3().setFromMatrixScale(m).z;
    expect(slow).toBeLessThan(fast);
    expect(fast).toBeCloseTo(0.002, 6);
  });

  it("draws the length PROPORTIONAL to the speed: |U| / max |U| (2026-11-04)", () => {
    // The old rule placed an arrow in the min..max range with a floor of 0.15: a 1.0 m/s arrow beside a
    // 1.2 m/s one came out at 15 % of its length, a magnitude the field does not have.
    const mesh = arrowMesh([seed(1.0), seed(1.2), seed(0.6)], 0.01, { min: 0.6, max: 1.2 })!;
    const m = new THREE.Matrix4();
    const lengthOf = (i: number): number => {
      mesh.getMatrixAt(i, m);
      return new THREE.Vector3().setFromMatrixScale(m).z;
    };
    expect(lengthOf(1)).toBeCloseTo(0.01, 9);
    expect(lengthOf(0)).toBeCloseTo(0.01 * (1.0 / 1.2), 9);
    expect(lengthOf(2)).toBeCloseTo(0.005, 9);
  });

  it("is depth-tested, so nearer arrows hide farther ones and the cloud has a front and a back", () => {
    const mesh = arrowMesh([seed(1)], 0.002, { min: 0, max: 1 })!;
    const material = mesh.material as THREE.Material;
    expect(material.depthTest).toBe(true);
    expect(material.depthWrite).toBe(true);
  });

  it("has nothing to draw for no seeds, or a zero length", () => {
    expect(arrowMesh([], 0.002, { min: 0, max: 1 })).toBeNull();
    expect(arrowMesh([seed(1)], 0, { min: 0, max: 1 })).toBeNull();
  });

  it("lifts the dark end of a colour map so a slow arrow is still visible", () => {
    const luminance = ([r, g, b]: readonly number[]) => (0.2126 * r! + 0.7152 * g! + 0.0722 * b!) / 255;
    // Viridis' first stop is nearly black; it must come out clearly lighter, with its hue (blue over green over red kept in order).
    const dark: [number, number, number] = [68, 1, 84];
    const lifted = legible(dark);
    expect(luminance(lifted)).toBeGreaterThan(0.3);
    expect(lifted[0]).toBeGreaterThan(dark[0]);
    // A colour that is already bright is left exactly as it was.
    expect(legible([253, 231, 37])).toEqual([253, 231, 37]);
  });

  it("colours by speed on a range shared with every other arrow", () => {
    const mesh = arrowMesh([seed(1), seed(3)], 0.002, { min: 1, max: 3 })!;
    const c = new THREE.Color();
    mesh.getColorAt(0, c);
    const slow = c.getHex();
    mesh.getColorAt(1, c);
    expect(c.getHex()).not.toBe(slow);
  });
});
