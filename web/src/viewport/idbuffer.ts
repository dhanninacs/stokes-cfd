/**
 * The id buffer (T-032, ADR-0021 3.3 and 3.4): one offscreen RGBA8 pass that names the part and the
 * face behind every pixel, so a box or a lasso over thousands of parts is resolved by a single
 * read-back instead of a raycast per part.
 *
 * The pass draws the part layer and nothing else: it renders with a camera whose layer mask holds
 * `PART_LAYER` alone, so the face highlight, the selection overlay, the ground, the grid, the contact
 * shadow and the view cube (and, when it lands, T-046's glyph layer) stay out of it. Hidden and
 * isolated-away parts are already invisible to it — a hidden part's mesh and its batch instance are
 * hidden, and the pass reads what three draws — while translucent parts are drawn like any other.
 *
 * A pixel is `R,G` = the part index (low byte first) and `B,A` = the face id, both 16 bit. A part
 * index of 0 is the clear value, so the scene numbers its parts from 1. The face id is a local,
 * per-patch ordinal: the transport's face ids start at a group's `faceBase` and are routinely larger
 * than 16 bit, so the per-vertex `faceId` attribute carries the ordinal and the scene keeps the
 * transport id of each (`geometry.ts` `groupFaceIds`). 0xffff is "no face": the sample part has none,
 * and a patch past 65,535 faces loses face picking above that ordinal.
 *
 * The material is a `ShaderMaterial` (not a `MeshBasicMaterial` with `onBeforeCompile`): three
 * uploads a shared override material's uniforms once per `render`, so a uniform written per object in
 * `onBeforeRender` would be stale for every object after the first. A `ShaderMaterial` is the one
 * material three re-uploads on demand — `uniformsNeedUpdate`, set per object in `onBeforeRender` —
 * and it still gets three's batching chunks, which is what makes one material serve both a part's own
 * mesh and a `BatchedMesh`. An own mesh sets `uPartIndex`; a batch is named by `uBatchParts`, a
 * texture of instance id to part index that `batch.ts` keeps beside the batch it belongs to.
 *
 * Rendering is on demand. A camera, a content or a visibility change marks the pass dirty and the
 * next pick or gesture end redraws it; a gesture end reads the rectangle (or the lasso's bounds) back
 * once, and a lasso tests each pixel of that rectangle against the path.
 */

import * as THREE from "three";
import { FACE_ID_ATTRIBUTE, FACE_ID_NONE } from "./geometry";

export { FACE_ID_ATTRIBUTE, FACE_ID_NONE } from "./geometry";

/** A part index of 0: the render target's clear value, so no part is named. */
export const PART_INDEX_NONE = 0;

/** One id-buffer pixel's bytes, low byte first, at `at`: R,G the part index and B,A the face id. */
export function packIdBytes(partIndex: number, faceId: number, bytes: Uint8Array, at: number): void {
  bytes[at] = partIndex & 0xff;
  bytes[at + 1] = (partIndex >>> 8) & 0xff;
  bytes[at + 2] = faceId & 0xff;
  bytes[at + 3] = (faceId >>> 8) & 0xff;
}

/** The part index and face id of the pixel at `at` (see `packIdBytes`). */
export function unpackIdBytes(bytes: Uint8Array, at: number): { part: number; face: number } {
  return { part: bytes[at] | (bytes[at + 1] << 8), face: bytes[at + 2] | (bytes[at + 3] << 8) };
}

/** A rectangle read back from the id buffer. `x` and `y` are the read rectangle's origin in the
 * render target's own pixels, `y` measured from the bottom as WebGL does. */
export interface IdPixels {
  x: number;
  y: number;
  width: number;
  height: number;
  bytes: Uint8Array;
}

/** What a mask found: the part indices it covers, and the face ordinals inside it per part index. */
export interface IdMask {
  parts: number[];
  faces: Map<number, number[]>;
}

/**
 * The part and face indices of the pixels `inside` accepts, each named once. `inside` takes the
 * render target's own pixel coordinates (see `IdPixels`), so a rectangle and a lasso differ only in
 * the test they pass in.
 */
