/**
 * The plot-over-line's own line in the viewport (R5): a thin segment between the two clicked points with a small
 * marker at each end, in the accent colour, drawn over the model (`depthTest` off) because the line lies on the
 * slice's plane, inside the body.
 *
 * **The geometry is a pure function** (`lineEnds`), so what is drawn can be tested without a renderer; the class
 * owns the objects and their lifetime. Widths and sizes are in pixels, so a 10 mm line and a 10 m one both read; the
 * material's `resolution` follows a resize (`setResolution`) and its colour a theme (`setAccent`).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";

/** The line's width and each end marker's size, CSS pixels: thinner than the measurement's, which it must not be mistaken for. */
export const LINE_WIDTH = 1.5;
export const LINE_DOT = 6;
/** After the parts and their edges (1), the streamlines and the measurement (3). */
const LINE_ORDER = 4;

/** The segment's six floats and the two ends' six, or null when there is no line to draw. */
export function lineEnds(a: readonly number[] | null, b: readonly number[] | null): { segment: Float32Array; ends: Float32Array } | null {
  if (a === null || b === null || a.length !== 3 || b.length !== 3) return null;
  if (![...a, ...b].every((v) => Number.isFinite(v))) return null;
  if (a[0] === b[0] && a[1] === b[1] && a[2] === b[2]) return null;
  const segment = new Float32Array([a[0]!, a[1]!, a[2]!, b[0]!, b[1]!, b[2]!]);
  return { segment, ends: new Float32Array(segment) };
}

export class LineLayer {
  private readonly material: LineMaterial;
  private readonly dotMaterial: THREE.PointsMaterial;
  private line: LineSegments2 | null = null;
  private dots: THREE.Points | null = null;

  constructor(
    private readonly scene: THREE.Scene,
    accent: THREE.ColorRepresentation,
    width: number,
    height: number,
  ) {
    this.material = new LineMaterial({ color: new THREE.Color(accent), linewidth: LINE_WIDTH, transparent: true, opacity: 0.95, depthTest: false, depthWrite: false, toneMapped: false });
    this.material.resolution.set(Math.max(1, width), Math.max(1, height));
    this.dotMaterial = new THREE.PointsMaterial({ color: new THREE.Color(accent), size: LINE_DOT, sizeAttenuation: false, transparent: true, depthTest: false, depthWrite: false, toneMapped: false });
  }

  /** The objects, for a frame (an export) that hides what is not the model. */
  get objects(): THREE.Object3D[] {
    return [this.line, this.dots].filter((o): o is LineSegments2 | THREE.Points => o !== null);
  }

  /** Draws the line between two world points (metres), or takes it away when either is null. */
  set(a: readonly number[] | null, b: readonly number[] | null): void {
    this.dispose(false);
    // The ends show with one point too (the first click), the segment only with both.
    const drawn = lineEnds(a, b);
    if (drawn !== null) {
      const line = new LineSegments2(new LineSegmentsGeometry().setPositions(drawn.segment), this.material);
      line.name = "plot-line";
      line.renderOrder = LINE_ORDER;
      line.frustumCulled = false;
      this.scene.add(line);
      this.line = line;
    }
    const ends: number[] = [];
    for (const p of [a, b]) if (p !== null && p.length === 3 && p.every((v) => Number.isFinite(v))) ends.push(p[0]!, p[1]!, p[2]!);
    if (ends.length === 0) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(ends), 3));
    const dots = new THREE.Points(geometry, this.dotMaterial);
    dots.name = "plot-line:ends";
    dots.renderOrder = LINE_ORDER + 1;
    dots.frustumCulled = false;
    this.scene.add(dots);
    this.dots = dots;
  }

  setResolution(width: number, height: number): void {
    this.material.resolution.set(Math.max(1, width), Math.max(1, height));
  }

  setAccent(accent: THREE.ColorRepresentation): void {
    this.material.color.set(accent);
    this.dotMaterial.color.set(accent);
  }

  /** Takes the objects out and frees their geometries; with `all`, the materials too (the scene is going). */
  dispose(all = true): void {
    if (this.line !== null) {
      this.scene.remove(this.line);
      this.line.geometry.dispose();
      this.line = null;
    }
    if (this.dots !== null) {
      this.scene.remove(this.dots);
      this.dots.geometry.dispose();
      this.dots = null;
    }
    if (all) {
      this.material.dispose();
      this.dotMaterial.dispose();
    }
  }
}
