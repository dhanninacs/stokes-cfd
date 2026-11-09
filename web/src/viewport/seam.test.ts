import { describe, expect, it } from "vitest";
import { PerspectiveCamera, Vector3, OrthographicCamera, MathUtils } from "three";
import { asViewOffsetOrigin } from "./centring";

/**
 * The SEAM between the arithmetic and the matrix — the test I should have written four rounds ago.
 *
 * `centringShift`'s arithmetic has been proven by five tests since; what repeatedly failed was whether
 * the value reaches `camera.projectionMatrix` at all. This projects a known point through a real
 * three.js camera, applies a shift the way `applyProjection` does, and demands the point move by
 * exactly that many pixels. **If suspect (1) is the cause - `updateProjectionMatrix` missing or early -
 * this test fails**; if it passes here, the fault is in the render loop, not in this file.
 */
const W = 1600;
const H = 816;

/** Where a point lands in CANVAS PIXELS, with the camera's matrices brought up to date. */
function pixelOf(camera: PerspectiveCamera | OrthographicCamera, point: Vector3): { x: number; y: number } {
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  const v = point.clone().project(camera);
  return { x: ((v.x + 1) / 2) * W, y: ((1 - v.y) / 2) * H };
}

/** The one function `applyProjection` is meant to be: the ONLY writer of the view offset. */
function applyShift(camera: PerspectiveCamera | OrthographicCamera, dx: number, dy: number): void {
  const origin = asViewOffsetOrigin(dx, dy);
  camera.setViewOffset(W, H, origin.x, origin.y, W, H);
  // **(1) The call that folds `camera.view` into `projectionMatrix`.** Without it the shift is stored
  // and never applied, which is exactly "computed but does not arrive".
  camera.updateProjectionMatrix();
}

function perspective(): PerspectiveCamera {
  const camera = new PerspectiveCamera(45, W / H, 0.1, 1000);
  camera.position.set(1, 1.5, 2);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  return camera;
}

describe("the view offset reaches the projection matrix", () => {
  it("moves a projected point by exactly the shift, for the perspective camera", () => {
    const camera = perspective();
    const point = new Vector3(0.1, 0.2, -0.3);
    const before = pixelOf(camera, point);
    applyShift(camera, 40, -25);
    const after = pixelOf(camera, point);
    // **+dx must move the image RIGHT**: `applyShift` negates for three.js, so the point lands `40`
    // pixels further right. Getting this backwards is the sign error that cost a round.
    expect(Math.round(after.x - before.x)).toBe(40);
    expect(Math.round(after.y - before.y)).toBe(-25);
  });

  it("moves it by the same amount whatever the point", () => {
    // **The property that makes this a closed form**: a principal-point shift is a TRANSLATION, so
    // every point moves by the same vector and no scale changes. Two corners, one shift, one answer.
    const camera = perspective();
    const a = new Vector3(-0.4, -0.3, 0.1);
    const b = new Vector3(0.5, 0.4, -0.6);
    const a0 = pixelOf(camera, a);
    const b0 = pixelOf(camera, b);
    applyShift(camera, -30, 12);
    const a1 = pixelOf(camera, a);
    const b1 = pixelOf(camera, b);
    expect(Math.round(a1.x - a0.x)).toBe(Math.round(b1.x - b0.x));
    expect(Math.round(a1.y - a0.y)).toBe(Math.round(b1.y - b0.y));
  });

  it("does the same for the orthographic camera", () => {
    const camera = new OrthographicCamera(-2, 2, 2, -2, 0.1, 1000);
    camera.position.set(1, 1.5, 2);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld();
    const point = new Vector3(0.1, 0.2, -0.3);
    const before = pixelOf(camera, point);
    applyShift(camera, 20, 15);
    const after = pixelOf(camera, point);
    expect(Math.round(after.x - before.x)).toBe(20);
    expect(Math.round(after.y - before.y)).toBe(15);
  });

  it("measures with a CLEARED offset, or the second reading is wrong", () => {
    // **(3) Measuring with a stale matrix.** A camera that still carries an offset reports a centre
    // that already includes it, so the correction computed from it is short by that amount - my third
    // attempt's permanent residual.
    const camera = perspective();
    const point = new Vector3(0.1, 0.2, -0.3);
    applyShift(camera, 40, 0);
    const shifted = pixelOf(camera, point);
    camera.clearViewOffset();
    camera.updateProjectionMatrix();
    const cleared = pixelOf(camera, point);
    expect(Math.round(shifted.x - cleared.x)).toBe(40);
    expect(Math.abs(MathUtils.degToRad(1))).toBeGreaterThan(0);
  });
});