export function scanIdPixels(pixels: IdPixels, inside: (x: number, y: number) => boolean): IdMask {
  const parts: number[] = [];
  const seenParts = new Set<number>();
  const faces = new Map<number, number[]>();
  const seenFaces = new Set<number>();
  const { bytes, width, height } = pixels;
  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) {
      const at = (row * width + col) * 4;
      const part = bytes[at] | (bytes[at + 1] << 8);
      if (part === PART_INDEX_NONE) continue;
      if (!inside(pixels.x + col, pixels.y + row)) continue;
      if (!seenParts.has(part)) {
        seenParts.add(part);
        parts.push(part);
      }
      const face = bytes[at + 2] | (bytes[at + 3] << 8);
      if (face === FACE_ID_NONE) continue;
      const key = part * 65536 + face;
      if (seenFaces.has(key)) continue;
      seenFaces.add(key);
      const list = faces.get(part);
      if (list) list.push(face);
      else faces.set(part, [face]);
    }
  }
  return { parts, faces };
}

/**
 * The x positions where the horizontal line at `y` crosses the polygon, sorted. An even-odd fill
 * reads them in pairs, which is the point-in-polygon test of the lasso done once per row rather than
 * once per pixel. `points` is a flat `[x0, y0, x1, y1, ...]` list; the polygon is closed.
 */
export function rowCrossings(points: readonly number[], y: number): number[] {
  const xs: number[] = [];
  const count = Math.floor(points.length / 2);
  for (let i = 0; i < count; i++) {
    const j = (i + 1) % count;
    const x0 = points[i * 2];
    const y0 = points[i * 2 + 1];
    const x1 = points[j * 2];
    const y1 = points[j * 2 + 1];
    // Half-open in y, so a vertex is counted by one of its two edges and never by both.
    if (y0 <= y && y1 > y) xs.push(x0 + ((y - y0) / (y1 - y0)) * (x1 - x0));
    else if (y1 <= y && y0 > y) xs.push(x0 + ((y - y0) / (y1 - y0)) * (x1 - x0));
  }
  return xs.sort((a, b) => a - b);
}

/** A point-in-polygon test (`rowCrossings`) with the rows it has already worked out kept. */
export function polygonMask(points: readonly number[]): (x: number, y: number) => boolean {
  const rows = new Map<number, number[]>();
  return (x, y) => {
    let xs = rows.get(y);
    if (xs === undefined) {
      xs = rowCrossings(points, y);
      rows.set(y, xs);
    }
    for (let i = 0; i + 1 < xs.length; i += 2) if (x >= xs[i] && x <= xs[i + 1]) return true;
    return false;
  };
}

/** A rectangle test in the same coordinates as the read-back. */
export function rectMask(x0: number, y0: number, x1: number, y1: number): (x: number, y: number) => boolean {
  const left = Math.min(x0, x1);
  const right = Math.max(x0, x1);
  const bottom = Math.min(y0, y1);
  const top = Math.max(y0, y1);
  return (x, y) => x >= left && x <= right && y >= bottom && y <= top;
}

/** The id material's vertex shader: the part index per object or per batch instance, the face ordinal per vertex. */
export const ID_VERTEX = `
#include <common>
// **Clipping support (Claude's defect 7c).** This material is a hand-written ShaderMaterial, so it
// does NOT get the renderer's clip planes for free: renderer.clippingPlanes reaches a material only
// through these chunks, which three.js injects into the materials it compiles itself. Without them the
// id pass drew straight through the cut, so a box or lasso selected faces the drawn frame had removed
// — measured on the cold plate cut through the middle: the renderer reported the plane (normal
// [1,0,0], constant -0.04) while the id buffer still returned hits across BOTH halves (left
// 1245px/7f, right 811px/4f) where the drawn frame showed the left half gone.
#include <clipping_planes_pars_vertex>
#include <batching_pars_vertex>
attribute float ${FACE_ID_ATTRIBUTE};
uniform float uPartIndex;
uniform sampler2D uBatchParts;
flat varying float vIdPart;
flat varying float vIdFace;
void main() {
	// project_vertex works from transformed, which begin_vertex declares from the position
	// attribute. Both are three's chunks, so the attribute plumbing stays three's business.
	#include <begin_vertex>
	#include <batching_vertex>
	#ifdef USE_BATCHING
		int instanceId = int( getIndirectIndex( gl_DrawID ) );
		int size = textureSize( uBatchParts, 0 ).x;
		float part = texelFetch( uBatchParts, ivec2( instanceId % size, instanceId / size ), 0 ).r;
	#else
		float part = uPartIndex;
	#endif
	vIdPart = part;
	vIdFace = ${FACE_ID_ATTRIBUTE};
	// **project_vertex, not a hand-rolled transform, because clipping_planes_vertex needs what it
	// leaves behind.** That chunk writes "vClipPosition = -mvPosition.xyz", and mvPosition must be in
	// VIEW space (project_vertex ends with "mvPosition = modelViewMatrix * mvPosition"). This shader
	// used to build mvPosition itself and never multiply by modelViewMatrix, so the clip plane was
	// tested against MODEL-space coordinates and the id pass clipped only by accident, when a plane's
	// sign happened to line up. Measured with the normal along +Z: the pick was unchanged at every
	// offset from 0 to +0.09 — the whole model, plane and all — while the same plane reversed
	// correctly emptied the buffer. project_vertex also carries the batching and instancing
	// transforms, so this is the only place the vertex position is built.
	#include <project_vertex>
	#include <clipping_planes_vertex>
}`;

