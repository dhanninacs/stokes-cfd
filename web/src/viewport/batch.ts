/**
 * Batched drawing for many-part scenes (ADR-0019): one THREE.BatchedMesh per look, so thousands of
 * parts cost a few draw calls instead of one each.
 *
 * A batch draws the parts of one kind in the material `createLook` makes for that kind (metalness,
 * clearcoat, double-sided, the polygon offset and the translucency of a part drawn on its own), on a
 * white base: each part's colour reaches the GPU per instance, converted from sRGB to linear once,
 * here, and multiplies the material's. So a batched part looks as it would on its own, and a new
 * colour is one texel write. Only a recipe's own emissive tint (`hasOwnTint`, the boundary patches)
 * cannot be per instance: such kinds are batched per colour, and a colour change moves the part to
 * the batch of its new colour. Tag colours are per vertex (the `tint` attribute, materials.ts): the
 * scene writes them into the part's geometry and `setTint` copies them into the batch.
 *
 * Opacity (ADR-0021 4.4) cannot be per instance either: three would have to sort every instance per
 * frame. A part's opacity bucket (tenths, `opacityBucket`) is part of its batch's key below 1, so a
 * look has at most nine translucent batches beside its opaque one, and an opacity change moves the
 * part between them. A translucent batch draws its look's `glass` (transparent, no depth writes) and
 * keeps the opaque batches' draw list and sorting: no sort per frame, the parts of one bucket drawn
 * in the order of the last still view's sort. It stays off the occluder layer, so the selection
 * outline shows through it; the contact shadow, which draws the part layer, keeps it.
 *
 * The scene keeps each part's own mesh: picking, the face tint, the selection outline and the
 * highlight of a hovered or selected part use it. This class owns only the batches.
 *
 * Capacity: a BatchedMesh fixes its instance, vertex and index budgets when it is made. A batch starts
 * at what its own parts need (`reserve`, per key) or at a small minimum. When a part does not fit,
 * the space of removed parts is reclaimed in place (`optimize`) if that makes room; otherwise the batch
 * is rebuilt at twice the size. The rebuilt batch is filled from the parts' own geometries before it
 * replaces the old one, which keeps drawing if the fill fails. Rebuilds happen O(log n) times, so the
 * copying is amortised O(n).
 *
 * Draw lists: by default three culls every instance of a batch against the camera and sorts them, in
 * JavaScript, before each pass: 5 to 7 ms for 4,500 parts on the development laptop, paid again by
 * the outline's occluder pass. An opaque batch outside wireframe keeps one list of every instance
 * instead, which every pass and camera reuses until a part comes, goes or changes visibility; the
 * scene has it sorted front to back once the view is still (`setSorting`), so overdraw stays low
 * without a sort per frame while orbiting. Off-screen instances then cost vertex work only. A look
 * whose recipe is translucent (the boundary patches) needs its order per view, and a wireframe list
 * is laid out for the wire index (two indices per triangle side), so those cull and sort in every
 * pass as three does by default.
 */

import * as THREE from "three";
import { FACE_ID_ATTRIBUTE } from "./geometry";
import { createLook, disposeLook, hasOwnTint, OPAQUE_BUCKET, paintLook, setLookOpacity, TINT_ATTRIBUTE, type PartLook } from "./materials";

type Rgb = readonly [number, number, number];

/** A part's place in the batches, kept up to date when a rebuild or a colour change moves it. */
export interface BatchedPart {
  /**
   * The key of the batch that owns the instance. Ids mean something only together with their
   * batch: every batch has its own id space, and id 0 of one batch is another part than id 0 of the next.
   */
  batchKey: string;
  instanceId: number;
  geometryId: number;
}

interface Capacity {
  instances: number;
  vertices: number;
  indices: number;
}

interface Item {
  readonly handle: BatchedPart;
  /**
   * The part's own geometry, which a rebuilt batch is filled from. The scene owns it and frees it only
   * after `remove` or `replace` has let go of it; the batch holds a copy of its data.
   */
  geometry: THREE.BufferGeometry;
  readonly kind: string;
  /** The part's colour, sRGB 0..1. */
  srgb: Rgb;
  /** The part's opacity bucket (`opacityBucket`), part of its batch's key below OPAQUE_BUCKET. */
  bucket: number;
  /** What the instance is multiplied by: the colour in linear space, or white in a batch keyed by colour. */
  readonly linear: THREE.Color;
  visible: boolean;
  /**
   * The part index the id buffer names this part by (T-032, `PART_INDEX_NONE` for none). It reaches
   * the GPU as a texel of the batch's `parts` texture, at the item's instance id, so it costs a
   * texel write when a part comes, goes or is renumbered, never a re-upload of the mesh (ADR-0021 3.3).
   */
  partIndex: number;
  /** The vertices and indices the geometry takes up in the batch's buffers. */
  vertices: number;
  indices: number;
}

