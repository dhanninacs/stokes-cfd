/**
 * The volume mesh at the section plane (`mesh.section`): every cell the plane cuts, filled by region and
 * outlined with its own edges, the way a mesh tool's section shows a mesh. Drawn on top of the clipped
 * boundary surfaces, so the Mesh stage's section is a cut through the cells rather than a hollow skin.
 *
 * Pure three.js and pure decoding: the server's base64 arrays are viewed as typed arrays, nothing is parsed
 * per number. The cut lies exactly on the clip plane, so it is moved a hair to the kept side (`lift`),
 * which keeps the clip from discarding it and wins the depth test against the flat section caps.
 */
import * as THREE from "three";

/** What `mesh.section` answers when ready, decoded. */
export interface MeshCut {
  axis: "x" | "y" | "z";
  offset: number;
  cells: number;
  names: string[];
  /** Each region's kind ("fluid", "solid") where the project names it, else "". */
  kinds: string[];
  positions: Float32Array;
  triangles: Uint32Array;
  edges: Uint32Array;
  regions: Uint8Array;
}

/** The fluid regions' fills, in turn; a region named hot or cold takes the warm or the cool one. */
export const FLUID_FILLS = ["#5b8fd9", "#e0795b", "#6fb38f", "#e3b55c", "#9b82c9", "#5bbfc7", "#c9799b"];
const WARM = "#e0795b";
const COOL = "#5b8fd9";
/** A solid region's fill: metal grey. */
export const SOLID_FILL = "#b4bac2";
/** A mesh of one region (no names) is drawn in this. */
export const SINGLE_FILL = "#a9c4e2";
const EDGE_COLOR = "#1d2430";

function bytesOf(text: unknown): Uint8Array {
  if (typeof text !== "string" || text === "") return new Uint8Array(0);
  const raw = atob(text);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** The command's answer as a cut, or null when it is not a ready one or its arrays disagree. */
export function readMeshCut(raw: unknown): MeshCut | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (r.state !== "ready" || (r.axis !== "x" && r.axis !== "y" && r.axis !== "z") || typeof r.offset !== "number") return null;
  const p = bytesOf(r.positions);
  const t = bytesOf(r.triangles);
  const e = bytesOf(r.edges);
  const g = bytesOf(r.regions);
  if (p.length % 12 || t.length % 12 || e.length % 8 || g.length * 12 !== t.length) return null;
  const positions = new Float32Array(p.buffer, 0, p.length / 4);
  const triangles = new Uint32Array(t.buffer, 0, t.length / 4);
  const edges = new Uint32Array(e.buffer, 0, e.length / 4);
  const points = positions.length / 3;
  for (const list of [triangles, edges]) for (let i = 0; i < list.length; i += 1) if (list[i]! >= points) return null;
  return {
    axis: r.axis,
    offset: r.offset,
    cells: typeof r.cells === "number" ? r.cells : 0,
    names: Array.isArray(r.names) ? r.names.filter((n): n is string => typeof n === "string") : [],
    kinds: Array.isArray(r.kinds) ? r.kinds.map((k) => (typeof k === "string" ? k : "")) : [],
    positions,
    triangles,
    edges,
    regions: g,
  };
}

/** The fill of region `index` in a cut with these regions: solids grey, hot warm, cold cool, other fluids in turn. */
export function regionFill(index: number, names: readonly string[], kinds: readonly string[] = []): string {
  if (names.length <= 1) return SINGLE_FILL;
  if (kinds[index] === "solid") return SOLID_FILL;
  const name = (names[index] ?? "").toLowerCase();
  if (/hot|warm/.test(name)) return WARM;
  if (/cold|cool/.test(name)) return COOL;
  let fluid = 0;
  for (let i = 0; i < index; i += 1) if (kinds[i] !== "solid") fluid += 1;
  return FLUID_FILLS[fluid % FLUID_FILLS.length]!;
}

export class MeshCutLayer {
  readonly object = new THREE.Group();
  private readonly fillMaterial = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, toneMapped: false, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
  private readonly edgeMaterial = new THREE.LineBasicMaterial({ color: EDGE_COLOR, transparent: true, opacity: 0.85, toneMapped: false });
  private held: MeshCut | null = null;

