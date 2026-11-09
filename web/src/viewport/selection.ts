/**
 * The tint on the live pick: the faces chosen in face mode, or every face of the bodies picked
 * besides the active one (ADR-0021 3.5). One overlay mesh, in the accent colour, a little in front
 * of the surface, so a picked set is one draw call whatever it holds.
 *
 * A set within one group borrows that group's position and normal buffers and builds its own index,
 * as `FaceHighlight.show` does, so no vertex is copied. A set across groups or parts copies the
 * referenced triangles into one re-indexed, world-space geometry (a map from old to new vertex index
 * per part), because the meshes have no single world matrix to follow. The hovered face keeps
 * `FaceHighlight`; this class only draws what is picked.
 *
 * Freeing the overlay must free only its own index: borrowed attributes are removed before its
 * geometry is disposed, as in faces.ts.
 */

import * as THREE from "three";
import type { FaceSource } from "./faces";

/** What to tint: one group of a part, and the face ids to tint, or null for every triangle. */
export interface FacePick {
  source: FaceSource;
  faces: ReadonlySet<number> | null;
}

/** Opacity of the accent tint, as `FaceHighlight` uses. */
const OPACITY = 0.5;

export class SelectionHighlight {
  /** Add this to the scene. It is invisible until a pick is shown. */
  readonly mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
  /** World-space bounds of what is drawn; empty while nothing is. */
  readonly bounds = new THREE.Box3();
  private readonly material: THREE.MeshBasicMaterial;
  private readonly point = new THREE.Vector3();
  /**
   * The instanced overlays, one per group of picks that share a geometry and a face filter. A body
   * pick draws one, whatever the model's size: the same geometry with one matrix per body.
   *
   * This is the fix for the box-select budget. Copying the picked triangles into one world-space
   * geometry cost in proportion to the TRIANGLES of the pick — a wide body box over the 4,500-part
   * benchmark is 3,624 bodies and 565,992 triangles, which measured 417 ms of a 555 ms release
   * against a 50 ms budget (2026-10-06). Nothing about the look needs that copy: the bodies already
   * have their own world matrices, and the GPU can apply them.
   */
  private readonly instanced: THREE.InstancedMesh[] = [];
  /** Scratch for a geometry's world box, so growing `bounds` allocates nothing. */
  private readonly cloned = new THREE.Box3();
  /** True once `prewarm` has asked for the instanced variant; it is asked for once per scene. */
  private warmed = false;

  constructor(
    accent: string,
    /** Where a new instanced overlay is added; the scene owns the graph, as it does for `mesh`. */
    private readonly host: THREE.Object3D | null = null,
  ) {
    this.material = new THREE.MeshBasicMaterial({
      color: new THREE.Color(accent),
      transparent: true,
      opacity: OPACITY,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
      polygonOffset: true,
      polygonOffsetFactor: -2,
      polygonOffsetUnits: -2,
    });
    this.mesh = new THREE.Mesh(new THREE.BufferGeometry(), this.material);
    this.mesh.name = "pick-faces";
    this.mesh.matrixAutoUpdate = false;
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = 2;
    this.mesh.visible = false;
  }

  get visible(): boolean {
    return this.mesh.visible || this.instanced.some((mesh) => mesh.visible);
  }

  /** Every object it draws with (the plain overlay and the instanced ones), for a frame that must show no pick. */
  get objects(): readonly THREE.Object3D[] {
    return [this.mesh, ...this.instanced];
  }

  setAccent(accent: string): void {
    this.material.color.set(accent);
  }

  /**
   * Compiles the instanced overlay's shader variant ahead of time, so the first box, lasso or paint
   * stroke does not pay for it. Does nothing after the first call for a given context.
   *
   * The bug this exists for (measured 2026-10-08 on the owner's Intel Iris Xe via ANGLE/D3D11):
   * three.js compiles a material's program the first time it DRAWS that material, and an
   * `InstancedMesh` needs a different program from the plain `Mesh` this class starts with (three's
   * program key carries `instancing: IS_INSTANCEDMESH`). Nothing drew an instanced overlay until the
   * owner's first box select, so that gesture carried the cost. This is the same case
   * `scene.warmGlass` already handles for the translucent looks, so it warms the same way:
   * `compileAsync` on a one-triangle `InstancedMesh`, off the main path, and the caller disposes the
   * probe when the promise settles.
   *
   * `geometry` is the scene's shared probe triangle, owned and freed by the scene, so it is not
   * disposed here.
   */
  prewarm(
    renderer: THREE.WebGLRenderer,
    geometry: THREE.BufferGeometry,
    camera: THREE.Camera,
    scene: THREE.Scene,
  ): void {
    if (this.warmed) return;
    this.warmed = true;
    const host = this.host;
    if (host === null) return;
    // Single-instance, and never on screen for a frame: `compileAsync` links and uploads without it
    // being drawn, so there is nothing to hide.
    const probe = new THREE.InstancedMesh(geometry, this.material, 1);
    probe.frustumCulled = false;
    probe.matrixAutoUpdate = false;
    probe.matrix.identity();
    const group = new THREE.Group().add(probe);
    host.add(group);
    const settle = (): void => {
      host.remove(group);
      probe.dispose();
    };
    // `scene` is the compile root (three walks it for lights and the environment), `host` is where
    // the probe is parked for the walk to find it.
    renderer.compileAsync(group, camera, scene).then(settle, settle);
  }