interface Batch {
  readonly key: string;
  readonly kind: string;
  /** The colour of a batch keyed by colour (a kind with its own tint), null otherwise. */
  readonly tint: Rgb | null;
  /** The opacity bucket its parts share; below OPAQUE_BUCKET the batch is translucent. */
  readonly bucket: number;
  mesh: THREE.BatchedMesh;
  capacity: Capacity;
  readonly look: PartLook;
  /**
   * Instance id to part index, one float per texel, square as three lays its own batch textures out
   * (T-032). The id material samples it with the instance id three's batching chunks hand it, so a
   * batch names the part under a pixel as exactly as a part's own mesh does.
   */
  parts: THREE.DataTexture;
  partsArray: Float32Array;
  /** The live parts in insertion order; a rebuilt batch re-adds them in this order. */
  readonly items: Set<Item>;
  /** Vertices and indices the live parts take up. The rest of what the buffers used is reclaimable. */
  vertices: number;
  indices: number;
  /** Parts came or went since the batch's bounds were computed. */
  moved: boolean;
}

/** Where a batch with no reservation starts: a few hundred kB, so a colour or kind with one part costs little. */
const MIN_CAPACITY: Capacity = { instances: 64, vertices: 1 << 14, indices: 3 << 14 };
/** Room a batch gets beyond what its parts need, so the next few parts do not rebuild it at once. */
const HEADROOM = 1.25;
const WHITE: Rgb = [1, 1, 1];
const BLACK = new THREE.Color(0, 0, 0);

function costOf(geometry: THREE.BufferGeometry): { vertices: number; indices: number } {
  const vertices = geometry.getAttribute("position")?.count ?? 0;
  return { vertices, indices: geometry.index?.count ?? 0 };
}

/** The batch of a part: its kind, its colour for a kind with its own tint, and its opacity bucket below 1. */
function keyFor(kind: string, srgb: Rgb, bucket: number): string {
  let key = kind;
  if (hasOwnTint(kind)) {
    const byte = (c: number) => Math.round(THREE.MathUtils.clamp(c, 0, 1) * 255);
    key += `|${((byte(srgb[0]) << 16) | (byte(srgb[1]) << 8) | byte(srgb[2])).toString(16).padStart(6, "0")}`;
  }
  // Only below 1, so an opaque part keeps the key, and the batch, it had before opacity existed.
  return bucket < OPAQUE_BUCKET ? `${key}|o${bucket}` : key;
}

/**
 * A geometry the batches can hold: indexed triangles with a position, a normal, the tag tint
 * (materials.ts) and the per-vertex face id the id buffer names a face with (T-032), and nothing
 * else, as the scene makes them. The first geometry of a batch decides its attributes, so they must
 * agree. `BatchedMesh` copies every attribute of a geometry when it takes it, so the id buffer's
 * face id reaches the batch without a copy of its own (ADR-0021 3.3).
 * An empty one stays out: `optimize` moves the vertex and index ranges of the parts together, which
 * holds only while every part takes up both.
 */
function batchable(geometry: THREE.BufferGeometry): boolean {
  const position = geometry.getAttribute("position");
  const normal = geometry.getAttribute("normal");
  const tint = geometry.getAttribute(TINT_ATTRIBUTE);
  const faceId = geometry.getAttribute(FACE_ID_ATTRIBUTE);
  const index = geometry.index;
  return (
    index !== null &&
    index.count > 0 &&
    position?.itemSize === 3 &&
    position.count > 0 &&
    normal?.itemSize === 3 &&
    tint?.itemSize === 4 &&
    tint.normalized &&
    faceId?.itemSize === 1 &&
    Object.keys(geometry.attributes).length === 4
  );
}

function grown(capacity: Capacity, factor: number): Capacity {
  return {
    instances: Math.ceil(capacity.instances * factor),
    vertices: Math.ceil(capacity.vertices * factor),
    indices: Math.ceil(capacity.indices * factor),
  };
}

