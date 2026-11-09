/**
 * Parser for the binary mesh transport written by stokes.geometry.transport, versions 1 and 2.
 *
 * Layout, little-endian: a u32 header length, the JSON header (zero-padded to a multiple of four),
 * then for every group in header order its positions f32[3N], normals f32[3N] and indices u32[3M],
 * and in version 2, when the group says `faceIds`, one face id per triangle u32[M]. A group's
 * `offset` counts from the end of the header to its first buffer.
 *
 * Version 1 is the sample part: groups with a name, kind, colour, counts and offset. Version 2 is
 * an imported part: the header adds `units` (of the positions, always metres), `bbox`, `faces` and
 * `source`, and each group adds `faceIds`, `faces`, `faceBase` (its face ids lie in
 * [faceBase, faceBase + faces)) and `quality` ("coarse" or "fine").
 *
 * The header and the buffers come over the wire, so they are data: every field is checked before a
 * typed array is made from it, and a buffer that does not fit its header is an error, not a guess.
 *
 * The per-vertex face attribute the id buffer names a face with (T-032) is built here too
 * (`groupFaceIds`), out of the transport's per-triangle ids.
 */

export interface GeometryGroupMeta {
  name: string;
  kind: string;
  color: [number, number, number];
  vertices: number;
  triangles: number;
  offset: number;
  /** B-rep faces in the group; 0 when the transport carries no face ids (the sample part). */
  faces: number;
  /** The group's face ids lie in [faceBase, faceBase + faces). */
  faceBase: number;
  /** "coarse" or "fine" for imported CAD, empty when the transport does not say. */
  quality: string;
  /** True when the group carries a face id for every triangle. */
  hasFaceIds: boolean;
}

export interface LoadedGroup extends GeometryGroupMeta {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  /** One face id per triangle; null when `hasFaceIds` is false. */
  faceIds: Uint32Array | null;
}

/** A vertex that belongs to no encodable face: the sample part's vertices, and a patch past 65,535 faces. */
export const FACE_ID_NONE = 0xffff;

/** The vertex attribute a group's face ordinals travel in, as `groupFaceIds` makes it (T-032). */
export const FACE_ID_ATTRIBUTE = "faceId";

/** What a group's per-vertex face attribute carries, and the way back to the transport's face ids. */
export interface GroupFaceIds {
  /** One face ordinal per vertex, aligned with the group's positions; `FACE_ID_NONE` where there is none. */
  attribute: Uint16Array;
  /** The transport face id of each ordinal, in ordinal order; empty without face ids. */
  faces: Uint32Array;
  /** True when the group has more faces than the attribute can name, so the ones past it are unnamed. */
  truncated: boolean;
}

/**
 * The per-vertex face attribute of a group (T-032), built from the transport's per-triangle face ids.
 *
 * The id buffer names a face in 16 bits, but a transport face id starts at the group's `faceBase`,
 * which is a whole file's count and routinely larger: so a vertex carries an ordinal local to its
 * group and the caller keeps `faces` to turn the ordinal back. The import welds vertices by face, so
 * the three vertices of a triangle always carry the same ordinal and no interpolation is involved
 * (`scene.ts` declares the attribute `flat` in the id material). Ordinals stop at 65,535: a face past
 * that keeps its part for body picking but has no face to name (`truncated`).
 */
export function groupFaceIds(group: LoadedGroup): GroupFaceIds {
  const attribute = new Uint16Array(group.positions.length / 3);
  const ids = group.faceIds;
  if (ids === null) {
    attribute.fill(FACE_ID_NONE);
    return { attribute, faces: new Uint32Array(0), truncated: false };
  }
  const ordinals = new Map<number, number>();
  const faces: number[] = [];
  let truncated = false;
  for (let t = 0; t < ids.length; t++) {
    const id = ids[t];
    let at = ordinals.get(id);
    if (at === undefined) {
      if (faces.length >= FACE_ID_NONE) {
        truncated = true;
        at = FACE_ID_NONE;
      } else {
        at = faces.length;
        ordinals.set(id, at);
        faces.push(id);
      }
    }
    attribute[group.indices[t * 3]] = at;
    attribute[group.indices[t * 3 + 1]] = at;
    attribute[group.indices[t * 3 + 2]] = at;
  }
  return { attribute, faces: Uint32Array.from(faces), truncated };
}

