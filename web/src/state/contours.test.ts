/**
 * Marching triangles and the band cut: where the contour lines of a field lie on a triangulated slice, that they
 * close along shared edges, that a banded surface keeps the area of the surface and every piece lies in one band
 * with that band's colour.
 */
import { describe, expect, it } from "vitest";
import { bandColor, colorAt, levelColor } from "./colormap";
import { bandOf, bandSurface, contourSegments, liftedLines } from "./contours";

/** A (n+1) x (n+1) grid of vertices over [0,n] x [0,n] at z = 0, two triangles per cell. */
function grid(n: number): { positions: Float32Array; normals: Float32Array; indices: Uint32Array } {
  const positions: number[] = [];
  const normals: number[] = [];
  for (let j = 0; j <= n; j++) {
    for (let i = 0; i <= n; i++) {
      positions.push(i, j, 0);
      normals.push(0, 0, 1);
    }
  }
  const indices: number[] = [];
  const at = (i: number, j: number) => j * (n + 1) + i;
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      indices.push(at(i, j), at(i + 1, j), at(i + 1, j + 1), at(i, j), at(i + 1, j + 1), at(i, j + 1));
    }
  }
  return { positions: Float32Array.from(positions), normals: Float32Array.from(normals), indices: Uint32Array.from(indices) };
}

function segmentsOf(out: { positions: Float32Array }): number[][] {
  const list: number[][] = [];
  for (let i = 0; i + 5 < out.positions.length; i += 6) list.push(Array.from(out.positions.subarray(i, i + 6)));
  return list;
}

const TRIANGLE = {
  positions: Float32Array.from([0, 0, 0, 1, 0, 0, 0, 1, 0]),
  normals: Float32Array.from([0, 0, 1, 0, 0, 1, 0, 0, 1]),
  indices: Uint32Array.from([0, 1, 2]),
};

describe("contourSegments", () => {
  it("joins the two crossings of a level, linear along each edge", () => {
    const out = contourSegments(TRIANGLE.positions, TRIANGLE.indices, [0, 1, 2], [0.5]);
    const [segment] = segmentsOf(out);
    expect(segmentsOf(out)).toHaveLength(1);
    // Edge a-b crosses 0.5 at 0.5 along it; edge c-a at 0.25 of the way from a... a = 0, c = 2: (0.5 - 0)/(2 - 0) = 0.25.
    const ends = [segment.slice(0, 3), segment.slice(3, 6)].sort((p, q) => p[0] - q[0]);
    expect(ends[0]).toEqual([0, 0.25, 0]);
    expect(ends[1]).toEqual([0.5, 0, 0]);
    expect(Array.from(out.level)).toEqual([0]);
  });

  it("draws a line at each level a triangle spans, and none outside its values", () => {
    const out = contourSegments(TRIANGLE.positions, TRIANGLE.indices, [0, 1, 2], [-1, 0.5, 1.5, 3]);
    expect(Array.from(out.level)).toEqual([1, 2]);
  });

  it("a plane of x on a grid: every line is vertical at its level and as long as the grid is high", () => {
    const g = grid(3);
    const x = Float32Array.from({ length: g.positions.length / 3 }, (_, i) => g.positions[i * 3]);
    const levels = [0.5, 1.5, 2.5];
    const out = contourSegments(g.positions, g.indices, x, levels);
    const perLevel = [0, 0, 0];
    for (let i = 0; i < out.level.length; i++) {
      const s = out.positions.subarray(i * 6, i * 6 + 6);
      expect(s[0]).toBeCloseTo(levels[out.level[i]], 6);
      expect(s[3]).toBeCloseTo(levels[out.level[i]], 6);
      perLevel[out.level[i]] += Math.hypot(s[3] - s[0], s[4] - s[1], s[5] - s[2]);
    }
    for (const length of perLevel) expect(length).toBeCloseTo(3, 5);
  });

  it("the lines close: every crossing point is shared by exactly two segments, or lies on the border", () => {
    const g = grid(6);
    // A bowl: the contours are closed curves.
    const v = Float32Array.from({ length: g.positions.length / 3 }, (_, i) => Math.hypot(g.positions[i * 3] - 3, g.positions[i * 3 + 1] - 3));
    const out = contourSegments(g.positions, g.indices, v, [1.2, 2.2]);
    const ends = new Map<string, number>();
    for (let i = 0; i < out.level.length; i++) {
      for (const at of [0, 3]) {
        const key = Array.from(out.positions.subarray(i * 6 + at, i * 6 + at + 3)).map((n) => n.toFixed(5)).join(",");
        ends.set(key, (ends.get(key) ?? 0) + 1);
      }
    }
    expect(ends.size).toBeGreaterThan(8);
    for (const n of ends.values()) expect(n).toBe(2);
  });

  it("a value exactly on a level counts as above and makes no zero-length segment", () => {
    // Only a is at the level and the others are below: both crossings are a itself.
    expect(contourSegments(TRIANGLE.positions, TRIANGLE.indices, [1, 0, 0], [1]).level).toHaveLength(0);
    // All on the level: not crossed.
    expect(contourSegments(TRIANGLE.positions, TRIANGLE.indices, [1, 1, 1], [1]).level).toHaveLength(0);
    // Two on the level and one below: the line is the edge between them.
    const edge = contourSegments(TRIANGLE.positions, TRIANGLE.indices, [0, 1, 1], [1]);
    expect(edge.level).toHaveLength(1);
  });

  it("skips a triangle with a value that is not a number, a vertex that is not there, and an empty mesh", () => {
    expect(contourSegments(TRIANGLE.positions, TRIANGLE.indices, [0, Number.NaN, 2], [0.5]).level).toHaveLength(0);
    expect(contourSegments(TRIANGLE.positions, Uint32Array.from([0, 1, 7]), [0, 1, 2], [0.5]).level).toHaveLength(0);
    expect(contourSegments(new Float32Array(0), new Uint32Array(0), [], [0.5]).positions).toHaveLength(0);
    expect(contourSegments(TRIANGLE.positions, TRIANGLE.indices, [0, 1, 2], []).level).toHaveLength(0);
  });
});