function atLeast(a: Capacity, b: Capacity): Capacity {
  // A batch of exactly 65,535 vertices would get 16-bit indices from BatchedMesh and a 32-bit wireframe
  // index from the renderer (their tests differ by one), which draws the wireframe from the wrong offsets.
  const vertices = Math.max(a.vertices, b.vertices);
  return {
    instances: Math.max(a.instances, b.instances),
    vertices: vertices === 65535 ? 65536 : vertices,
    indices: Math.max(a.indices, b.indices),
  };
}

export class BatchedMeshPool {
  private readonly batches = new Map<string, Batch>();
  private readonly items = new Map<BatchedPart, Item>();
  /** Starting budgets of batches not made yet, by key (see `reserve`). */
  private readonly reserved = new Map<string, Capacity>();
  private wireframe = false;
  private isDark: boolean;
  /** A draw list changed since `takeOrderChange` was last asked (it is rebuilt unsorted). */
  private orderStale = true;
  /**
   * Called on the batch about to be drawn, with its instance id to part index texture, so the scene
   * can hand it to the id material for that draw and nowhere else (T-032, idbuffer.ts).
   */
  onBindParts: (parts: THREE.Texture) => void = () => undefined;

  /**
   * `layer` is enabled on every batch besides the default, as on the parts' own meshes, and
   * `occluderLayer` on the opaque ones only (see scene.ts).
   */
  constructor(
    private readonly scene: THREE.Scene,
    private readonly layer: number,
    private readonly occluderLayer: number,
    isDark: boolean,
  ) {
    this.isDark = isDark;
  }

  /**
   * Sizes the batches about to be made for `parts` to what those parts need, with some headroom. The
   * budgets are per key: a batch is never sized by the parts of another, so an assembly of many colours
   * or kinds does not reserve the whole assembly's size once per batch.
   */
  reserve(parts: Iterable<{ geometry: THREE.BufferGeometry; kind: string; color: Rgb; bucket: number }>): void {
    const totals = new Map<string, Capacity>();
    for (const part of parts) {
      const key = keyFor(part.kind, part.color, part.bucket);
      const cost = costOf(part.geometry);
      const total = totals.get(key) ?? { instances: 0, vertices: 0, indices: 0 };
      total.instances += 1;
      total.vertices += cost.vertices;
      total.indices += cost.indices;
      totals.set(key, total);
    }
    for (const [key, total] of totals) if (!this.batches.has(key)) this.reserved.set(key, total);
  }

  /**
   * Adds a part. Returns its handle, or null when the geometry is not one a batch can hold (then the
   * part stays its own mesh). Throws only on something unexpected from three.js, with the pool intact.
   */
  add(geometry: THREE.BufferGeometry, kind: string, srgb: Rgb, bucket: number, visible: boolean): BatchedPart | null {
    if (!batchable(geometry)) return null;
    const key = keyFor(kind, srgb, bucket);
    const cost = costOf(geometry);
    const batch = this.batches.get(key) ?? this.make(key, kind, srgb, bucket, cost);
    const handle: BatchedPart = { batchKey: key, instanceId: -1, geometryId: -1 };
    const item: Item = { handle, geometry, kind, srgb: [srgb[0], srgb[1], srgb[2]], bucket, linear: new THREE.Color(), visible, partIndex: 0, ...cost };
    try {
      this.place(batch, item);
    } catch (err) {
      if (batch.items.size === 0) this.drop(batch);
      throw err;
    }
    this.items.set(handle, item);
    return handle;
  }

