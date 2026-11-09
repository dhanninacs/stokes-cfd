/**
 * The arithmetic of dragging a section plane along its own axis (T-065d).
 *
 * ## The gesture
 *
 * The section's handle sits on the model, in the plane. Dragging it should slide the cut **along the
 * plane's normal** and nothing else — the axis is chosen in the popover, and a drag that also rotated
 * the plane would be doing two things at once.
 *
 * ## The projection
 *
 * A drag moves the pointer in two dimensions on the screen; the plane moves in one along its axis. The
 * bridge is the standard closest-point-between-two-lines: the pointer's ray and the line through the
 * handle along the axis. The parameter along that line where the two come closest is how far the user
 * meant to drag, and it is exact — no iteration, no frame-to-frame accumulation that drifts.
 *
 * ## Why it is its own module
 *
 * Plain numbers in and out, no three.js, no DOM, no scene. The rule in this log from the T-046 work is
 * that pure functions belong in modules with no browser imports, so they can be tested: a scene needs a
 * WebGL context, and `store.ts` transitively touches `window`, so neither can be imported by Vitest.
 * This is the arithmetic that decides where the cut goes, which is exactly the part worth testing.
 */

/** A point or direction in world space. */
export interface Vec3Like {
  x: number;
  y: number;
  z: number;
}

/** A line: a point on it and a **unit** direction. */
export interface Line {
  origin: Vec3Like;
  direction: Vec3Like;
}

/**
 * How far along `axis` the closest point to `ray` lies, relative to `axis.origin`.
 *
 * The standard formula for the closest points of two skew lines, with the axis' own parameter
 * returned. Returns `null` when the two are near-parallel: a ray that looks straight down the axis
 * cannot say how far along it the user dragged, and a number computed from a near-zero denominator
 * would be enormous and jump the cut across the model. Refusing lets the caller leave the plane where
 * it is, which is what a user sees as "that drag did nothing" rather than the cut flying away.
 *
 * `ray.direction` and `axis.direction` are both treated as unit vectors; the caller normalises them.
 */
export function axisDistance(ray: Line, axis: Line): number | null {
  const r = ray.direction;
  const a = axis.direction;
  const w0 = {
    x: ray.origin.x - axis.origin.x,
    y: ray.origin.y - axis.origin.y,
    z: ray.origin.z - axis.origin.z,
  };
  const dot = (u: Vec3Like, v: Vec3Like) => u.x * v.x + u.y * v.y + u.z * v.z;
  const rr = dot(r, r);
  const ra = dot(r, a);
  const aa = dot(a, a);
  const denominator = rr * aa - ra * ra;
  // `rr` and `aa` are 1 for unit inputs, so the denominator is `1 - cos²`, i.e. `sin²` of the angle
  // between them. 1e-9 is `sin` about 3e-5 — a tenth of a degree, below which the drag has no
  // meaningful direction along the axis.
  if (!(Math.abs(denominator) > 1e-9)) return null;
  const wr = dot(w0, r);
  const wa = dot(w0, a);
  // The axis' parameter of the closest approach, from the 2x2 system
  //   s·(r·r) − t·(r·a) = −(w0·r)
  //  −s·(r·a) + t·(a·a) =  (w0·a)
  // solved for `t`:
  //   t = ( (w0·a)·(r·r) − (w0·r)·(r·a) ) / ( (r·r)·(a·a) − (r·a)² )
  //
  // **The sign matters and is easy to flip.** The first version of this wrote the numerator the other
  // way round, which mirrored every drag: the cut moved against the pointer. The hand-computed cases
  // in `sectionDrag.test.ts` caught it, which is the reason they are worked out rather than read off
  // the implementation — a test that agreed with the code would have agreed with the bug.
  const numerator = wa * rr - wr * ra;
  const t = numerator / denominator;
  return Number.isFinite(t) ? t : null;
}

/**
 * The plane further along the drag, from the cut's own offset and the distance the pointer moved.
 *
 * `offset` is the plane's signed distance from the origin along its axis; `distance` is how far the
 * pointer's ray moved the handle along that same axis. Adding them is the whole rule, which is why
 * `invert` needs no special case: the axis direction the caller passes already carries the sign, so an
 * inverted section drags the way its arrow points rather than against it.
 */
export function draggedOffset(offset: number, distance: number): number {
  return Number.isFinite(distance) ? offset + distance : offset;
}
