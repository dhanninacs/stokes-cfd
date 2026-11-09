/**
 * Contours of a field on a triangulated surface (a slice of the results), as pure functions on arrays.
 *
 * * `contourSegments`: the lines where the field equals each of a set of levels, by marching triangles: a
 *   vertex is "above" a level when its value is at or over it, a triangle with both kinds crosses the level on
 *   the two edges that join them, and the segment joins the two crossings (linear in the value along an edge).
 *   The rule is the same for every triangle, so two triangles that share an edge agree on where it is
 *   crossed and the lines close without gaps; a vertex exactly on a level counts as above and makes no
 *   zero-length segment. A triangle with a value that is not a number draws nothing.
 * * `bandSurface`: the filled contours: each triangle cut along the band boundaries into pieces that lie
 *   in one band each, every piece with its band's colour on all its vertices, so the bands are crisp edges and
 *   not the smooth ramp that a colour per vertex would blend across a triangle. A band is
 *   `floor((value - min) / (max - min) * bands)`, clamped to the first and last (a value outside the range
 *   takes the colour of its end, as the smooth map does).
 *
 * The levels and the bands are the ones `panels/sliceModel.ts` `contourLevels` names: N bands have N - 1 lines
 * between them, so the lines are the edges of the bands.
 *
 * Import rule: no React, no store, no DOM, no three. This loads in Node.
 */

/** The segments of `contourSegments`: six floats each, and the index (into the levels) of the level each is at. */
export interface ContourSegments {
  positions: Float32Array;
  level: Uint16Array;
}

/** A growing list of numbers, for output whose size is not known before the walk. */
class Grow {
  private data: Float32Array;
  length = 0;
  constructor(capacity = 256) {
    this.data = new Float32Array(capacity);
  }
  push(a: number, b: number, c: number): void {
    if (this.length + 3 > this.data.length) {
      const next = new Float32Array(this.data.length * 2);
      next.set(this.data);
      this.data = next;
    }
    this.data[this.length++] = a;
    this.data[this.length++] = b;
    this.data[this.length++] = c;
  }
  done(): Float32Array {
    return this.data.slice(0, this.length);
  }
}