  /**
   * Re-arms `prewarm` after a lost context took the compiled program with it, as the scene's
   * `coolKinds` does for the part looks. Without this the next box select would pay the compile
   * again on a restored context.
   */
  uncool(): void {
    this.warmed = false;
  }

  /** Draws the given picks; an empty list clears. Returns true when the picture changed. */
  show(picks: readonly FacePick[]): boolean {
    const active = picks.filter((pick) => pick.faces === null || pick.faces.size > 0);
    if (active.length === 0) return this.clear();
    // Many picks: draw them instanced, grouped by what they actually share. A body pick over a
    // batched model is one group, so this is one geometry and one matrix per body.
    if (active.length > 1) {
      const groups = this.group(active);
      if (groups !== null) {
        this.drawInstanced(groups);
        return true;
      }
    }
    let geometry: THREE.BufferGeometry | null = null;
    if (active.length === 1) {
      geometry = this.borrow(active[0]);
      if (geometry) this.mesh.matrix.copy(active[0].source.mesh.matrixWorld);
    } else {
      geometry = this.copy(active);
      if (geometry) this.mesh.matrix.identity();
    }
    if (!geometry) return this.clear();
    this.clearInstanced();
    this.swap(geometry);
    this.mesh.visible = true;
    this.measure(geometry, this.mesh.matrix);
    return true;
  }

  /**
   * The picks grouped by the geometry they draw and the faces they keep, in the order they arrived.
   * `null` when a group would need more instances than the driver can be trusted with, so the
   * caller falls back to the copying path.
   */
  private group(picks: readonly FacePick[]): { geometry: THREE.BufferGeometry; faces: ReadonlySet<number> | null; picks: FacePick[] }[] | null {
    const byGeometry = new Map<THREE.BufferGeometry, Map<ReadonlySet<number> | null, FacePick[]>>();
    for (const pick of picks) {
      const geometry = pick.source.mesh.geometry;
      let byFaces = byGeometry.get(geometry);
      if (byFaces === undefined) {
        byFaces = new Map();
        byGeometry.set(geometry, byFaces);
      }
      const bucket = byFaces.get(pick.faces);
      if (bucket === undefined) byFaces.set(pick.faces, [pick]);
      else bucket.push(pick);
    }
    const groups: { geometry: THREE.BufferGeometry; faces: ReadonlySet<number> | null; picks: FacePick[] }[] = [];
    for (const [geometry, byFaces] of byGeometry) {
      for (const [faces, bucket] of byFaces) groups.push({ geometry, faces, picks: bucket });
    }
    return groups.length > 0 ? groups : null;
  }

  /**
   * Draws the groups as instanced meshes, reusing the meshes already made when the count allows.
   * The meshes are made once per group and then only re-pointed and re-matrixed, so dragging a
   * second box does not allocate a geometry again.
   */
  private drawInstanced(groups: { geometry: THREE.BufferGeometry; faces: ReadonlySet<number> | null; picks: FacePick[] }[]): void {
    this.clearInstanced(groups.length);
    this.mesh.visible = false;
    this.bounds.makeEmpty();
    groups.forEach((entry, at) => {
      let mesh = this.instanced[at];
      if (mesh === undefined || mesh.geometry !== entry.geometry) {
        if (mesh !== undefined) mesh.dispose();
        mesh = new THREE.InstancedMesh(entry.geometry, this.material, entry.picks.length);
        mesh.frustumCulled = false;
        mesh.matrixAutoUpdate = false;
        mesh.renderOrder = 2;
        // The overlay lives in world space, as the copying path's geometry did: each pick's own
        // matrix is its mesh's world matrix, applied per instance by the shader instead of per
        // vertex here.
        mesh.matrix.identity();
        this.instanced[at] = mesh;
        this.host?.add(mesh);
      } else if (mesh.instanceMatrix.count < entry.picks.length) {
        // Only a longer set needs a bigger buffer; a shorter one just draws fewer instances.
        mesh.instanceMatrix = new THREE.InstancedBufferAttribute(
          new Float32Array(entry.picks.length * 16),
          16,
        );
      }
      mesh.count = entry.picks.length;
      entry.picks.forEach((pick, instance) => {
        mesh.setMatrixAt(instance, pick.source.mesh.matrixWorld);
      });
      mesh.instanceMatrix.needsUpdate = true;
      mesh.visible = true;
      // The bounds come from each geometry's own box, so this reads no vertex either.
      for (const pick of entry.picks) this.expandToInstance(entry.geometry, pick.source.mesh.matrixWorld);
    });
  }