  /**
   * Gives a part a new geometry (a fine mesh replacing the coarse one) and keeps its instance, so its
   * colour and visibility stay. The caller frees the old geometry afterwards.
   */
  replace(handle: BatchedPart, geometry: THREE.BufferGeometry): void {
    const item = this.items.get(handle);
    const batch = item ? this.batches.get(handle.batchKey) : undefined;
    if (!item || !batch) return;
    if (!batchable(geometry)) throw new Error("the new mesh is not indexed triangles with normals");
    const cost = costOf(geometry);
    const range = batch.mesh.getGeometryRangeAt(handle.geometryId);
    if (range && cost.vertices <= range.reservedVertexCount && cost.indices <= range.reservedIndexCount) {
      // It fits where the old one was: no new space, the reservation stays as it was.
      batch.mesh.setGeometryAt(handle.geometryId, geometry);
      item.geometry = geometry;
      this.moved(batch);
      return;
    }
    // Room first: a rebuild re-adds the old geometry, which is still the item's until the swap below.
    this.ensureRoom(batch, cost);
    const mesh = batch.mesh;
    const geometryId = mesh.addGeometry(geometry);
    mesh.setGeometryIdAt(handle.instanceId, geometryId);
    // No instance draws the old geometry any more, so deleting it takes nothing else with it.
    mesh.deleteGeometry(handle.geometryId);
    batch.vertices += cost.vertices - item.vertices;
    batch.indices += cost.indices - item.indices;
    handle.geometryId = geometryId;
    item.geometry = geometry;
    item.vertices = cost.vertices;
    item.indices = cost.indices;
    this.moved(batch);
  }

  /** A part's colour (sRGB 0..1): a texel write, or a move for a kind batched per colour. */
  setColor(handle: BatchedPart, srgb: Rgb): void {
    const item = this.items.get(handle);
    const batch = item ? this.batches.get(handle.batchKey) : undefined;
    if (!item || !batch) return;
    item.srgb = [srgb[0], srgb[1], srgb[2]];
    const key = keyFor(item.kind, item.srgb, item.bucket);
    if (key === batch.key) {
      this.tint(batch, item);
      batch.mesh.setColorAt(handle.instanceId, item.linear);
      return;
    }
    // The colour is part of the batch's look: the part moves to the batch of its new colour.
    this.move(batch, item, key);
  }

  /**
   * A part's opacity bucket (`opacityBucket`): the part moves to the batch of its new bucket, keeping
   * its colour and visibility. Throws only on something unexpected from three.js, as `add` does.
   */
  setOpacity(handle: BatchedPart, bucket: number): void {
    const item = this.items.get(handle);
    const batch = item ? this.batches.get(handle.batchKey) : undefined;
    if (!item || !batch || item.bucket === bucket) return;
    item.bucket = bucket;
    const key = keyFor(item.kind, item.srgb, bucket);
    if (key !== batch.key) this.move(batch, item, key);
  }

  /**
   * Copies a part's tag tint (materials.ts) from its own geometry into its batch, after the scene wrote
   * it there: only that attribute's range moves, and three merges the ranges of neighbouring parts
   * into one upload.
   */
  setTint(handle: BatchedPart): void {
    const item = this.items.get(handle);
    const batch = item ? this.batches.get(handle.batchKey) : undefined;
    if (!item || !batch) return;
    const source = item.geometry.getAttribute(TINT_ATTRIBUTE);
    const target = batch.mesh.geometry.getAttribute(TINT_ATTRIBUTE);
    if (!(source instanceof THREE.BufferAttribute) || !(target instanceof THREE.BufferAttribute)) return;
    const range = batch.mesh.getGeometryRangeAt(handle.geometryId);
    if (!range || source.count > range.reservedVertexCount) return;
    target.array.set(source.array, range.vertexStart * target.itemSize);
    target.addUpdateRange(range.vertexStart * target.itemSize, range.reservedVertexCount * target.itemSize);
    target.needsUpdate = true;
  }

  /**
   * The part index the id buffer names this part by (T-032). One texel write: the batch's texture is
   * already on the GPU, so renumbering thousands of parts costs a few hundred kB, not a re-upload of
   * their meshes.
   */
  setPartIndex(handle: BatchedPart, index: number): void {
    const item = this.items.get(handle);
    const batch = item ? this.batches.get(handle.batchKey) : undefined;
    if (!item || !batch) return;
    item.partIndex = index;
    this.writePartIndex(batch, handle.instanceId, index);
  }

  setVisible(handle: BatchedPart, visible: boolean): void {    const item = this.items.get(handle);
    const batch = item ? this.batches.get(handle.batchKey) : undefined;
    if (!item || !batch || item.visible === visible) return;
    item.visible = visible;
    batch.mesh.setVisibleAt(handle.instanceId, visible);
    this.orderStale = true;
  }

  /** Takes a part out, freeing its geometry slot; a batch left empty is freed whole. */
  remove(handle: BatchedPart): void {
    const item = this.items.get(handle);
    const batch = item ? this.batches.get(handle.batchKey) : undefined;
    this.items.delete(handle);
    if (!item || !batch) return;
    this.release(batch, item, handle.geometryId);
    handle.instanceId = -1;
    handle.geometryId = -1;
  }

