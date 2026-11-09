/**
 * The boundary glyphs as instanced meshes (ADR-0022 §8.1, T-046): **one `InstancedMesh` per glyph
 * shape**, on their own layer, rebuilt only when the boundaries or a part's mesh change.
 *
 * §8.1's rules are what this class exists to honour:
 *
 * * one glyph per boundary, never per face — the instance count is the number of boundaries, so a
 *   tag of 900 faces still costs one instance;
 * * its own layer, outside `PART_LAYER`, so the glyphs are excluded from the outline occluder pass,
 *   the id pass, the contact shadow and the batches (nothing else draws that layer);
 * * constant on-screen size: the caller passes a world `scale` per glyph (from `glyphs.ts`), and the
 *   per-frame work is one matrix write per instance — tens, never faces;
 * * colour is the tag's, per instance, so an override follows without a rebuild.
 *
 * The shapes are built once, in the constructor, from cheap primitives: an arrow is a cone on a
 * shaft, a plane is a thin box, a pair is two facing plates, a diamond is an octahedron. `none` is
 * not built at all — a kind with no glyph (a wall) simply has no instances.
 *
 * A shape's geometry points along **+Z** in its own space, because §8.1's placement gives a
 * direction to orient along; `orient` builds the quaternion that takes +Z to that direction.
 */

import * as THREE from "three";

import type { Glyph, GlyphShape } from "./glyphs";

/**
 * The colour a glyph falls back to when its tag has none: a muted grey, matching the wall kind's.
 * Never the accent — the accent means selection in this UI.
 */
const FALLBACK_COLOR = new THREE.Color("#7f8c99");

/** Half the glyph's own size along its long axis: `glyphs.ts` scales by the world size. */
const UNIT = 0.5;

export class GlyphLayer {
  /** The object to add to the scene; the per-shape instances are its children. */
  readonly object: THREE.Group;

  private readonly material: THREE.MeshBasicMaterial;
  /** One instanced mesh per shape, built lazily the first time that shape is needed. */
  private readonly meshes = new Map<GlyphShape, THREE.InstancedMesh>();
  /** The geometry per shape, so `dispose` can free what the constructor did not know it needed. */
  private readonly geometries = new Map<GlyphShape, THREE.BufferGeometry>();
  private disposed = false;

  /** Scratch for the instance matrix, so a rebuild allocates nothing per glyph. */
  private readonly position = new THREE.Vector3();
  private readonly direction = new THREE.Vector3();
  private readonly scale = new THREE.Vector3();
  private readonly quaternion = new THREE.Quaternion();
  private readonly matrix = new THREE.Matrix4();
  private readonly color = new THREE.Color();
  /** The +Z axis a shape's geometry points along, which `orient` rotates from. */
  private static readonly FORWARD = new THREE.Vector3(0, 0, 1);

  /**
   * `layer` is the glyph layer, passed rather than imported so the one place that defines what a
   * layer means stays in `scene.ts`.
   *
   * `camera` is the camera that must DRAW the glyphs, and the layer is enabled on it here. That
   * parameter is deliberate (T-046-fix-1): a camera in three.js starts on layer 0 alone and draws an
   * object only when `camera.layers.test(object.layers)` holds, so putting the meshes on a private
   * layer without enabling that layer on the main camera produced glyphs that were built, instanced,
   * positioned — and completely invisible. All 31 tests passed, because each checked the layer
   * *assignment* and never what the camera draws. Taking the camera as a constructor parameter makes
   * "somebody must enable this" structural instead of a line a caller has to remember.
   */
  constructor(
    host: THREE.Object3D,
    private readonly layer: number,
    camera: THREE.Camera,
  ) {
    this.object = new THREE.Group();
    this.object.name = "glyphs";
    // The glyphs are drawn over the model and must not be occluded by it: a boundary marker inside
    // a closed body would otherwise never be visible.
    // T-046-fix-2: `depthTest: true` (it used to be false) is what makes a glyph on a hidden face
    // stay hidden. The glyphs are drawn in the same main pass as the parts, after them, so an opaque
    // surface in front now occludes the marker with **no per-frame work and no rebuild-time
    // raycast** — and, unlike a rebuild-time occlusion test, it stays correct while the camera
    // orbits. A translucent part does not write depth in this renderer and wireframe/X-ray has no
    // filled depth, so the glyph correctly shows through those without extra code.
    //
    // `depthWrite: false` keeps the markers from occluding each other or the parts behind them, and
    // `polygonOffset` pushes the glyph a hair away from the face it sits on: the anchor is *on* the
    // surface (that is what T-046-fix-3 fixed), so the arrow's base can z-fight with it otherwise.
    // `transparent` stays for the material's blending, not for ordering.
    this.material = new THREE.MeshBasicMaterial({
      depthTest: true,
      depthWrite: false,
      transparent: true,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -1,
    });
    host.add(this.object);
    // The id, outline and shadow passes narrow the camera with `layers.set` and restore `mask`
    // afterwards, so enabling this here survives them and they still exclude the glyphs.
    camera.layers.enable(this.layer);
  }

  /**
   * Replace what is drawn. An empty list hides everything without disposing, so switching the
   * glyphs off and on again costs no geometry rebuild.
   */
  set(glyphs: readonly Glyph[]): void {
    if (this.disposed) return;
    const byShape = new Map<GlyphShape, Glyph[]>();
    for (const glyph of glyphs) {
      const list = byShape.get(glyph.shape);
      if (list === undefined) byShape.set(glyph.shape, [glyph]);
      else list.push(glyph);
    }
    for (const [shape, mesh] of this.meshes) {
      const wanted = byShape.get(shape) ?? [];
      mesh.count = wanted.length;
      mesh.visible = wanted.length > 0;
      mesh.instanceMatrix.needsUpdate = true;
      this.write(mesh, wanted);
    }
    for (const [shape, wanted] of byShape) {
      if (this.meshes.has(shape)) continue;
      const mesh = this.build(shape, wanted.length);
      this.meshes.set(shape, mesh);
      this.write(mesh, wanted);
    }
  }