  /** Grows `bounds` by one instance of `geometry` under `matrix`, reading no vertex. */
  private expandToInstance(geometry: THREE.BufferGeometry, matrix: THREE.Matrix4): void {
    if (geometry.boundingBox === null) geometry.computeBoundingBox();
    const box = geometry.boundingBox;
    if (box === null) return;
    this.cloned.copy(box).applyMatrix4(matrix);
    this.bounds.union(this.cloned);
  }

  /**
   * Drops the instanced overlays after the first `keep`, hiding and freeing the rest. They are kept
   * rather than disposed on every pick, because the next box usually wants the same geometries.
   */
  private clearInstanced(keep = 0): void {
    for (let i = this.instanced.length - 1; i >= keep; i--) {
      const mesh = this.instanced[i];
      mesh.visible = false;
      mesh.dispose();
      if (this.host !== null) this.host.remove(mesh);
      this.instanced.splice(i, 1);
    }
  }

  /** Takes the tint away. Returns true when there was one. */
  clear(): boolean {
    this.bounds.makeEmpty();
    const wasVisible = this.mesh.visible || this.instanced.some((mesh) => mesh.visible);
    if (!wasVisible) return false;
    this.mesh.visible = false;
    this.clearInstanced();
    this.swap(new THREE.BufferGeometry());
    return true;
  }

  dispose(): void {
    this.clear();
    this.mesh.geometry.dispose();
    this.material.dispose();
  }

  /** One group's chosen triangles over its own position and normal buffers. */
  private borrow(pick: FacePick): THREE.BufferGeometry | null {
    const { mesh, faceIds, indices } = pick.source;
    let count = 0;
    for (let t = 0; t < faceIds.length; t++) if (pick.faces === null || pick.faces.has(faceIds[t])) count++;
    if (count === 0) return null;
    const own = new Uint32Array(count * 3);
    let at = 0;
    for (let t = 0; t < faceIds.length; t++) {
      if (pick.faces !== null && !pick.faces.has(faceIds[t])) continue;
      own[at++] = indices[t * 3];
      own[at++] = indices[t * 3 + 1];
      own[at++] = indices[t * 3 + 2];
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", mesh.geometry.getAttribute("position"));
    geometry.setAttribute("normal", mesh.geometry.getAttribute("normal"));
    geometry.setIndex(new THREE.BufferAttribute(own, 1));
    return geometry;
  }

  /** The chosen triangles of several groups copied into one world-space geometry. */
  private copy(picks: readonly FacePick[]): THREE.BufferGeometry | null {
    const position: number[] = [];
    const normal: number[] = [];
    const index: number[] = [];
    const vertex = new THREE.Vector3();
    const direction = new THREE.Vector3();
    const normalMatrix = new THREE.Matrix3();
    for (const pick of picks) {
      const mesh = pick.source.mesh;
      const geometry = mesh.geometry;
      const positions = geometry.getAttribute("position");
      const normals = geometry.getAttribute("normal");
      const indices = pick.source.indices;
      const faceIds = pick.source.faceIds;
      normalMatrix.getNormalMatrix(mesh.matrixWorld);
      const seen = new Map<number, number>();
      const add = (vertexIndex: number): number => {
        let next = seen.get(vertexIndex);
        if (next !== undefined) return next;
        next = position.length / 3;
        vertex.fromBufferAttribute(positions, vertexIndex).applyMatrix4(mesh.matrixWorld);
        position.push(vertex.x, vertex.y, vertex.z);
        if (normals) {
          direction.fromBufferAttribute(normals, vertexIndex).applyMatrix3(normalMatrix).normalize();
          normal.push(direction.x, direction.y, direction.z);
        }
        seen.set(vertexIndex, next);
        return next;
      };
      for (let t = 0; t < faceIds.length; t++) {
        if (pick.faces !== null && !pick.faces.has(faceIds[t])) continue;
        index.push(add(indices[t * 3]), add(indices[t * 3 + 1]), add(indices[t * 3 + 2]));
      }
    }
    if (index.length === 0) return null;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(position, 3));
    if (normal.length) geometry.setAttribute("normal", new THREE.Float32BufferAttribute(normal, 3));
    geometry.setIndex(index);
    return geometry;
  }

  /** The world-space bounds of the drawn triangles, for the context toolbar's anchor. */
  private measure(geometry: THREE.BufferGeometry, matrix: THREE.Matrix4): void {
    const positions = geometry.getAttribute("position");
    const index = geometry.getIndex();
    this.bounds.makeEmpty();
    if (!positions || !index) return;
    for (let i = 0; i < index.count; i++) this.bounds.expandByPoint(this.point.fromBufferAttribute(positions, index.getX(i)));
    this.bounds.applyMatrix4(matrix);
  }

  /** Puts a new geometry in and frees the old one's index, but not the buffers it borrowed. */
  private swap(next: THREE.BufferGeometry): void {
    const old = this.mesh.geometry;
    this.mesh.geometry = next;
    old.deleteAttribute("position");
    old.deleteAttribute("normal");
    old.dispose();
  }
}