/** The id material's fragment shader: R,G the part index and B,A the face id, low byte first. */
export const ID_FRAGMENT = `
flat varying float vIdPart;
flat varying float vIdFace;
// **Discard the clipped-away fragments, so the id buffer shows what is drawn (defect 7c).**
// The clipping_planes_fragment chunk is an "if (any(...)) discard;", which is what makes a box or
// lasso selection agree with the picture. The pars chunk must come first: it declares the uniforms
// and the helper the fragment chunk calls.
#include <clipping_planes_pars_fragment>
void main() {
	#include <clipping_planes_fragment>
	float part = vIdPart;
	float face = vIdFace;
	gl_FragColor = vec4(
		mod( part, 256.0 ) / 255.0,
		floor( part / 256.0 ) / 255.0,
		mod( face, 256.0 ) / 255.0,
		floor( face / 256.0 ) / 255.0
	);
}`;

/**
 * The offscreen pass: its render target, its material and the read-back. The scene numbers its parts,
 * sets `uPartIndex` on each own mesh and keeps `uBatchParts` up to date per batch (batch.ts); this
 * class only owns the GPU side.
 */
export class IdPass {
  /** The one material every part mesh and every batch draws with, as `scene.overrideMaterial`. */
  readonly material: THREE.ShaderMaterial;
  private target: THREE.WebGLRenderTarget;
  private width: number;
  private height: number;

  /** `partLayer` is the layer of the part meshes and of the batches (scene.ts `PART_LAYER`). */
  constructor(private readonly partLayer: number) {
    this.width = 1;
    this.height = 1;
    this.target = this.makeTarget(1, 1);
    this.material = new THREE.ShaderMaterial({
      name: "stokes:id",
      uniforms: {
        uPartIndex: { value: 0 },
        // A 1x1 black texel until a batch binds its own: a sampler must always name a texture.
        uBatchParts: { value: this.emptyParts() },
      },
      vertexShader: ID_VERTEX,
      fragmentShader: ID_FRAGMENT,
      side: THREE.FrontSide,
      depthTest: true,
      depthWrite: true,
      toneMapped: false,
      fog: false,
      // **`clipping: true` is REQUIRED, and it is the whole reason the chunks above did nothing.**
      // three binds the `clippingPlanes` uniform only when the material is not a ShaderMaterial, or
      // when it explicitly opts in (`WebGLRenderer`: `if ((!material.isShaderMaterial &&
      // !material.isRawShaderMaterial) || material.clipping === true) uniforms.clippingPlanes = ...`).
      // This material IS a ShaderMaterial, so without this flag the compiled shader had the clipping
      // code and the `clippingPlanes` array was never uploaded — every plane read as zero, so nothing
      // was ever discarded. Measured: the shader compiled with `vClipPosition`, `clippingPlanes[
      // NUM_CLIPPING_PLANES ]` and `discard` all present, and the id buffer was still byte-identical
      // across the cut. A compiled shader with no uniforms looks exactly like a working one.
      clipping: true,
    });
  }

