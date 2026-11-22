/**
 * The ghost (ADR-0039): a translucent body built in the browser from the sketch's 2D profile and
 * a depth, shown while the handle is dragged and while the server's mesher rebuilds the real body.
 * No mesher and no API call: three.js extrudes the outline, which is exact for a pull of a polygonal
 * profile and within the chord tolerance for curves. Transient view state only: it is never the
 * model, and the body that arrives replaces it.
 */
import * as THREE from "three";
import { nest, type Loop } from "./profile";
import { planeAxes, type PlaneId, type Vec3 } from "./plane";

/** What a ghost shows: a profile (mm) on a plane through `origin` (m), pulled `depth` mm along w. */
export interface GhostSpec {
  plane: PlaneId;
  origin: Vec3;
  loops: Loop[];
  depth: number;
  symmetric: boolean;
}

/** The pulled profile in plane coordinates (mm): u, v across, w from 0 (or -depth/2) to depth. */
export function ghostGeometry(loops: readonly Loop[], depth: number, symmetric: boolean): THREE.BufferGeometry {
  const shapes = nest(loops).map(({ outer, holes }) => {
    const shape = new THREE.Shape(outer.map(([x, y]) => new THREE.Vector2(x, y)));
    shape.holes = holes.map((h) => new THREE.Path(h.map(([x, y]) => new THREE.Vector2(x, y))));
    return shape;
  });
  const geometry = new THREE.ExtrudeGeometry(shapes, { depth: Math.max(depth, 1e-6), bevelEnabled: false, curveSegments: 1, steps: 1 });
  if (symmetric) geometry.translate(0, 0, -depth / 2);
  return geometry;
}

/** The matrix from plane millimetres to world metres. */
export function planeMatrix(plane: PlaneId, origin: Vec3): THREE.Matrix4 {
  const { u, v, w } = planeAxes(plane);
  const m = new THREE.Matrix4().makeBasis(new THREE.Vector3(...u), new THREE.Vector3(...v), new THREE.Vector3(...w));
  m.scale(new THREE.Vector3(0.001, 0.001, 0.001));
  m.setPosition(origin[0], origin[1], origin[2]);
  return m;
}

/** The ghost's object: faces and edges in the accent colour, drawn through the model so a shorter pull still shows. */
export function ghostObject(spec: GhostSpec, colour: string): THREE.Object3D {
  const geometry = ghostGeometry(spec.loops, spec.depth, spec.symmetric);
  const group = new THREE.Group();
  group.name = "ghost";
  const faces = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({ color: new THREE.Color(colour), transparent: true, opacity: 0.22, depthWrite: false, depthTest: false, side: THREE.DoubleSide, toneMapped: false }),
  );
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(geometry, 20),
    new THREE.LineBasicMaterial({ color: new THREE.Color(colour), transparent: true, opacity: 0.9, depthTest: false, toneMapped: false }),
  );
  faces.renderOrder = 998;
  edges.renderOrder = 999;
  group.add(faces, edges);
  group.matrixAutoUpdate = false;
  group.matrix.copy(planeMatrix(spec.plane, spec.origin));
  group.matrixWorldNeedsUpdate = true;
  return group;
}

/** Frees what `ghostObject` made. */
export function disposeGhost(object: THREE.Object3D): void {
  object.traverse((o) => {
    const mesh = o as THREE.Mesh;
    mesh.geometry?.dispose();
    const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material?.dispose();
  });
}