/** The first index of `sorted` whose value is at least `x` (the length when none is). */
function lowerBound(sorted: ArrayLike<number>, x: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (sorted[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * The line segments of the contours of `values` (one per vertex of `positions`, three floats each) over the
 * triangles `indices` at `levels`, which must be in ascending order. Segments shorter than `1e-12` are dropped.
 */
export function contourSegments(positions: ArrayLike<number>, indices: ArrayLike<number>, values: ArrayLike<number>, levels: ArrayLike<number>): ContourSegments {
  const out = new Grow(1024);
  const which: number[] = [];
  const vertices = Math.floor(positions.length / 3);
  for (let t = 0; t + 2 < indices.length; t += 3) {
    const ia = indices[t];
    const ib = indices[t + 1];
    const ic = indices[t + 2];
    if (ia >= vertices || ib >= vertices || ic >= vertices || ia >= values.length || ib >= values.length || ic >= values.length) continue;
    const va = values[ia];
    const vb = values[ib];
    const vc = values[ic];
    if (Number.isNaN(va) || Number.isNaN(vb) || Number.isNaN(vc)) continue;
    const low = Math.min(va, vb, vc);
    const high = Math.max(va, vb, vc);
    for (let k = lowerBound(levels, low); k < levels.length && levels[k] <= high; k++) {
      const level = levels[k];
      // A vertex is above when its value is at or over the level; a triangle all on one side is not crossed.
      const above = [va >= level, vb >= level, vc >= level];
      if (above[0] === above[1] && above[1] === above[2]) continue;
      const ends: number[] = [];
      const corner = [ia, ib, ic];
      const value = [va, vb, vc];
      for (let e = 0; e < 3; e++) {
        const f = e;
        const g = (e + 1) % 3;
        if (above[f] === above[g]) continue;
        const along = (level - value[f]) / (value[g] - value[f]);
        const p = corner[f] * 3;
        const q = corner[g] * 3;
        ends.push(
          positions[p] + (positions[q] - positions[p]) * along,
          positions[p + 1] + (positions[q + 1] - positions[p + 1]) * along,
          positions[p + 2] + (positions[q + 2] - positions[p + 2]) * along,
        );
      }
      if (ends.length !== 6) continue;
      if (Math.hypot(ends[0] - ends[3], ends[1] - ends[4], ends[2] - ends[5]) < 1e-12) continue;
      out.push(ends[0], ends[1], ends[2]);
      out.push(ends[3], ends[4], ends[5]);
      which.push(k);
    }
  }
  return { positions: out.done(), level: Uint16Array.from(which) };
}

/**
 * The line buffers of segments drawn on a plane: each segment twice, lifted `lift` metres along the plane's
 * normal and against it, so the line is in front of the surface it follows whichever side the camera is on
 * (the surface hides the copy that is behind it). `colours` is the sRGB bytes for each end, from `colourOf(level)`,
 * or null for one colour.
 */
export function liftedLines(
  segments: ContourSegments,
  normal: readonly [number, number, number],
  lift: number,
  colourOf: ((level: number) => readonly [number, number, number]) | null,
): { positions: Float32Array; colours: Uint8Array | null } {
  const n = segments.level.length;
  const length = Math.hypot(normal[0], normal[1], normal[2]);
  const d = length > 0 ? [(normal[0] / length) * lift, (normal[1] / length) * lift, (normal[2] / length) * lift] : [0, 0, 0];
  const positions = new Float32Array(n * 12);
  const colours = colourOf === null ? null : new Uint8Array(n * 12);
  for (let i = 0; i < n; i++) {
    for (let side = 0; side < 2; side++) {
      const sign = side === 0 ? 1 : -1;
      const at = i * 12 + side * 6;
      for (let end = 0; end < 2; end++) {
        for (let axis = 0; axis < 3; axis++) positions[at + end * 3 + axis] = segments.positions[i * 6 + end * 3 + axis] + sign * d[axis];
      }
      if (colours !== null && colourOf !== null) {
        const rgb = colourOf(segments.level[i]);
        for (let end = 0; end < 2; end++) for (let axis = 0; axis < 3; axis++) colours[at + end * 3 + axis] = rgb[axis];
      }
    }
  }
  return { positions, colours };
}

/** A surface cut into bands: the geometry of the pieces and an RGBA8 tint per vertex. */
export interface BandedSurface {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  /** RGBA8 per vertex: the colour of the band the piece lies in; alpha 0 on a triangle that has no value. */
  tint: Uint8Array;
}

/** One vertex of a piece being cut: position, normal and value. */
interface Vertex {
  p: [number, number, number];
  n: [number, number, number];
  v: number;
}

/** The band of a value: `floor(fraction * bands)`, clamped to 0..bands-1. A range with no width puts every value in the middle band. */
export function bandOf(value: number, min: number, max: number, bands: number): number {
  if (!(max > min)) return bands >> 1;
  const k = Math.floor(((value - min) / (max - min)) * bands);
  return Math.min(bands - 1, Math.max(0, k));
}

/** The piece of `polygon` on one side of `bound` (value at or over it with `keepAbove`, else at or under). */
function clipValue(polygon: Vertex[], bound: number, keepAbove: boolean): Vertex[] {
  const inside = (v: Vertex) => (keepAbove ? v.v >= bound : v.v <= bound);
  const out: Vertex[] = [];
  for (let i = 0; i < polygon.length; i++) {
    const current = polygon[i];
    const previous = polygon[(i + polygon.length - 1) % polygon.length];
    const crossing = (): Vertex => {
      const along = (bound - previous.v) / (current.v - previous.v);
      return {
        p: [previous.p[0] + (current.p[0] - previous.p[0]) * along, previous.p[1] + (current.p[1] - previous.p[1]) * along, previous.p[2] + (current.p[2] - previous.p[2]) * along],
        n: [previous.n[0] + (current.n[0] - previous.n[0]) * along, previous.n[1] + (current.n[1] - previous.n[1]) * along, previous.n[2] + (current.n[2] - previous.n[2]) * along],
        v: bound,
      };
    };
    if (inside(current)) {
      if (!inside(previous)) out.push(crossing());
      out.push(current);
    } else if (inside(previous)) {
      out.push(crossing());
    }
  }
  return out;
}

/**
 * The surface cut along the `bands` band boundaries of `[min, max]`. `colourOf(band, bands)` is the colour of a
 * band, sRGB bytes. A triangle that is in one band is kept whole; a triangle that spans several becomes one
 * piece per band it touches.
 */
export function bandSurface(
  positions: ArrayLike<number>,
  normals: ArrayLike<number>,
  indices: ArrayLike<number>,
  values: ArrayLike<number>,
  range: { min: number; max: number },
  bands: number,
  colourOf: (band: number, bands: number) => readonly [number, number, number],
): BandedSurface {
  const outPositions = new Grow(1024);
  const outNormals = new Grow(1024);
  const outIndices: number[] = [];
  const tint: number[] = [];
  let count = 0;
  const vertices = Math.floor(positions.length / 3);
  const step = (range.max - range.min) / bands;

  const emit = (polygon: readonly Vertex[], rgba: readonly [number, number, number, number]): void => {
    const first = count;
    for (const v of polygon) {
      outPositions.push(v.p[0], v.p[1], v.p[2]);
      outNormals.push(v.n[0], v.n[1], v.n[2]);
      tint.push(rgba[0], rgba[1], rgba[2], rgba[3]);
      count++;
    }
    for (let i = 1; i + 1 < polygon.length; i++) outIndices.push(first, first + i, first + i + 1);
  };

  for (let t = 0; t + 2 < indices.length; t += 3) {
    const corners = [indices[t], indices[t + 1], indices[t + 2]];
    if (corners.some((c) => c >= vertices || c >= values.length)) continue;
    const triangle: Vertex[] = corners.map((c) => ({
      p: [positions[c * 3], positions[c * 3 + 1], positions[c * 3 + 2]],
      n: [normals[c * 3] ?? 0, normals[c * 3 + 1] ?? 0, normals[c * 3 + 2] ?? 0],
      v: values[c],
    }));
    if (triangle.some((v) => Number.isNaN(v.v))) {
      emit(triangle, [0, 0, 0, 0]);
      continue;
    }
    const first = bandOf(Math.min(triangle[0].v, triangle[1].v, triangle[2].v), range.min, range.max, bands);
    const last = bandOf(Math.max(triangle[0].v, triangle[1].v, triangle[2].v), range.min, range.max, bands);
    for (let k = first; k <= last; k++) {
      let piece = triangle;
      // The first band has no lower boundary and the last no upper one: a value past the range is in its end band.
      if (k > 0) piece = clipValue(piece, range.min + step * k, true);
      if (k < bands - 1 && piece.length >= 3) piece = clipValue(piece, range.min + step * (k + 1), false);
      if (piece.length < 3) continue;
      const rgb = colourOf(k, bands);
      emit(piece, [rgb[0], rgb[1], rgb[2], 255]);
    }
  }
  return { positions: outPositions.done(), normals: outNormals.done(), indices: Uint32Array.from(outIndices), tint: Uint8Array.from(tint) };
}