/**
 * What the store knows about a drawn group: its meta with the colour actually shown (a part's
 * colour parameter replaces the file's), the unique name the selection works with, and the part
 * node it belongs to, null for the sample part.
 */
export interface PatchMeta extends GeometryGroupMeta {
  part: string | null;
}

export interface GeometryBox {
  min: [number, number, number];
  max: [number, number, number];
}

/** The file a mesh was made from; `mtimeNs` is a nanosecond count too large for exact doubles. */
export interface GeometrySource {
  name: string;
  size: number;
  mtimeNs: number;
}

export interface ParsedGeometry {
  version: 1 | 2;
  /** Unit of the positions; the server always sends metres. */
  units: string;
  bbox: GeometryBox | null;
  /** B-rep faces over all groups; 0 for version 1. */
  faces: number;
  source: GeometrySource | null;
  groups: LoadedGroup[];
}

/** The header is a short JSON document; anything larger is not one of ours. */
const MAX_HEADER_BYTES = 1 << 24;
const MAX_UNITS_LENGTH = 32;
const GROUP_BYTES_PER_VERTEX = 24;
const TRIANGLE_BYTES = 12;
const FACE_ID_BYTES = 4;

type Dict = Record<string, unknown>;

function isDict(value: unknown): value is Dict {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isPoint(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3 && value.every((n) => typeof n === "number" && Number.isFinite(n));
}

/** A count that may be missing (then `fallback`), but must be a non-negative integer when present. */
function optionalCount(source: Dict, key: string, fallback: number, where: string): number {
  const value = source[key];
  if (value === undefined) return fallback;
  if (!isCount(value)) throw new Error(`${where}: ${key} must be a non-negative integer`);
  return value;
}

/** Checks one group of the header against the buffer; the header comes over the wire, so it is data. */
function checkGroup(value: unknown, version: 1 | 2, dataStart: number, byteLength: number): GeometryGroupMeta {
  if (!isDict(value) || typeof value.name !== "string" || typeof value.kind !== "string") {
    throw new Error("geometry group without a name or kind");
  }
  const name = value.name;
  const where = `geometry group ${name}`;
  const color = value.color;
  if (!Array.isArray(color) || color.length !== 3 || !color.every((c) => typeof c === "number" && c >= 0 && c <= 1)) {
    throw new Error(`${where}: colour must be three numbers from 0 to 1`);
  }
  const { vertices, triangles, offset } = value;
  if (!isCount(vertices) || !isCount(triangles) || !isCount(offset)) {
    throw new Error(`${where}: counts and offset must be non-negative integers`);
  }
  let faces = 0;
  let faceBase = 0;
  let quality = "";
  let hasFaceIds = false;
  if (version === 2) {
    const flag = value.faceIds;
    if (flag !== undefined && typeof flag !== "boolean") throw new Error(`${where}: faceIds must be true or false`);
    hasFaceIds = flag === true;
    faces = optionalCount(value, "faces", 0, where);
    faceBase = optionalCount(value, "faceBase", 0, where);
    const q = value.quality;
    if (q !== undefined && typeof q !== "string") throw new Error(`${where}: quality must be a string`);
    quality = q ?? "";
  }
  const base = dataStart + offset;
  const end = base + vertices * GROUP_BYTES_PER_VERTEX + triangles * (TRIANGLE_BYTES + (hasFaceIds ? FACE_ID_BYTES : 0));
  if (base % 4 !== 0 || end > byteLength) throw new Error(`${where}: buffers lie outside the data`);
  return { name, kind: value.kind, color: [color[0], color[1], color[2]], vertices, triangles, offset, faces, faceBase, quality, hasFaceIds };
}

/** An index past the vertices would make the GPU read garbage; one pass over the buffer rules it out. */
function checkIndices(g: GeometryGroupMeta, indices: Uint32Array): void {
  let largest = 0;
  for (let i = 0; i < indices.length; i++) if (indices[i] > largest) largest = indices[i];
  if (indices.length > 0 && largest >= g.vertices) throw new Error(`geometry group ${g.name}: an index points past the vertices`);
}

/** Every face id must lie in the group's range, or a lookup by face would find the wrong triangles. */
function checkFaceIds(g: GeometryGroupMeta, faceIds: Uint32Array): void {
  let lowest = 0xffffffff;
  let highest = 0;
  for (let i = 0; i < faceIds.length; i++) {
    const id = faceIds[i];
    if (id < lowest) lowest = id;
    if (id > highest) highest = id;
  }
  if (faceIds.length > 0 && (lowest < g.faceBase || highest >= g.faceBase + g.faces)) {
    throw new Error(`geometry group ${g.name}: a face id lies outside its range`);
  }
}

function checkBox(value: unknown): GeometryBox | null {
  if (value === undefined || value === null) return null;
  if (!isDict(value) || !isPoint(value.min) || !isPoint(value.max)) throw new Error("geometry bbox must hold a min and a max point");
  return { min: [value.min[0], value.min[1], value.min[2]], max: [value.max[0], value.max[1], value.max[2]] };
}

function checkSource(value: unknown): GeometrySource | null {
  if (value === undefined || value === null) return null;
  if (!isDict(value) || typeof value.name !== "string" || typeof value.size !== "number" || typeof value.mtime_ns !== "number") {
    throw new Error("geometry source must hold a name, a size and a modification time");
  }
  return { name: value.name, size: value.size, mtimeNs: value.mtime_ns };
}

export function parseGeometry(buffer: ArrayBuffer): ParsedGeometry {
  if (buffer.byteLength < 4) throw new Error("geometry buffer is too short");
  const view = new DataView(buffer);
  const headerLength = view.getUint32(0, true);
  if (headerLength > MAX_HEADER_BYTES || 4 + headerLength > buffer.byteLength) throw new Error("geometry header is truncated");
  const headerText = new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength)).replace(/\0+$/, "");
  let header: unknown;
  try {
    header = JSON.parse(headerText);
  } catch {
    throw new Error("geometry header is not JSON");
  }
  if (!isDict(header)) throw new Error("geometry header is not an object");
  const version = header.version;
  if (version !== 1 && version !== 2) throw new Error(`unsupported geometry version ${String(version)}`);
  if (!Array.isArray(header.groups)) throw new Error("geometry header lists no groups");

  let units = "m";
  let bbox: GeometryBox | null = null;
  let source: GeometrySource | null = null;
  let faces: number | null = null;
  if (version === 2) {
    if (header.units !== undefined) {
      if (typeof header.units !== "string" || header.units.length > MAX_UNITS_LENGTH) throw new Error("geometry units must be a short string");
      units = header.units;
    }
    bbox = checkBox(header.bbox);
    source = checkSource(header.source);
    if (header.faces !== undefined) faces = optionalCount(header, "faces", 0, "geometry header");
  }

  const dataStart = 4 + headerLength;
  const groups = header.groups.map((entry: unknown): LoadedGroup => {
    const g = checkGroup(entry, version, dataStart, buffer.byteLength);
    const base = dataStart + g.offset;
    const positions = new Float32Array(buffer, base, g.vertices * 3);
    const normals = new Float32Array(buffer, base + g.vertices * 12, g.vertices * 3);
    const indices = new Uint32Array(buffer, base + g.vertices * GROUP_BYTES_PER_VERTEX, g.triangles * 3);
    checkIndices(g, indices);
    let faceIds: Uint32Array | null = null;
    if (g.hasFaceIds) {
      faceIds = new Uint32Array(buffer, base + g.vertices * GROUP_BYTES_PER_VERTEX + g.triangles * TRIANGLE_BYTES, g.triangles);
      checkFaceIds(g, faceIds);
    }
    return { ...g, positions, normals, indices, faceIds };
  });
  return { version, units, bbox, faces: faces ?? groups.reduce((sum, g) => sum + g.faces, 0), source, groups };
}
