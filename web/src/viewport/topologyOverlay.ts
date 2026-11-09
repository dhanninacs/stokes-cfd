/**
 * The edge and vertex overlay (ADR-0022 §15.3, T-058): one accent line object for the hovered or
 * picked edges and one marker object for the vertices, rebuilt only when the *pick* changes.
 *
 * §15.3 is specific about how this must be drawn, and the rules are why it is a module of its own:
 *
 * * "hover draws one accent line (edge) or marker (point) above the surface";
 * * "the persistent picked set uses a batched line/point overlay rebuilt on pick or camera/mesh
 *   changes, **not one draw call per edge, vertex or part**";
 * * "scale the point marker and line pick width in screen space";
 * * "**no topology scan or extra draw in body/face mode**" and "no edge work runs in the normal
 *   orbit path".
 *
 * So the objects here are created empty and stay invisible until there is something to show; adding
 * or clearing a pick rewrites their position buffers, and nothing in this class is called from a
 * frame or an orbit. That is what keeps the orbit budget untouched — there is no per-frame work to
 * measure, which is the property §15.3's probe is there to protect.
 *
 * Coordinates are world metres. A part's topology record stores its points in the part's own local
 * frame, so the caller passes the matrix that takes them to world; baking it here (rather than
 * parenting the object per part) is what allows one line object for edges from many parts — the
 * batching §15.3 asks for.
 *
 * Freeing the overlay frees only its own buffers: it borrows no geometry from a part mesh.
 */

import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";

import type { PartTopology } from "./topology";

/** Where an overlay belongs in the draw order: above the surface, under the measure line. */
const OVERLAY_ORDER = 3;

/** Marker size in device pixels, and the line width, both in screen space (§15.3). */
export const MARKER_SIZE_PX = 9;
export const LINE_WIDTH_PX = 3;

/** One edge to draw: which part, which edge id, and the known-invisible case is filtered by callers. */
export interface EdgeRef {
  part: string;
  id: number;
}

/** One vertex to draw. */
export interface VertexRef {
  part: string;
  id: number;
}

/**
 * The hover and pick overlay for edges and vertices.
 *
 * `set(edges, vertices, matrices)` replaces what is drawn; each ref is looked up in the topology
 * records the caller passes. A ref whose part has no record, or whose id is out of range, is skipped
 * rather than guessed at — the caller is expected to have filtered those out, and silently drawing
 * the wrong edge would be worse than drawing none.
 */
export class TopologyOverlay {
  /** The hovered edge(s): usually one. Add to the scene; it hides itself when empty. */
  readonly hover: LineSegments2;
  /** The picked edges. */
  readonly picked: LineSegments2;
  /** The hovered vertex marker. */
  readonly hoverPoints: THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial>;
  /** The picked vertex markers. */
  readonly pickedPoints: THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial>;

  private readonly hoverMaterial: LineMaterial;
  private readonly pickedMaterial: LineMaterial;
  private readonly hoverPointMaterial: THREE.PointsMaterial;
  private readonly pickedPointMaterial: THREE.PointsMaterial;
  private disposed = false;

  constructor(accent: string, host: THREE.Object3D) {
    const color = new THREE.Color(accent);
    // World-space overlays: `resolution` is what LineMaterial needs to keep its width in pixels,
    // and the scene sets it with the canvas size (setResolution).
    this.hoverMaterial = new LineMaterial({ color, linewidth: LINE_WIDTH_PX, worldUnits: false, depthTest: true, transparent: true });
    this.pickedMaterial = new LineMaterial({ color, linewidth: LINE_WIDTH_PX, worldUnits: false, depthTest: true, transparent: true });

    this.hover = makeLines(this.hoverMaterial, "topology:hover-edges", host);
    this.picked = makeLines(this.pickedMaterial, "topology:picked-edges", host);

    this.hoverPointMaterial = makePointMaterial(color, MARKER_SIZE_PX, 0.55);
    this.pickedPointMaterial = makePointMaterial(color, MARKER_SIZE_PX, 1.0);
    this.hoverPoints = makePoints(this.hoverPointMaterial, "topology:hover-points", host);
    this.pickedPoints = makePoints(this.pickedPointMaterial, "topology:picked-points", host);
  }

  /**
   * Replace what is drawn. `matrices` maps a part id to the matrix that takes its topology points to
   * world; a part without one contributes nothing.
   */
  set(
    hoverEdges: readonly EdgeRef[],
    pickedEdges: readonly EdgeRef[],
    hoverVertices: readonly VertexRef[],
    pickedVertices: readonly VertexRef[],
    topologies: ReadonlyMap<string, PartTopology>,
    matrices: ReadonlyMap<string, THREE.Matrix4>,
  ): void {
    if (this.disposed) return;
    this.setEdges(this.hover, hoverEdges, topologies, matrices);
    this.setEdges(this.picked, pickedEdges, topologies, matrices);
    this.setPoints(this.hoverPoints, hoverVertices, topologies, matrices);
    this.setPoints(this.pickedPoints, pickedVertices, topologies, matrices);
  }