describe("liftedLines", () => {
  it("puts each segment in front of and behind the plane, so the surface hides the far copy", () => {
    const segments = { positions: Float32Array.from([0, 0, 0, 1, 0, 0]), level: Uint16Array.from([0]) };
    const { positions, colours } = liftedLines(segments, [0, 0, 2], 0.01, null);
    expect(colours).toBeNull();
    expect(Array.from(positions)).toEqual([0, 0, 0.01, 1, 0, 0.01, 0, 0, -0.01, 1, 0, -0.01].map((v) => Math.fround(v)));
  });

  it("colours both copies from the level, three bytes per end", () => {
    const segments = { positions: Float32Array.from([0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0]), level: Uint16Array.from([0, 3]) };
    const { colours } = liftedLines(segments, [0, 0, 1], 0.01, (level) => [level, level + 1, level + 2]);
    expect(colours).toHaveLength(24);
    expect(Array.from(colours!.subarray(0, 3))).toEqual([0, 1, 2]);
    expect(Array.from(colours!.subarray(9, 12))).toEqual([0, 1, 2]);
    expect(Array.from(colours!.subarray(12, 15))).toEqual([3, 4, 5]);
  });
});

describe("bandOf", () => {
  it("is floor of the fraction times the bands, clamped to the end bands", () => {
    expect(bandOf(0, 0, 10, 5)).toBe(0);
    expect(bandOf(1.99, 0, 10, 5)).toBe(0);
    expect(bandOf(2, 0, 10, 5)).toBe(1);
    expect(bandOf(10, 0, 10, 5)).toBe(4);
    expect(bandOf(-3, 0, 10, 5)).toBe(0);
    expect(bandOf(99, 0, 10, 5)).toBe(4);
  });

  it("puts every value of a range with no width in the middle band", () => {
    expect(bandOf(3, 3, 3, 10)).toBe(5);
  });
});

function area(positions: Float32Array, indices: Uint32Array): number {
  let sum = 0;
  for (let t = 0; t < indices.length; t += 3) {
    const [a, b, c] = [indices[t] * 3, indices[t + 1] * 3, indices[t + 2] * 3];
    const u = [positions[b] - positions[a], positions[b + 1] - positions[a + 1], positions[b + 2] - positions[a + 2]];
    const v = [positions[c] - positions[a], positions[c + 1] - positions[a + 1], positions[c + 2] - positions[a + 2]];
    sum += 0.5 * Math.hypot(u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]);
  }
  return sum;
}