  constructor() {
    this.object.name = "mesh-section";
    this.object.renderOrder = 5;
  }

  /** The cut on screen, if any. */
  get cut(): MeshCut | null {
    return this.held;
  }

  /** Draws `cut`, moved `lift` metres along `normal` (the clip's kept side); null takes it away. */
  set(cut: MeshCut | null, normal: THREE.Vector3, lift: number): void {
    this.clear();
    this.held = cut;
    if (cut === null || cut.triangles.length === 0) return;
    // The fill twice as far into the kept side as the edges, so the edges are in front of it from the camera,
    // which looks at the cut from the removed side.
    const shift = normal.clone().normalize().multiplyScalar(2 * lift);
    const points = cut.positions.length / 3;
    // Unshared vertices for the fill, so each triangle carries its region's colour flat.
    const tris = cut.triangles.length / 3;
    const fillPos = new Float32Array(tris * 9);
    const fillCol = new Float32Array(tris * 9);
    const colours = new Map<number, THREE.Color>();
    for (let t = 0; t < tris; t += 1) {
      const region = cut.regions[t] ?? 0;
      let colour = colours.get(region);
      if (colour === undefined) {
        colour = new THREE.Color(regionFill(region, cut.names, cut.kinds));
        colours.set(region, colour);
      }
      for (let k = 0; k < 3; k += 1) {
        const v = cut.triangles[t * 3 + k]!;
        const o = t * 9 + k * 3;
        fillPos[o] = cut.positions[v * 3]! + shift.x;
        fillPos[o + 1] = cut.positions[v * 3 + 1]! + shift.y;
        fillPos[o + 2] = cut.positions[v * 3 + 2]! + shift.z;
        fillCol[o] = colour.r;
        fillCol[o + 1] = colour.g;
        fillCol[o + 2] = colour.b;
      }
    }
    const fillGeometry = new THREE.BufferGeometry();
    fillGeometry.setAttribute("position", new THREE.BufferAttribute(fillPos, 3));
    fillGeometry.setAttribute("color", new THREE.BufferAttribute(fillCol, 3));
    const fill = new THREE.Mesh(fillGeometry, this.fillMaterial);
    fill.name = "mesh-section:cells";
    fill.frustumCulled = false;
    fill.renderOrder = 5;

    const lifted = new Float32Array(points * 3);
    const near = shift.clone().multiplyScalar(0.5);
    for (let i = 0; i < points; i += 1) {
      lifted[i * 3] = cut.positions[i * 3]! + near.x;
      lifted[i * 3 + 1] = cut.positions[i * 3 + 1]! + near.y;
      lifted[i * 3 + 2] = cut.positions[i * 3 + 2]! + near.z;
    }
    const edgeGeometry = new THREE.BufferGeometry();
    edgeGeometry.setAttribute("position", new THREE.BufferAttribute(lifted, 3));
    edgeGeometry.setIndex(new THREE.BufferAttribute(cut.edges, 1));
    const edges = new THREE.LineSegments(edgeGeometry, this.edgeMaterial);
    edges.name = "mesh-section:edges";
    edges.frustumCulled = false;
    edges.renderOrder = 6;
    this.object.add(fill, edges);
  }

  /** The legend: each region's name and fill, in index order; empty for a mesh of one region. */
  legend(): { name: string; fill: string }[] {
    const names = this.held?.names ?? [];
    const kinds = this.held?.kinds ?? [];
    return names.length <= 1 ? [] : names.map((name, i) => ({ name, fill: regionFill(i, names, kinds) }));
  }

  clear(): void {
    for (const child of [...this.object.children]) {
      this.object.remove(child);
      if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) child.geometry.dispose();
    }
    this.held = null;
  }

  dispose(): void {
    this.clear();
    this.fillMaterial.dispose();
    this.edgeMaterial.dispose();
  }
}

/** The plane to cut, or null when the mesh section should not show. */
export function wantedCut(state: { section: { axis: "x" | "y" | "z"; offset: number } | null; stage: string; meshDisplay: string }): { axis: "x" | "y" | "z"; offset: number } | null {
  const section = state.section;
  if (section === null) return null;
  if (state.stage !== "Mesh" && state.meshDisplay !== "cells") return null;
  return { axis: section.axis, offset: section.offset };
}