  /** Wireframe view: every batch draws with its look's wire material instead of the surface. */
  setWireframe(on: boolean): void {
    if (on === this.wireframe) return;
    this.wireframe = on;
    for (const batch of this.batches.values()) {
      batch.mesh.material = on ? batch.look.wire : (batch.look.glass ?? batch.look.surface);
      this.listMode(batch.mesh, batch.look);
      // A kept list was laid out for the other index (see the module doc): it is made again.
      const first = batch.items.values().next().value;
      if (first) {
        batch.mesh.setVisibleAt(first.handle.instanceId, !first.visible);
        batch.mesh.setVisibleAt(first.handle.instanceId, first.visible);
      }
    }
    this.orderStale = true;
  }

  /**
   * Whether the next pass sorts the kept lists of opaque batches front to back for its camera (see
   * the module doc). The scene turns it on for the main pass of a frame with a still view and off
   * after it, so the other passes, and the frames of an orbit, reuse that order.
   */
  setSorting(on: boolean): void {
    for (const batch of this.batches.values()) if (this.keepsList(batch.look)) batch.mesh.sortObjects = on;
  }

  /** True once after any draw list changed, which leaves it unsorted until the next sorted pass. */
  takeOrderChange(): boolean {
    const stale = this.orderStale;
    this.orderStale = false;
    return stale && this.batches.size > 0;
  }

  /** The wire colour depends on the appearance (see `wireBaseColor`). */
  setTheme(isDark: boolean): void {
    this.isDark = isDark;
    for (const batch of this.batches.values()) paintLook(batch.look, BLACK, isDark, 0);
  }

  /**
   * Brings the batches' own bounds up to date where parts came or went. The renderer orders objects by
   * them; culling does not use them (see `newMesh`).
   */
  refresh(): void {
    for (const batch of this.batches.values()) {
      if (!batch.moved) continue;
      batch.moved = false;
      batch.mesh.computeBoundingSphere();
    }
  }

  /** Removes every batch from the scene and frees its GPU resources. */
  clear(): void {
    for (const batch of this.batches.values()) this.free(batch);
    this.batches.clear();
    this.items.clear();
    this.reserved.clear();
  }

  get batchCount(): number {
    return this.batches.size;
  }

  /** Batches of translucent parts (an opacity bucket below 1). */
  get translucentCount(): number {
    let count = 0;
    for (const batch of this.batches.values()) if (batch.bucket < OPAQUE_BUCKET) count++;
    return count;
  }

  /** Instances drawn by the batches, over all of them. */
  get instanceCount(): number {
    let count = 0;
    for (const batch of this.batches.values()) count += batch.mesh.instanceCount;
    return count;
  }

  // ------------------------------------------------------------------ inside
  private make(key: string, kind: string, srgb: Rgb, bucket: number, first: { vertices: number; indices: number }): Batch {
    const tint = hasOwnTint(kind) ? ([srgb[0], srgb[1], srgb[2]] as const) : null;
    const reserved = this.reserved.get(key);
    this.reserved.delete(key);
    const capacity = atLeast(MIN_CAPACITY, reserved ? grown(reserved, HEADROOM) : { instances: 1, vertices: first.vertices * 2, indices: first.indices * 2 });
    // The kind's own look on a white base: the instance colour supplies the colour (see the module doc).
    // A kind tinted by its colour keeps that colour in the material and gets white instances. A
    // translucent batch draws the look's glass, the kind's surface at the bucket's opacity.
    const look = createLook(kind, tint ?? WHITE, this.isDark);
    setLookOpacity(look, bucket);
    paintLook(look, BLACK, this.isDark, 0);
    const { texture, array } = this.newParts(capacity.instances);
    const batch: Batch = {
      key,
      kind,
      tint,
      bucket,
      mesh: this.newMesh(key, capacity, look, texture),
      capacity,
      look,
      parts: texture,
      partsArray: array,
      items: new Set(),
      vertices: 0,
      indices: 0,
      moved: true,
    };
    this.scene.add(batch.mesh);
    this.batches.set(key, batch);
    return batch;
  }