  /** The part index of the object about to be drawn, for a part's own mesh. */
  setPartIndex(index: number): void {
    this.material.uniforms.uPartIndex.value = index;
  }

  /** The instance id to part index map of the batch about to be drawn (batch.ts). */
  setBatchParts(texture: THREE.Texture): void {
    this.material.uniforms.uBatchParts.value = texture;
  }

  /**
   * Re-uploads the material's uniforms for the object about to be drawn. Three uploads a shared
   * material's uniforms once per `render`, so every object of the pass asks for its own upload here.
   */
  refreshUniforms(): void {
    this.material.uniformsNeedUpdate = true;
  }

  /** The target is the drawing buffer's size, so a read-back is in the pixels actually drawn. */
  setSize(width: number, height: number): void {
    const w = Math.max(1, Math.floor(width));
    const h = Math.max(1, Math.floor(height));
    if (w === this.width && h === this.height) return;
    this.width = w;
    this.height = h;
    this.target.dispose();
    this.target = this.makeTarget(w, h);
  }

  get size(): { width: number; height: number } {
    return { width: this.width, height: this.height };
  }

  /**
   * Draws the part layer into the target. The camera's layer mask is narrowed to `partLayer` for the
   * duration, which is what leaves the overlays and the glyph layer out; the clear is the renderer's
   * own (black, alpha 0), so a pixel no part covers decodes as part index 0.
   */
  render(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera): void {
    const previousTarget = renderer.getRenderTarget();
    const previousOverride = scene.overrideMaterial;
    const previousMask = camera.layers.mask;
    renderer.setRenderTarget(this.target);
    renderer.clear(true, true, false);
    scene.overrideMaterial = this.material;
    camera.layers.set(this.partLayer);
    renderer.render(scene, camera);
    camera.layers.mask = previousMask;
    scene.overrideMaterial = previousOverride;
    renderer.setRenderTarget(previousTarget);
  }

  /** Reads a rectangle of the target back; `x` and `y` are its pixels, `y` from the bottom. */
  read(renderer: THREE.WebGLRenderer, x: number, y: number, width: number, height: number): IdPixels {
    const rect = this.clip(x, y, width, height);
    const bytes = new Uint8Array(rect.width * rect.height * 4);
    renderer.readRenderTargetPixels(this.target, rect.x, rect.y, rect.width, rect.height, bytes);
    return { ...rect, bytes };
  }

  /** Reads one pixel of the target; null when it lies outside it. */
  readPixel(renderer: THREE.WebGLRenderer, x: number, y: number): { part: number; face: number } | null {
    if (x < 0 || y < 0 || x >= this.width || y >= this.height) return null;
    const bytes = new Uint8Array(4);
    renderer.readRenderTargetPixels(this.target, x, y, 1, 1, bytes);
    return unpackIdBytes(bytes, 0);
  }

  dispose(): void {
    this.target.dispose();
    const empty = this.material.uniforms.uBatchParts.value as THREE.Texture | null;
    empty?.dispose();
    this.material.dispose();
  }

  private makeTarget(width: number, height: number): THREE.WebGLRenderTarget {
    return new THREE.WebGLRenderTarget(width, height, {
      format: THREE.RGBAFormat,
      type: THREE.UnsignedByteType,
      minFilter: THREE.NearestFilter,
      magFilter: THREE.NearestFilter,
      depthBuffer: true,
      stencilBuffer: false,
      generateMipmaps: false,
      samples: 0,
    });
  }

  private emptyParts(): THREE.DataTexture {
    const texture = new THREE.DataTexture(new Float32Array([0]), 1, 1, THREE.RedFormat, THREE.FloatType);
    texture.needsUpdate = true;
    return texture;
  }

  /** The part of a read rectangle that lies inside the target. */
  private clip(x: number, y: number, width: number, height: number): { x: number; y: number; width: number; height: number } {
    const x0 = Math.max(0, Math.min(this.width, Math.floor(x)));
    const y0 = Math.max(0, Math.min(this.height, Math.floor(y)));
    const x1 = Math.max(x0, Math.min(this.width, Math.ceil(x + width)));
    const y1 = Math.max(y0, Math.min(this.height, Math.ceil(y + height)));
    return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
  }
}