  /** Free every geometry and material this layer owns. Safe to call twice. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const mesh of this.meshes.values()) {
      this.object.remove(mesh);
      mesh.dispose();
    }
    for (const geometry of this.geometries.values()) geometry.dispose();
    this.geometries.clear();
    this.meshes.clear();
    this.material.dispose();
    this.object.parent?.remove(this.object);
  }

  /** How many glyphs are drawn, for a test or a perf readout. */
  count(): number {
    let total = 0;
    for (const mesh of this.meshes.values()) total += mesh.visible ? mesh.count : 0;
    return total;
  }

  // ------------------------------------------------------------------ internals

  private write(mesh: THREE.InstancedMesh, glyphs: readonly Glyph[]): void {
    for (let index = 0; index < glyphs.length; index++) {
      const glyph = glyphs[index];
      this.position.set(glyph.position[0], glyph.position[1], glyph.position[2]);
      this.direction.set(glyph.direction[0], glyph.direction[1], glyph.direction[2]);
      this.orient(this.direction);
      // The shape's own geometry is a unit along its axis, so the world scale multiplies it directly.
      this.scale.setScalar(Math.max(glyph.scale, 1e-6));
      this.matrix.compose(this.position, this.quaternion, this.scale);
      mesh.setMatrixAt(index, this.matrix);
      // The material is shared, so the colour has to be per instance (three multiplies them).
      mesh.setColorAt(index, this.color.set(glyph.color));
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor !== null) mesh.instanceColor.needsUpdate = true;
  }

  /** The rotation that takes a shape's +Z to `direction`; identity when there is no direction. */
  private orient(direction: THREE.Vector3): void {
    if (direction.lengthSq() === 0) {
      this.quaternion.identity();
      return;
    }
    this.direction.copy(direction).normalize();
    this.quaternion.setFromUnitVectors(GlyphLayer.FORWARD, this.direction);
  }

  /** Build the instanced mesh for one shape, with room for `count` instances. */
  private build(shape: GlyphShape, count: number): THREE.InstancedMesh {
    const geometry = this.geometryFor(shape);
    const mesh = new THREE.InstancedMesh(geometry, this.material, Math.max(count, 1));
    mesh.name = `glyphs:${shape}`;
    mesh.layers.set(this.layer);
    // Instanced and rebuilt in place: a bounding-sphere cull would hide a glyph whose anchor moved.
    mesh.frustumCulled = false;
    mesh.count = count;
    mesh.visible = count > 0;
    // `setColorAt` allocates the instance colour attribute on first use.
    mesh.setColorAt(0, FALLBACK_COLOR);
    this.object.add(mesh);
    return mesh;
  }

  private geometryFor(shape: GlyphShape): THREE.BufferGeometry {
    const hit = this.geometries.get(shape);
    if (hit !== undefined) return hit;
    const geometry = buildShape(shape);
    this.geometries.set(shape, geometry);
    return geometry;
  }
}

/** A unit shape pointing along +Z, cheap enough to build once per layer. */
function buildShape(shape: GlyphShape): THREE.BufferGeometry {
  if (shape === "arrow_in" || shape === "arrow_out") {
    // A shaft with a cone on the end: one merged geometry, still cheap. The cone's own tip is at
    // +Z, so the whole arrow points along +Z.
    const shaft = new THREE.CylinderGeometry(0.06, 0.06, UNIT, 8);
    shaft.rotateX(Math.PI / 2);
    shaft.translate(0, 0, -UNIT / 2);
    const head = new THREE.ConeGeometry(0.18, UNIT, 12);
    head.rotateX(Math.PI / 2);
    head.translate(0, 0, UNIT / 2);
    return merge([shaft, head]);
  }
  if (shape === "pair") {
    // Two plates facing each other, for a cyclic or interface pair.
    const left = new THREE.BoxGeometry(0.08, 0.4, 0.4);
    left.translate(-0.15, 0, 0);
    const right = new THREE.BoxGeometry(0.08, 0.4, 0.4);
    right.translate(0.15, 0, 0);
    return merge([left, right]);
  }
  if (shape === "diamond") {
    // An octahedron: visible from every angle and with no direction of its own.
    return new THREE.OctahedronGeometry(0.3);
  }
  // A plane (the default, and what a wall-like or unknown kind gets): a thin flat tile facing +Z.
  return new THREE.BoxGeometry(0.5, 0.5, 0.06);
}

/**
 * Merge geometries that share a material into one buffer. Each is converted to non-indexed form
 * first, which is wasteful in principle and irrelevant here: these are a few dozen vertices.
 */
function merge(parts: readonly THREE.BufferGeometry[]): THREE.BufferGeometry {
  const positions: number[] = [];
  const normals: number[] = [];
  for (const part of parts) {
    const flat = part.index === null ? part : part.toNonIndexed();
    const position = flat.getAttribute("position");
    const normal = flat.getAttribute("normal");
    for (let i = 0; i < position.count; i++) {
      positions.push(position.getX(i), position.getY(i), position.getZ(i));
      if (normal !== undefined) normals.push(normal.getX(i), normal.getY(i), normal.getZ(i));
      else normals.push(0, 0, 1);
    }
    if (flat !== part) flat.dispose();
    part.dispose();
  }
  const merged = new THREE.BufferGeometry();
  merged.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  merged.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  return merged;
}