  /**
   * The instance id to part index texture of a batch (T-032), laid out square as three lays its own
   * batch textures out, so the id material's `instanceId % size` / `instanceId / size` finds the
   * texel. One float per instance: 16 bit of part index is exact in a float.
   */
  private newParts(instances: number): { texture: THREE.DataTexture; array: Float32Array } {
    const size = Math.max(1, Math.ceil(Math.sqrt(instances)));
    const array = new Float32Array(size * size);
    const texture = new THREE.DataTexture(array, size, size, THREE.RedFormat, THREE.FloatType);
    texture.minFilter = THREE.NearestFilter;
    texture.magFilter = THREE.NearestFilter;
    texture.needsUpdate = true;
    return { texture, array };
  }

  /** One texel of that texture: the part index of the instance `instanceId`, or 0 for none. */
  private writePartIndex(batch: Batch, instanceId: number, index: number): void {
    if (instanceId < 0 || instanceId >= batch.partsArray.length) return;
    batch.partsArray[instanceId] = index;
    batch.parts.needsUpdate = true;
  }

  private newMesh(key: string, capacity: Capacity, look: PartLook, parts: THREE.DataTexture): THREE.BatchedMesh {
    const surface = look.glass ?? look.surface;
    const mesh = new THREE.BatchedMesh(capacity.instances, capacity.vertices, capacity.indices, this.wireframe ? look.wire : surface);
    mesh.name = `batch:${key}`;
    this.listMode(mesh, look);
    // The batch's own bounds are computed once, lazily, and go stale as parts arrive: culling the whole
    // batch by them could drop parts added later.
    mesh.frustumCulled = false;
    mesh.layers.enable(this.layer);
    // A translucent batch hides nothing from the selection outline (see the module doc).
    if (look.glass === null) mesh.layers.enable(this.occluderLayer);
    // `BatchedMesh` has an `onBeforeRender` of its own (it updates the multi-draw list); the id pass
    // rides on it rather than replacing it (T-032).
    const inner = mesh.onBeforeRender;
    mesh.onBeforeRender = (renderer, scene, camera, geometry, material, group) => {
      inner.call(mesh, renderer, scene, camera, geometry, material, group);
      this.onBindParts(parts);
    };
    return mesh;
  }

  /** An opaque look outside wireframe keeps one draw list; any other culls and sorts every pass. */
  private keepsList(look: PartLook): boolean {
    return !this.wireframe && !look.translucent;
  }

  private listMode(mesh: THREE.BatchedMesh, look: PartLook): void {
    const perPass = !this.keepsList(look);
    mesh.perObjectFrustumCulled = perPass;
    mesh.sortObjects = perPass;
  }

  private tint(batch: Batch, item: Item): void {
    const [r, g, b] = batch.tint ? WHITE : item.srgb;
    item.linear.setRGB(r, g, b, THREE.SRGBColorSpace);
  }

  /** Adds an item's geometry and instance to `batch`, making room first, and records where it went. */
  private place(batch: Batch, item: Item): void {
    const cost = costOf(item.geometry);
    this.ensureRoom(batch, cost);
    this.tint(batch, item);
    const mesh = batch.mesh;
    const geometryId = mesh.addGeometry(item.geometry);
    let instanceId: number;
    try {
      instanceId = mesh.addInstance(geometryId);
    } catch (err) {
      mesh.deleteGeometry(geometryId);
      throw err;
    }
    // addInstance has set an identity matrix; colour and visibility are the part's.
    mesh.setColorAt(instanceId, item.linear);
    mesh.setVisibleAt(instanceId, item.visible);
    item.handle.batchKey = batch.key;
    item.handle.geometryId = geometryId;
    item.handle.instanceId = instanceId;
    item.vertices = cost.vertices;
    item.indices = cost.indices;
    // The id buffer finds the part through the instance id (T-032); a move carries the index along.
    this.writePartIndex(batch, instanceId, item.partIndex);
    batch.items.add(item);
    batch.vertices += cost.vertices;
    batch.indices += cost.indices;
    this.moved(batch);
  }

  /**
   * Moves an item from `batch` to the batch of `key`, made if need be: a colour of a kind batched per
   * colour, or an opacity bucket. On an error from three.js the item stays where it was, and a batch
   * made for it alone goes again.
   */
  private move(batch: Batch, item: Item, key: string): void {
    // place() writes the new ids into the handle, so the old geometry id is taken first.
    const old = item.handle.geometryId;
    const existing = this.batches.get(key);
    const target = existing ?? this.make(key, item.kind, item.srgb, item.bucket, costOf(item.geometry));
    try {
      this.place(target, item);
    } catch (err) {
      if (!existing && target.items.size === 0) this.drop(target);
      throw err;
    }
    this.release(batch, item, old);
  }

