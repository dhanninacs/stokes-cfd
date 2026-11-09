/**
 * Whether a point is on the side of the section plane that the cut KEEPS (T-065e).
 *
 * ## Why this exists
 *
 * A clip plane discards fragments when drawing, but it does nothing to a raycast: a pick ray passes
 * straight through the plane and finds geometry the user cannot see. So a click on empty-looking space
 * returns a face from the removed half, the hover highlights something invisible, and the readout names
 * a part that is not there (Claude's defect 7).
 *
 * ## What the rule is
 *
 * **A ray that meets removed geometry must ignore that hit and continue to the next one on the kept
 * side** — not return nothing. Returning nothing would make a click on the model's far wall dead, and
 * would hide the geometry behind the cut, which is the whole point of cutting a model open: every CAD
 * section view lets you pick what you can see through the opening.
 *
 * ## The plane's own convention, and why the sign is easy to get wrong
 *
 * `THREE.Plane` keeps the half-space where `normal · point + constant > 0`, which is what the renderer
 * clips by. The scene stores its plane with `invert` already folded into the normal's sign, so the test
 * is exactly the renderer's own: a point is kept when its signed distance is **positive**. Using the
 * opposite sign would pick precisely the geometry the cut removed, which looks like the feature working
 * backwards.
 *
 * ## The epsilon
 *
 * A point exactly on the plane — the cap itself, and every vertex of a cut face — has a distance of
 * zero, and floating point makes "exactly" unreliable. A point within `epsilon` of the plane is treated
 * as **kept**: it is the cut surface the user is looking at and clicking on, so erring towards keeping
 * it is erring towards the thing they meant. The epsilon is scaled to the model (`eps` is the caller's
 * model-relative figure), so the same tolerance behaves the same on a 5 cm part and a 5 m one.
 *
 * Pure, so the rule and its boundary cases are testable without a renderer or a scene.
 */

/** A `THREE.Plane`'s two fields, without importing three.js. */
export interface PlaneLike {
  normal: { x: number; y: number; z: number };
  constant: number;
}

/** A point in the plane's own space. */
export interface PointLike {
  x: number;
  y: number;
  z: number;
}

/**
 * How far `point` is along the plane's normal, sign included, with the plane's own convention.
 *
 * Positive is the KEPT side, because that is what the renderer clips by.
 */
export function planeDistance(point: PointLike, plane: PlaneLike): number {
  return plane.normal.x * point.x + plane.normal.y * point.y + plane.normal.z * point.z + plane.constant;
}

/**
 * Whether `point` is on the side the cut keeps, within `epsilon` of the plane.
 *
 * `epsilon` should come from the model's size (the caller's job): an absolute tolerance is wrong at
 * both ends of the scale — a thousandth of a millimetre is noise on a 5 m duct and a millimetre is a
 * visible gap on a 50 mm pipe.
 */
export function keptSide(point: PointLike, plane: PlaneLike, epsilon: number): boolean {
  // **A point within the epsilon is kept**, not removed: the cap and every vertex of a cut face lie
  // exactly on the plane, and they are what the user is looking at.
  return planeDistance(point, plane) >= -Math.abs(epsilon);
}

/**
 * The first item whose point is on the kept side, in the order given.
 *
 * The shape every raycast path needs: hits arrive nearest-first from three.js, so this is "skip the
 * clipped-away ones and take the next", which is the rule Claude set. `null` when the ray met nothing
 * on the kept side.
 */
export function firstKept<T>(
  hits: readonly T[],
  pointOf: (hit: T) => PointLike,
  plane: PlaneLike,
  epsilon: number,
): T | null {
  for (const hit of hits) {
    if (keptSide(pointOf(hit), plane, epsilon)) return hit;
  }
  return null;
}

/**
 * Where a ray meets the section plane, or `null` when it never does.
 *
 * ## Why the cap is a plane test and not a mesh pick
 *
 * The drawn cap is a quad sized to the model and laid on the cut, drawn with the **stencil** buffer so
 * it appears only where the cut actually exposes the body. It is not a solid the raycaster can hit in a
 * way that means anything: the quad is far larger than the model (it has to cover the widest cut), and
 * the stencil that clips it back to the body is not available to a CPU ray. So a ray that crosses the
 * plane inside the model's bounds is the honest test for "the user clicked the cut face", and the
 * caller supplies the bounds.
 *
 * `t` is the distance along the ray. Only a **forward** crossing counts: a plane behind the camera is
 * not something the user clicked.
 */
export function rayPlanePoint(
  origin: PointLike,
  direction: PointLike,
  plane: PlaneLike,
): { t: number; point: PointLike } | null {
  const denom =
    plane.normal.x * direction.x + plane.normal.y * direction.y + plane.normal.z * direction.z;
  // Parallel to the plane: either no crossing, or the ray lies in it, and neither is a cut face the
  // user aimed at. A tiny epsilon rather than `=== 0`, because a ray that grazes the plane at 1e-18
  // is parallel for this purpose and dividing by it would produce a wild `t`.
  if (Math.abs(denom) < 1e-12) return null;
  const t = -planeDistance(origin, plane) / denom;
  if (!(t >= 0)) return null;
  return {
    t,
    point: {
      x: origin.x + direction.x * t,
      y: origin.y + direction.y * t,
      z: origin.z + direction.z * t,
    },
  };
}

/** An axis-aligned box, in the same shape `THREE.Box3` reports bounds in. */
export interface BoxLike {
  min: PointLike;
  max: PointLike;
}

/** Whether a point is inside an axis-aligned box, within `epsilon` of each face. */
export function insideBox(point: PointLike, box: BoxLike, epsilon: number): boolean {
  return (
    point.x >= box.min.x - epsilon &&
    point.x <= box.max.x + epsilon &&
    point.y >= box.min.y - epsilon &&
    point.y <= box.max.y + epsilon &&
    point.z >= box.min.z - epsilon &&
    point.z <= box.max.z + epsilon
  );
}

/**
 * Whether a ray meets the cap: it crosses the section plane, **inside the model's bounds**.
 *
 * The bounds test is what keeps a click on the sky — or on the far side of the viewport where the
 * model is not — from selecting whatever body happens to be behind. The plane is infinite; the cut
 * face is not.
 */
export function hitsCap(
  origin: PointLike,
  direction: PointLike,
  plane: PlaneLike,
  box: BoxLike,
  epsilon: number,
): boolean {
  const hit = rayPlanePoint(origin, direction, plane);
  if (hit === null) return false;
  return insideBox(hit.point, box, epsilon);
}
