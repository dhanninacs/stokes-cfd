/**
 * Sketch planes in the world (ADR-0039): the plane's u, v and w axes, a sketch point in world
 * metres, and the orthographic camera that shows the plane face-on so the sketcher's drawing lies
 * exactly over it (same origin, same millimetres per pixel).
 *
 * The axes are the server's (`stokes/implicit/sketch.py`): xy is (x, y, z), yz is (y, z, x), zx is
 * (z, x, y), with w the pull direction. The viewport draws model coordinates as they are, so these
 * are three.js world axes too. Pure: the tests run in Node.
 */

export type PlaneId = "xy" | "yz" | "zx";
export type Vec3 = [number, number, number];

const AXES: Record<PlaneId, { u: Vec3; v: Vec3; w: Vec3 }> = {
  xy: { u: [1, 0, 0], v: [0, 1, 0], w: [0, 0, 1] },
  yz: { u: [0, 1, 0], v: [0, 0, 1], w: [1, 0, 0] },
  zx: { u: [0, 0, 1], v: [1, 0, 0], w: [0, 1, 0] },
};

export const PLANE_IDS: readonly PlaneId[] = ["xy", "yz", "zx"];

/** Whether `value` names a sketch plane. */
export function isPlane(value: unknown): value is PlaneId {
  return value === "xy" || value === "yz" || value === "zx";
}

/** The plane's u (screen right), v (screen up) and w (normal, the pull direction). */
export function planeAxes(plane: PlaneId): { u: Vec3; v: Vec3; w: Vec3 } {
  return AXES[plane];
}

/** A sketch point (u, v in mm) at height `w` mm above the plane, in world metres. */
export function sketchToWorld(plane: PlaneId, origin: Vec3, u: number, v: number, w = 0): Vec3 {
  const a = AXES[plane];
  const out: Vec3 = [0, 0, 0];
  for (let i = 0; i < 3; i++) out[i] = origin[i] + (a.u[i] * u + a.v[i] * v + a.w[i] * w) / 1000;
  return out;
}

/** The origin parameter of a sketch as metres; anything that is not three numbers is the world origin. */
export function originOf(value: unknown): Vec3 {
  if (!Array.isArray(value) || value.length !== 3) return [0, 0, 0];
  const out: Vec3 = [0, 0, 0];
  for (let i = 0; i < 3; i++) {
    const c = value[i] as unknown;
    const n = typeof c === "number" ? c : typeof c === "object" && c !== null && typeof (c as { value?: unknown }).value === "number" ? (c as { value: number }).value : NaN;
    out[i] = Number.isFinite(n) ? n : 0;
  }
  return out;
}

/** A rectangle in client pixels. */
export interface ClientRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** The camera that puts the sketcher's view over the 3D canvas, face-on. */
export interface PlaneCamera {
  /** The point the camera looks at (the canvas centre), world metres. */
  target: Vec3;
  /** From the target towards the camera: the plane's normal. */
  dir: Vec3;
  /** Screen up: the plane's v axis. */
  up: Vec3;
  /** Height of the canvas in world metres. */
  span: number;
}

/**
 * The orthographic camera under which the sketch point (cu, cv) mm sits at the centre of the sketch
 * drawing `drawing` and one millimetre is `scale` pixels, on a canvas at `canvas` (client pixels).
 *
 * An orthographic view face-on to the plane maps the plane to the screen by a translation and one
 * scale, so matching the drawing's centre and its scale matches every point: the grid, the origin
 * and the entities lie over what they will build. The canvas centre shows the target, so the
 * target is the drawing's centre point moved back by the drawing's offset from the canvas centre.
 */
export function planeCamera(plane: PlaneId, origin: Vec3, view: { scale: number; cu: number; cv: number }, drawing: ClientRect, canvas: ClientRect): PlaneCamera {
  const a = AXES[plane];
  const pxPerMetre = view.scale * 1000;
  const centre = sketchToWorld(plane, origin, view.cu, view.cv);
  const dx = drawing.left + drawing.width / 2 - (canvas.left + canvas.width / 2);
  const dy = drawing.top + drawing.height / 2 - (canvas.top + canvas.height / 2);
  const target: Vec3 = [0, 0, 0];
  for (let i = 0; i < 3; i++) target[i] = centre[i] - (a.u[i] * dx) / pxPerMetre + (a.v[i] * dy) / pxPerMetre;
  return { target, dir: [...a.w], up: [...a.v], span: canvas.height / pxPerMetre };
}
