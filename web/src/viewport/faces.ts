/**
 * The tint on the face under the pointer.
 *
 * A part is one mesh with a face id for every triangle (transport version 2), so a face is not a
 * thing the GPU knows. Hovering one builds it on demand: the triangles that carry the id become a
 * small index buffer over the part's own position and normal buffers, drawn a little in front of the
 * surface in the accent colour. Nothing is built per face up front, and moving the pointer within a
 * face costs nothing; moving to another face costs one pass over the part's face ids.
 *
 * The overlay borrows the part's vertex buffers, so freeing it must free only its own index: the
 * borrowed attributes are removed from the overlay's geometry before it is disposed.
 */

import * as THREE from "three";

/** What the highlight needs to know about the part a face belongs to. */
export interface FaceSource {
  /** The part's mesh: its position and normal buffers are borrowed, its world matrix is followed. */
  mesh: THREE.Mesh;
  /** One face id per triangle. */
  faceIds: Uint32Array;
  /** Three vertex indices per triangle. */
  indices: Uint32Array;
}

/** Opacity of the accent tint. Half lets the surface's own shading show through. */
const OPACITY = 0.5;

export class FaceHighlight {
  /** Add this to the scene. It is invisible until a face is shown. */
  readonly mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
  private readonly material: THREE.MeshBasicMaterial;
  private shown: { owner: object; face: number } | null = null;

  constructor(accent: string) {
    this.material = new THREE.MeshBasicMaterial({
      color: new THREE.Color(accent),
      transparent: true,
      opacity: OPACITY,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
      // In front of the surface, which is itself pushed back a hair so edge lines never fight it.
      polygonOffset: true,
      polygonOffsetFactor: -2,
      polygonOffsetUnits: -2,
    });
    this.mesh = new THREE.Mesh(new THREE.BufferGeometry(), this.material);
    this.mesh.name = "hover-face";
    this.mesh.matrixAutoUpdate = false;
    // Culling would need a bounding sphere, which reads every vertex of the part.
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = 2;
    this.mesh.visible = false;
  }

  /** Whose face is shown now, or null. The owner is whatever the caller passed to `show`. */
  get owner(): object | null {
    return this.shown?.owner ?? null;
  }

  setAccent(accent: string): void {
    this.material.color.set(accent);
  }

  /**
   * Tints the face that triangle `triangle` of `source` belongs to. Returns true when the picture
   * changed, so the caller knows to draw again; the same face as before changes nothing.
   */
  show(owner: object, source: FaceSource, triangle: number): boolean {
    const ids = source.faceIds;
    if (triangle < 0 || triangle >= ids.length) return this.clear();
    const face = ids[triangle];
    if (this.shown && this.shown.owner === owner && this.shown.face === face) return false;
    let count = 0;
    for (let t = 0; t < ids.length; t++) if (ids[t] === face) count++;
    if (count === 0) return this.clear();
    const own = new Uint32Array(count * 3);
    let at = 0;
    for (let t = 0; t < ids.length; t++) {
      if (ids[t] !== face) continue;
      own[at++] = source.indices[t * 3];
      own[at++] = source.indices[t * 3 + 1];
      own[at++] = source.indices[t * 3 + 2];
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", source.mesh.geometry.getAttribute("position"));
    geometry.setAttribute("normal", source.mesh.geometry.getAttribute("normal"));
    geometry.setIndex(new THREE.BufferAttribute(own, 1));
    this.swap(geometry);
    this.mesh.matrix.copy(source.mesh.matrixWorld);
    this.mesh.visible = true;
    this.shown = { owner, face };
    return true;
  }

  /** Takes the tint away. Returns true when there was one. */
  clear(): boolean {
    if (!this.shown) return false;
    this.shown = null;
    this.mesh.visible = false;
    this.swap(new THREE.BufferGeometry());
    return true;
  }

  dispose(): void {
    this.clear();
    this.mesh.geometry.dispose();
    this.material.dispose();
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