  /** Clear everything, e.g. when leaving Edge or Point mode. */
  clear(): void {
    if (this.disposed) return;
    this.setEdges(this.hover, [], new Map(), new Map());
    this.setEdges(this.picked, [], new Map(), new Map());
    this.setPoints(this.hoverPoints, [], new Map(), new Map());
    this.setPoints(this.pickedPoints, [], new Map(), new Map());
  }

  /**
   * The canvas resolution, in device pixels. `LineMaterial` needs it to keep a line's width constant
   * on screen; §15.3 asks for exactly that ("scale the point marker and line pick width in screen
   * space").
   */
  setResolution(width: number, height: number): void {
    this.hoverMaterial.resolution.set(width, height);
    this.pickedMaterial.resolution.set(width, height);
  }

  /** Follow the theme's accent, as the other overlays do. */
  setAccent(accent: string): void {
    const color = new THREE.Color(accent);
    this.hoverMaterial.color.copy(color);
    this.pickedMaterial.color.copy(color);
    this.hoverPointMaterial.color.copy(color);
    this.pickedPointMaterial.color.copy(color);
  }

  /** Free the overlay's own buffers; borrowed part geometry is never touched. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const object of [this.hover, this.picked]) {
      object.geometry.dispose();
      object.parent?.remove(object);
    }
    for (const points of [this.hoverPoints, this.pickedPoints]) {
      points.geometry.dispose();
      points.parent?.remove(points);
    }
    this.hoverMaterial.dispose();
    this.pickedMaterial.dispose();
    this.hoverPointMaterial.dispose();
    this.pickedPointMaterial.dispose();
  }

  // ------------------------------------------------------------------ internals

  private setEdges(
    object: LineSegments2,
    refs: readonly EdgeRef[],
    topologies: ReadonlyMap<string, PartTopology>,
    matrices: ReadonlyMap<string, THREE.Matrix4>,
  ): void {
    const segments: number[] = [];
    const point = new THREE.Vector3();
    for (const ref of refs) {
      const topology = topologies.get(ref.part);
      const matrix = matrices.get(ref.part);
      const edge = topology?.edges[ref.id];
      if (topology === undefined || matrix === undefined || edge === undefined || !edge.pickable) continue;
      // One segment per polyline step: a curve is drawn as the polyline that was sampled for it.
      for (let i = 1; i < edge.pointsCount; i++) {
        point.set(edge.points[(i - 1) * 3], edge.points[(i - 1) * 3 + 1], edge.points[(i - 1) * 3 + 2]).applyMatrix4(matrix);
        segments.push(point.x, point.y, point.z);
        point.set(edge.points[i * 3], edge.points[i * 3 + 1], edge.points[i * 3 + 2]).applyMatrix4(matrix);
        segments.push(point.x, point.y, point.z);
      }
    }
    // `LineSegmentsGeometry.setPositions` copies, so the array can be discarded afterwards.
    object.geometry.dispose();
    object.geometry = new LineSegmentsGeometry();
    object.visible = segments.length > 0;
    if (segments.length === 0) return; // an empty geometry has no attributes to distance
    object.geometry.setPositions(segments);
    // LineMaterial's dashed mode needs line distances; computing them on an empty or attribute-less
    // geometry throws, so this is only reached with segments present.
    object.computeLineDistances();
  }

  private setPoints(
    points: THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial>,
    refs: readonly VertexRef[],
    topologies: ReadonlyMap<string, PartTopology>,
    matrices: ReadonlyMap<string, THREE.Matrix4>,
  ): void {
    const positions: number[] = [];
    const point = new THREE.Vector3();
    for (const ref of refs) {
      const topology = topologies.get(ref.part);
      const matrix = matrices.get(ref.part);
      const vertex = topology?.vertices[ref.id];
      if (topology === undefined || matrix === undefined || vertex === undefined) continue;
      point.set(vertex.position[0], vertex.position[1], vertex.position[2]).applyMatrix4(matrix);
      positions.push(point.x, point.y, point.z);
    }
    const geometry = new THREE.BufferGeometry();
    if (positions.length > 0) geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    points.geometry.dispose();
    points.geometry = geometry;
    points.visible = positions.length > 0;
  }
}

/** A world-space line object for the overlay: hidden, on the overlay layer, no per-frame work. */
function makeLines(material: LineMaterial, name: string, host: THREE.Object3D): LineSegments2 {
  const object = new LineSegments2(new LineSegmentsGeometry(), material);
  object.name = name;
  object.renderOrder = OVERLAY_ORDER;
  object.visible = false;
  object.frustumCulled = false; // the buffer changes with the pick; a stale bound would hide it
  host.add(object);
  return object;
}

/** A world-space point marker object. A square reads well at 9 px and needs no texture. */
function makePoints(
  material: THREE.PointsMaterial,
  name: string,
  host: THREE.Object3D,
): THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial> {
  const object = new THREE.Points(new THREE.BufferGeometry(), material);
  object.name = name;
  object.renderOrder = OVERLAY_ORDER;
  object.visible = false;
  object.frustumCulled = false;
  host.add(object);
  return object;
}

/** Marker material: constant screen size, drawn over the surface, with the accent colour. */
function makePointMaterial(color: THREE.Color, size: number, opacity: number): THREE.PointsMaterial {
  return new THREE.PointsMaterial({
    color,
    size,
    sizeAttenuation: false,
    transparent: true,
    opacity,
    depthWrite: false,
  });
}
