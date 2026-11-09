/**
 * Screen-space hit tests for B-rep edges and vertices (ADR-0022 §15.3, T-057).
 *
 * The face and body picker is a raycaster against triangles, which cannot see an edge or a vertex:
 * a ray hits the shaded face between them, and a vertex may project outside every face's pixels.
 * §15.3 therefore asks for CPU screen-space tests over the part's *topology record* rather than a
 * ray: project each candidate polyline and vertex to pixels, and take the nearest one within a
 * tolerance measured in device pixels.
 *
 * This module is the arithmetic of that test and nothing else — plain numbers in, plain numbers
 * out, no three.js, no DOM, no scene. That is what lets it be tested directly (`topologypick.test.ts`)
 * instead of through a renderer, the same reason `measure.ts` and `scale.ts` are separate.
 *
 * **Tolerance is in device pixels.** §15.3 calls for a device-pixel-aware tolerance, so callers
 * pass the device pixel ratio and the tolerance is in those units: the same finger-sized target on
 * a HiDPI canvas and a normal one. The caller decides the value (a good default is
 * `DEFAULT_TOLERANCE_PX`); this module only applies it.
 *
 * **Occlusion is not decided here.** §15.3 says an occluded candidate is reached with Alt + wheel
 * rather than picked through a wall, and that hidden faces must not make a hidden edge pickable.
 * Depth ordering and visibility belong to the scene, which knows the camera and the hidden sets; the
 * caller passes the candidates it considers visible and this module returns them in nearest-first
 * order so the cycle has a defined sequence.
 */

/** A point in world space, in metres. */
export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/** A projected point in device pixels, plus its depth (three.js NDC z, -1 near to 1 far). */
export interface Projected {
  x: number;
  y: number;
  depth: number;
}

/**
 * Whether an edge is hidden by its own part's hidden faces (A2, §15.3).
 *
 * The review's rule, which I follow exactly: **an edge is hidden only when EVERY face it borders is
 * hidden.** An edge between a hidden face and a visible one stays pickable — it lies on the visible
 * face's boundary, and a user aiming at that boundary means it. A boundary edge has a single face,
 * so it is hidden when that one face is.
 *
 * This is deliberately not "drop the part's edges when any face is hidden", which would make a part's
 * visible edges unpickable the moment one face is hidden. `faces` is what the record's T-124a change
 * added; an edge with no faces (impossible for a version 2 payload) is treated as visible rather than
 * silently disappearing.
 */
export function hiddenByFaces(faces: readonly number[], hidden: ReadonlySet<number> | undefined): boolean {
  if (hidden === undefined || hidden.size === 0 || faces.length === 0) return false;
  for (const face of faces) if (!hidden.has(face)) return false;
  return true;
}

/** One edge candidate: its id, the part it belongs to, and its projected, clipped runs. */
export interface EdgeCandidate {
  part: string;
  id: number;
  /**
   * The edge's visible portions in device pixels, each a chain of at least two points, after
   * near-plane clipping (A1). **Runs, not one polyline**: an edge that leaves and re-enters the
   * frustum has two, and joining them would invent a segment the edge does not follow — which the
   * hit test would then treat as pickable. `depth` is the point's NDC z.
   */
  runs: readonly Projected[][];
}

/** One vertex candidate: its id, the part it belongs to, and its projected position. */
export interface VertexCandidate {
  part: string;
  id: number;
  point: Projected;
}

/** An edge or vertex that is within the tolerance, with how far away it was. */
export interface Hit<T> {
  candidate: T;
  /** Perpendicular distance in device pixels from the pointer to the candidate. */
  distance: number;
}

/** The default tolerance in device pixels: about a fingertip, and a comfortable line click. */
export const DEFAULT_TOLERANCE_PX = 8;

/**
 * Squared distance from `p` to the segment `a`–`b`, in the units the points are given in (here,
 * device pixels). Squared so the common case needs no square root; callers compare against a
 * squared tolerance. A degenerate segment (a and b coincide) is a point.
 */
export function distanceToSegmentSq(p: Projected, a: Projected, b: Projected): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) {
    const px = p.x - a.x;
    const py = p.y - a.y;
    return px * px + py * py;
  }
  // Project p onto the segment, clamped to its ends: the nearest point of a segment to p.
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq));
  const cx = a.x + t * dx;
  const cy = a.y + t * dy;
  const px = p.x - cx;
  const py = p.y - cy;
  return px * px + py * py;
}

/** The nearest point of a polyline to `p`, as squared distance in device pixels. */
/**
 * The nearest squared distance from `p` to any of `runs` (A1).
 *
 * Distances are taken run by run and never across them: the gap between two runs is not part of the
 * edge, so a pointer in that gap must not be measured against it.
 */
