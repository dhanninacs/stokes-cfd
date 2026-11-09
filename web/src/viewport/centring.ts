/**
 * Where to move the image so the projected model is centred on the free area.
 *
 * **The closed form** (Claude's 2026-10-19 note, after three failed attempts of mine): with a
 * PERSPECTIVE camera, shifting the principal point translates every projected point by the SAME pixel
 * vector, exactly, with no change of scale. So the correction is not an iteration - it is one
 * subtraction, provided the measurement is taken with **no offset already in place**.
 *
 * **My three failures, and what each taught**: a nudge scaled by `span` diverged (span is what the view
 * covers, not what a pixel spans); a measured per-unit scale still compounded because it fed its own
 * output back; shifting the principal point was the right KIND and left a residual only because it was
 * measured with the previous offset still applied. Clear first, measure, subtract once.
 *
 * **Pure, and tested without a renderer** - the property is "after applying this, the projected centre
 * is the free centre", which is checkable in arithmetic and which a screenshot can only suggest.
 */
export interface CanvasPoint {
  x: number;
  y: number;
}

export interface FreeRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * The pixel vector the image must move by, so the projected box centres on the free area.
 *
 * `project` maps a 3D point to canvas pixels **with no view offset applied**; the caller supplies it,
 * which is what keeps this function free of three.js and testable with plain arithmetic.
 */
export function centringShift(
  points: readonly unknown[],
  project: (point: unknown) => CanvasPoint,
  free: FreeRect,
): { dx: number; dy: number } {
  if (points.length === 0) return { dx: 0, dy: 0 };
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    const p = project(point);
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  }
  const centreX = (minX + maxX) / 2;
  const centreY = (minY + maxY) / 2;
  const freeX = free.x + free.width / 2;
  const freeY = free.y + free.height / 2;
  // **The image must MOVE by this vector**, which `setViewOffset` does through its negated origin;
  // `asViewOffsetOrigin` below is the one place that translation happens.
  return { dx: freeX - centreX, dy: freeY - centreY };
}

/**
 * The `setViewOffset` origin for a shift, for a canvas `width` x `height`.
 *
 * **three.js moves the image content by MINUS the origin**, which is the sign that cost me a round of
 * measuring. Keeping it in its own function means the sign is tested once rather than remembered each
 * time a camera is touched.
 */
export function asViewOffsetOrigin(dx: number, dy: number): { x: number; y: number } {
  return { x: -dx, y: -dy };
}
