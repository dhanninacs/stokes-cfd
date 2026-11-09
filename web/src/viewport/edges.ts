/**
 * Feature edges: the lines along which neighbouring triangles meet at more than an angle, drawn over
 * the surface in the "shaded with edges" view mode.
 *
 * A part's edges are found once (`featureEdges`) and kept as a flat array of segment end points. Below
 * the batching threshold every part draws them as its own line object (`edgeLines`). Above it
 * (ADR-0019) one `MergedEdges` draws the edges of all shown parts, so the mode costs one draw call
 * rather than one per part.
 */

import * as THREE from "three";
import type { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";

/** EdgesGeometry rounds positions to this many decimals to tell which corners are the same point. */
const PRECISION = 1e4;

/**
 * The edges of `geometry` between triangles whose normals differ by more than `angle` degrees, and
 * the open edges: six floats per segment.
 *
 * Exactly what `THREE.EdgesGeometry(geometry, angle)` finds, segment for segment, in the same order
 * and with the same arithmetic, so the two can stand in for each other. That class builds nine strings
 * per triangle to match corners; this rounds each vertex once, to the same key, and matches edges by
 * integer, which is several times faster: a scene of thousands of parts finds its edges in a fraction
 * of the time. Geometry it cannot read directly (interleaved or normalised positions) goes to
 * EdgesGeometry itself.
 */
export function featureEdges(geometry: THREE.BufferGeometry, angle: number): Float32Array {
  const position = geometry.getAttribute("position");
  if (!(position instanceof THREE.BufferAttribute) || position.normalized || position.itemSize !== 3) {
    const found = new THREE.EdgesGeometry(geometry, angle);
    const positions = found.getAttribute("position").array as Float32Array;
    found.dispose();
    return positions;
  }
  const p = position.array;
  const index = geometry.getIndex()?.array ?? null;
  const count = index ? index.length : position.count;
  const thresholdDot = Math.cos(THREE.MathUtils.DEG2RAD * angle);

  // Every vertex to the first vertex at its rounded position: the corner key EdgesGeometry uses.
  const same = new Int32Array(position.count);
  const firsts = new Map<string, number>();
  for (let v = 0; v < position.count; v++) {
    const key = `${Math.round(p[3 * v] * PRECISION)},${Math.round(p[3 * v + 1] * PRECISION)},${Math.round(p[3 * v + 2] * PRECISION)}`;
    let id = firsts.get(key);
    if (id === undefined) {
      id = firsts.size;
      firsts.set(key, id);
    }
    same[v] = id;
  }
  const span = firsts.size;

  // An edge seen once waits here, by (from, to) of its corner ids, with its triangle's normal; -1 once matched.
  const open = new Map<number, number>();
  const normals: number[] = [];
  const ends: number[] = [];
  const out: number[] = [];
  const corner = [0, 0, 0];
  const id = [0, 0, 0];
  for (let i = 0; i < count; i += 3) {
    corner[0] = index ? index[i] : i;
    corner[1] = index ? index[i + 1] : i + 1;
    corner[2] = index ? index[i + 2] : i + 2;
    id[0] = same[corner[0]];
    id[1] = same[corner[1]];
    id[2] = same[corner[2]];
    if (id[0] === id[1] || id[1] === id[2] || id[2] === id[0]) continue;
    // The normal as THREE.Triangle.getNormal works it out: (c - b) x (a - b), normalised.
    const a = 3 * corner[0];
    const b = 3 * corner[1];
    const c = 3 * corner[2];
    const ux = p[c] - p[b];
    const uy = p[c + 1] - p[b + 1];
    const uz = p[c + 2] - p[b + 2];
    const vx = p[a] - p[b];
    const vy = p[a + 1] - p[b + 1];
    const vz = p[a + 2] - p[b + 2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const lengthSq = nx * nx + ny * ny + nz * nz;
    if (lengthSq > 0) {
      const scale = 1 / Math.sqrt(lengthSq);
      nx *= scale;
      ny *= scale;
      nz *= scale;
    } else {
      nx = ny = nz = 0;
    }
    for (let j = 0; j < 3; j++) {
      const k = j === 2 ? 0 : j + 1;
      const twin = open.get(id[k] * span + id[j]);
      if (twin !== undefined && twin >= 0) {
        // The same edge the other way round: the two triangles meet here.
        if (nx * normals[3 * twin] + ny * normals[3 * twin + 1] + nz * normals[3 * twin + 2] <= thresholdDot) {
          const from = 3 * corner[j];
          const to = 3 * corner[k];
          out.push(p[from], p[from + 1], p[from + 2], p[to], p[to + 1], p[to + 2]);
        }
        open.set(id[k] * span + id[j], -1);
      } else {
        const key = id[j] * span + id[k];
        if (open.has(key)) continue;
        open.set(key, normals.length / 3);
        normals.push(nx, ny, nz);
        ends.push(corner[j], corner[k]);
      }
    }
  }
  // Edges no other triangle shares: the open boundary.
  for (const slot of open.values()) {
    if (slot < 0) continue;
    const from = 3 * ends[2 * slot];
    const to = 3 * ends[2 * slot + 1];
    out.push(p[from], p[from + 1], p[from + 2], p[to], p[to + 1], p[to + 2]);
  }
  return new Float32Array(out);
}

/** One part's edge lines, hidden until the view mode shows them; null when it has no edges. */
export function edgeLines(segments: Float32Array, material: LineMaterial, name: string): LineSegments2 | null {
  if (segments.length === 0) return null;
  const lines = new LineSegments2(new LineSegmentsGeometry().setPositions(segments), material);
  lines.name = name;
  lines.renderOrder = 1;
  lines.visible = false;
  return lines;
}

/** The edges of many parts as one line object. */
export class MergedEdges {
  /** Add this to the scene; it is hidden while it has nothing to draw. */
  readonly object: LineSegments2;

  constructor(material: LineMaterial) {
    this.object = new LineSegments2(new LineSegmentsGeometry(), material);
    this.object.name = "edges:merged";
    this.object.renderOrder = 1;
    this.object.visible = false;
  }

  /** Draws exactly these segments, the parts' arrays one after another. */
  set(chunks: readonly Float32Array[]): void {
    let length = 0;
    for (const chunk of chunks) length += chunk.length;
    if (length === 0) {
      this.clear();
      return;
    }
    const merged = new Float32Array(length);
    let at = 0;
    for (const chunk of chunks) {
      merged.set(chunk, at);
      at += chunk.length;
    }
    const previous = this.object.geometry;
    this.object.geometry = new LineSegmentsGeometry().setPositions(merged);
    previous.dispose();
    this.object.visible = true;
  }

  hide(): void {
    this.object.visible = false;
  }

  /** Hides the lines and frees what they drew. */
  clear(): void {
    this.object.visible = false;
    const previous = this.object.geometry;
    this.object.geometry = new LineSegmentsGeometry();
    previous.dispose();
  }

  dispose(): void {
    this.object.geometry.dispose();
  }
}