  /** Takes an item's geometry (and with it its instance) out of `batch`; an empty batch goes. */
  private release(batch: Batch, item: Item, geometryId: number): void {
    if (batch.items.delete(item)) {
      batch.mesh.deleteGeometry(geometryId);
      batch.vertices -= item.vertices;
      batch.indices -= item.indices;
      this.moved(batch);
    }
    if (batch.items.size === 0) this.drop(batch);
  }

  /** Parts came or went: the bounds and the draw list follow. */
  private moved(batch: Batch): void {
    batch.moved = true;
    this.orderStale = true;
  }

  private drop(batch: Batch): void {
    this.free(batch);
    this.batches.delete(batch.key);
  }

  private free(batch: Batch): void {
    this.scene.remove(batch.mesh);
    batch.mesh.dispose();
    batch.parts.dispose();
    disposeLook(batch.look);
  }

  /**
   * Makes room in `batch` for one more instance of `need`. Only a real shortage gets here, measured
   * before the add rather than guessed from an exception: the reclaimable space of removed parts is
   * compacted in place when that is enough, else the batch grows.
   */
  private ensureRoom(batch: Batch, need: { vertices: number; indices: number }): void {
    const mesh = batch.mesh;
    const instance = mesh.instanceCount < mesh.maxInstanceCount;
    if (instance && need.vertices <= mesh.unusedVertexCount && need.indices <= mesh.unusedIndexCount) return;
    const { capacity } = batch;
    if (instance && batch.vertices + need.vertices <= capacity.vertices && batch.indices + need.indices <= capacity.indices) {
      mesh.optimize();
      if (need.vertices <= mesh.unusedVertexCount && need.indices <= mesh.unusedIndexCount) return;
    }
    this.grow(batch, need);
  }

  /**
   * Rebuilds `batch` bigger: at least twice its size, and enough for its parts and `need` with
   * headroom. The new BatchedMesh is filled completely before anything else changes; only then does it
   * take the old one's place in the scene, the parts learn their new ids, and the old one is freed. A
   * failure while filling leaves the old batch drawing and the ids untouched.
   */
  private grow(batch: Batch, need: { vertices: number; indices: number }): void {
    const live: Capacity = { instances: batch.items.size + 1, vertices: batch.vertices + need.vertices, indices: batch.indices + need.indices };
    const capacity = atLeast(grown(batch.capacity, 2), grown(live, HEADROOM));
    // The instance ids are laid out again, so the id buffer's texture is made again with them (T-032).
    const { texture, array } = this.newParts(capacity.instances);
    const mesh = this.newMesh(batch.key, capacity, batch.look, texture);
    const placed: { item: Item; geometryId: number; instanceId: number; vertices: number; indices: number }[] = [];
    try {
      for (const item of batch.items) {
        const geometryId = mesh.addGeometry(item.geometry);
        const instanceId = mesh.addInstance(geometryId);
        mesh.setColorAt(instanceId, item.linear);
        mesh.setVisibleAt(instanceId, item.visible);
        placed.push({ item, geometryId, instanceId, ...costOf(item.geometry) });
      }
    } catch (err) {
      mesh.dispose();
      throw err;
    }
    // One synchronous step, so no frame is drawn with both batches or with neither.
    const previous = batch.mesh;
    const previousParts = batch.parts;
    this.scene.add(mesh);
    this.scene.remove(previous);
    batch.mesh = mesh;
    batch.capacity = capacity;
    batch.parts = texture;
    batch.partsArray = array;
    batch.vertices = 0;
    batch.indices = 0;
    for (const { item, geometryId, instanceId, vertices, indices } of placed) {
      item.handle.geometryId = geometryId;
      item.handle.instanceId = instanceId;
      item.vertices = vertices;
      item.indices = indices;
      batch.vertices += vertices;
      batch.indices += indices;
      this.writePartIndex(batch, instanceId, item.partIndex);
    }
    this.moved(batch);
    // The look's materials carry over to the new batch, so only the old mesh's buffers are freed.
    previous.dispose();
    previousParts.dispose();
  }
}
