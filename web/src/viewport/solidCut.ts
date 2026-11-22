/**
 * The solid a section cuts through each body made with the modeller (`geometry.section`), filled in the body's
 * own colour. A stencil cap (`sectionCaps.ts`) needs a closed display mesh, and a lattice body's is not (the
 * mesher drops folded triangles), so its cut showed the hollow inside; the server computes these fills from the
 * body's field instead. Bodies with a fill here are left out of the stencil caps; imported bodies keep them.
 *
 * `SolidSectionController` (`solidSection.ts`) asks while a section is on and the bodies are what the view shows (not the Mesh stage's
 * cell cut, not the Results). Nothing here is a project command.
 */
import * as THREE from "three";
import { wantedCut } from "./meshCut";

/** One body's filled section, decoded. */
export interface SolidCut {
  part: string;
  positions: Float32Array;
  triangles: Uint32Array;
}

function bytesOf(text: unknown): Uint8Array {
  if (typeof text !== "string" || text === "") return new Uint8Array(0);
  const raw = atob(text);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** `geometry.section`'s answer for the plane `axis`/`offset`, or null when it is for another plane or malformed. */
export function readSolidCuts(raw: unknown, axis: string, offset: number): SolidCut[] | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (r.axis !== axis || typeof r.offset !== "number" || Math.abs(r.offset - offset) > 1e-12 || !Array.isArray(r.cuts)) return null;
  const out: SolidCut[] = [];
  for (const c of r.cuts as unknown[]) {
    if (typeof c !== "object" || c === null) continue;
    const cut = c as Record<string, unknown>;
    if (typeof cut.part !== "string") continue;
    const p = bytesOf(cut.positions);
    const t = bytesOf(cut.triangles);
    if (p.length % 12 || t.length % 12) continue;
    const positions = new Float32Array(p.buffer, 0, p.length / 4);
    const triangles = new Uint32Array(t.buffer, 0, t.length / 4);
    const points = positions.length / 3;
    if (triangles.some((i) => i >= points)) continue;
    out.push({ part: cut.part, positions, triangles });
  }
  return out;
}

/** Whether the bodies' own section should be asked for: a section is on and the parts are what is drawn. */
export function wantSolidCut(state: { section: { axis: "x" | "y" | "z"; offset: number } | null; stage: string; meshDisplay: string }): boolean {
  return state.section !== null && wantedCut(state) === null && state.stage !== "Results";
}

/** The edges used by one triangle only (the fill's outline), as pairs of point indices. */
export function outlineOf(triangles: Uint32Array): Uint32Array {
  const seen = new Map<string, number>();
  for (let t = 0; t < triangles.length; t += 3) {
    for (let k = 0; k < 3; k += 1) {
      const a = triangles[t + k]!;
      const b = triangles[t + ((k + 1) % 3)]!;
      const key = a < b ? `${a},${b}` : `${b},${a}`;
      seen.set(key, (seen.get(key) ?? 0) + 1);
    }
  }
  const out: number[] = [];
  for (const [key, n] of seen) {
    if (n !== 1) continue;
    const [a, b] = key.split(",");
    out.push(Number(a), Number(b));
  }
  return Uint32Array.from(out);
}

/** The fills in the scene: one mesh per body, in a group the scene adds once. */
export class SolidCutLayer {
  readonly object = new THREE.Group();
  private cuts: SolidCut[] = [];

  constructor() {
    this.object.name = "solid-section";
  }

  /** The parts that have a fill: the stencil caps leave them out. */
  parts(): ReadonlySet<string> {
    return new Set(this.cuts.map((c) => c.part));
  }

  /** Draws `cuts`, each in `colourOf(part)` (null: not drawn), moved `lift` metres along `normal`. */
  set(cuts: SolidCut[], colourOf: (part: string) => THREE.Color | null, normal: THREE.Vector3, lift: number): void {
    this.clear();
    this.cuts = cuts;
    const shift = normal.clone().normalize().multiplyScalar(lift);
    for (const cut of cuts) {
      const colour = colourOf(cut.part);
      if (colour === null || cut.triangles.length === 0) continue;
      const moved = new Float32Array(cut.positions.length);
      for (let i = 0; i < moved.length; i += 3) {
        moved[i] = cut.positions[i]! + shift.x;
        moved[i + 1] = cut.positions[i + 1]! + shift.y;
        moved[i + 2] = cut.positions[i + 2]! + shift.z;
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(moved, 3));
      geometry.setIndex(new THREE.BufferAttribute(cut.triangles, 1));
      const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: colour, side: THREE.DoubleSide }));
      mesh.name = `solid-section:${cut.part}`;
      mesh.frustumCulled = false;
      this.object.add(mesh);
      // The body's outline at the cut, darker than its fill: bodies of one colour (two fluids and the wall
      // between them) still read as separate shapes.
      const rim = outlineOf(cut.triangles);
      if (rim.length > 0) {
        const lines = new THREE.BufferGeometry();
        lines.setAttribute("position", new THREE.BufferAttribute(moved, 3));
        lines.setIndex(new THREE.BufferAttribute(rim, 1));
        const outline = new THREE.LineSegments(lines, new THREE.LineBasicMaterial({ color: colour.clone().multiplyScalar(0.45) }));
        outline.name = `solid-section:${cut.part}:outline`;
        outline.frustumCulled = false;
        outline.renderOrder = 1;
        this.object.add(outline);
      }
    }
  }

  clear(): void {
    for (const child of [...this.object.children]) {
      this.object.remove(child);
      if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) {
        child.geometry.dispose();
        (child.material as THREE.Material).dispose();
      }
    }
    this.cuts = [];
  }
}