describe("bandSurface", () => {
  const range = { min: 0, max: 4 };

  it("cuts a ramp into pieces that lie in one band each, with that band's colour, and keeps the area", () => {
    const g = grid(4);
    const x = Float32Array.from({ length: g.positions.length / 3 }, (_, i) => g.positions[i * 3]);
    // 8 bands over 0..4: a band is half a unit wide; cells are one unit wide, so each is cut in two.
    const out = bandSurface(g.positions, g.normals, g.indices, x, range, 8, bandColor);
    expect(area(out.positions, out.indices)).toBeCloseTo(16, 4);
    expect(out.tint).toHaveLength((out.positions.length / 3) * 4);
    expect(out.normals).toHaveLength(out.positions.length);
    for (let t = 0; t < out.indices.length; t += 3) {
      const xs = [0, 1, 2].map((k) => out.positions[out.indices[t + k] * 3]);
      const band = Math.floor((Math.min(...xs) + Math.max(...xs)) / 2 / 0.5);
      expect(Math.min(...xs)).toBeGreaterThanOrEqual(band * 0.5 - 1e-5);
      expect(Math.max(...xs)).toBeLessThanOrEqual((band + 1) * 0.5 + 1e-5);
      for (let k = 0; k < 3; k++) {
        const v = out.indices[t + k] * 4;
        expect(Array.from(out.tint.subarray(v, v + 4))).toEqual([...bandColor(band, 8), 255]);
      }
    }
  });

  it("keeps a triangle that is in one band whole", () => {
    const out = bandSurface(TRIANGLE.positions, TRIANGLE.normals, TRIANGLE.indices, [1.1, 1.2, 1.3], range, 4, bandColor);
    expect(out.indices).toHaveLength(3);
    expect(Array.from(out.positions)).toEqual(Array.from(TRIANGLE.positions));
    expect(Array.from(out.tint.subarray(0, 4))).toEqual([...bandColor(1, 4), 255]);
  });

  it("interpolates the normals of the new vertices", () => {
    const normals = Float32Array.from([0, 0, 1, 0, 1, 0, 1, 0, 0]);
    const out = bandSurface(TRIANGLE.positions, normals, TRIANGLE.indices, [0, 2, 4], range, 2, bandColor);
    // Every new vertex has a unit-ish normal that is a blend of the three; at least one vertex is not a corner.
    expect(out.normals.length).toBeGreaterThan(9);
    for (let i = 0; i < out.normals.length; i += 3) {
      const n = Math.hypot(out.normals[i], out.normals[i + 1], out.normals[i + 2]);
      expect(n).toBeGreaterThan(0.6);
      expect(n).toBeLessThanOrEqual(1.0001);
    }
  });

  it("gives a value past the range the colour of its end band", () => {
    const out = bandSurface(TRIANGLE.positions, TRIANGLE.normals, TRIANGLE.indices, [-5, -4, -3], range, 4, bandColor);
    expect(out.indices).toHaveLength(3);
    expect(Array.from(out.tint.subarray(0, 4))).toEqual([...bandColor(0, 4), 255]);
    const high = bandSurface(TRIANGLE.positions, TRIANGLE.normals, TRIANGLE.indices, [9, 9, 10], range, 4, bandColor);
    expect(Array.from(high.tint.subarray(0, 4))).toEqual([...bandColor(3, 4), 255]);
  });

  it("a triangle with a value that is not a number keeps its own colour (alpha 0) and is not cut", () => {
    const out = bandSurface(TRIANGLE.positions, TRIANGLE.normals, TRIANGLE.indices, [0, Number.NaN, 3], range, 4, bandColor);
    expect(out.indices).toHaveLength(3);
    expect(Array.from(out.tint.subarray(0, 4))).toEqual([0, 0, 0, 0]);
  });

  it("a range with no width is one band", () => {
    const out = bandSurface(TRIANGLE.positions, TRIANGLE.normals, TRIANGLE.indices, [1, 1, 1], { min: 1, max: 1 }, 10, bandColor);
    expect(out.indices).toHaveLength(3);
    expect(Array.from(out.tint.subarray(0, 4))).toEqual([...bandColor(5, 10), 255]);
  });
});

describe("band and level colours", () => {
  it("a band is the map at its middle, a line the map at the boundary it draws", () => {
    expect(bandColor(0, 10)).toEqual(colorAt(0.05).map((c) => Math.round(c)));
    expect(levelColor(0, 10)).toEqual(colorAt(0.1).map((c) => Math.round(c)));
    expect(levelColor(8, 10)).toEqual(colorAt(0.9).map((c) => Math.round(c)));
  });
});