export function distanceToRunsSq(p: Projected, runs: readonly (readonly Projected[])[]): number {
  let best = Number.POSITIVE_INFINITY;
  for (const run of runs) {
    const d = distanceToPolylineSq(p, run);
    if (d < best) best = d;
  }
  return best;
}

/** Every run's points as one list, for a caller that does not need the run boundaries. */
export function flattenRuns(runs: readonly (readonly Projected[])[]): Projected[] {
  const out: Projected[] = [];
  for (const run of runs) out.push(...run);
  return out;
}

export function distanceToPolylineSq(p: Projected, points: readonly Projected[]): number {
  if (points.length === 0) return Number.POSITIVE_INFINITY;
  if (points.length === 1) {
    const dx = p.x - points[0].x;
    const dy = p.y - points[0].y;
    return dx * dx + dy * dy;
  }
  let best = Number.POSITIVE_INFINITY;
  for (let i = 1; i < points.length; i++) {
    const d = distanceToSegmentSq(p, points[i - 1], points[i]);
    if (d < best) best = d;
  }
  return best;
}

/**
 * The edges within `tolerance` pixels of `pointer`, nearest first.
 *
 * Ties are broken by depth (nearer first) and then by the candidate's stable id, as §15.3 asks, so
 * the result is deterministic whatever order the candidates arrived in — which matters because the
 * Alt + wheel cycle steps through this list and a re-ordered list would make the cycle jump.
 * `tolerance` is in device pixels; pass 0 to require an exact hit.
 */
export function hitEdges(
  pointer: Projected,
  candidates: readonly EdgeCandidate[],
  tolerance: number = DEFAULT_TOLERANCE_PX,
): Hit<EdgeCandidate>[] {
  const limit = Math.max(0, tolerance);
  const found: Hit<EdgeCandidate>[] = [];
  for (const candidate of candidates) {
    const sq = distanceToRunsSq(pointer, candidate.runs);
    if (sq <= limit * limit) found.push({ candidate, distance: Math.sqrt(sq) });
  }
  return found.sort(
    (a, b) =>
      compareHits(a, b, flattenRuns(a.candidate.runs), flattenRuns(b.candidate.runs)) ||
      byKey(a.candidate, b.candidate),
  );
}

/** The vertices within `tolerance` pixels of `pointer`, nearest first (same tie-break as edges). */
export function hitVertices(
  pointer: Projected,
  candidates: readonly VertexCandidate[],
  tolerance: number = DEFAULT_TOLERANCE_PX,
): Hit<VertexCandidate>[] {
  const limit = Math.max(0, tolerance);
  const found: Hit<VertexCandidate>[] = [];
  for (const candidate of candidates) {
    const dx = pointer.x - candidate.point.x;
    const dy = pointer.y - candidate.point.y;
    const sq = dx * dx + dy * dy;
    if (sq <= limit * limit) found.push({ candidate, distance: Math.sqrt(sq) });
  }
  const single = (p: Projected): readonly Projected[] => [p];
  return found.sort((a, b) => compareHits(a, b, single(a.candidate.point), single(b.candidate.point)) || byKey(a.candidate, b.candidate));
}

/** Distance first, then the shallowest depth: the two geometric tie-breaks of §15.3. */
function compareHits<T>(a: Hit<T>, b: Hit<T>, pa: readonly Projected[], pb: readonly Projected[]): number {
  if (a.distance !== b.distance) return a.distance - b.distance;
  const da = shallowest(pa);
  const db = shallowest(pb);
  return da === db ? 0 : da - db;
}

/** The last tie-break: a stable (part, id) order, so the Alt + wheel cycle never jumps. */
function byKey(a: { part: string; id: number }, b: { part: string; id: number }): number {
  if (a.part !== b.part) return a.part < b.part ? -1 : 1;
  return a.id - b.id;
}

/** The nearest (smallest NDC z) of a candidate's projected points. */
function shallowest(points: readonly Projected[]): number {
  let best = Number.POSITIVE_INFINITY;
  for (const point of points) if (point.depth < best) best = point.depth;
  return best;
}

/**
 * Where an Alt + wheel cycle lands (ADR-0022 6.7 / §15.3): the next candidate after `current` in a
 * list already ordered by the hit tests, wrapping around. `step` is normally ±1; a `current` absent
 * from the list starts the cycle at its first (or last, when stepping backwards) entry. An empty
 * list has nothing to cycle and gives null.
 */
export function cycleCandidate<T>(ordered: readonly T[], current: T | null, step: number): T | null {
  if (ordered.length === 0) return null;
  const at = current === null ? -1 : ordered.indexOf(current);
  if (at < 0) return step >= 0 ? ordered[0] : ordered[ordered.length - 1];
  const next = (at + (step >= 0 ? 1 : -1) + ordered.length) % ordered.length;
  return ordered[next];
}
