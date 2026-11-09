/**
 * Parser for the binary edge/vertex topology record written by
 * `stokes.geometry.topology_transport` (ADR-0022 §15.2), and the projection helpers T-058 needs to
 * draw and pick edges and vertices.
 *
 * Layout, little-endian: a u32 header length, the JSON header (zero-padded to a multiple of four),
 * then every edge's polyline as f32[3 * points], in header order, with no separator. The header
 * carries `{version, part, basis, units: "m", scale, edges: [...], vertices: [...]}`; each edge has
 * `{id, length, centre, start, end, tangent, curve, points, pickable}` and each vertex
 * `{id, position, degree, edges, lengths}`. Every figure is SI metres, so nothing here converts
 * units — `scale` records what the *shape* was stored in and is informational.
 *
 * Like the mesh parser, the bytes come over the wire and are treated as data: the header length,
 * the point counts and the payload size are all checked before a typed array is made, so a hostile
 * or truncated record is an error rather than an over-allocation. A version this module does not
 * know is refused (the server regenerates it), matching §15.2's rule that an unknown version is
 * never handed to another decoder.
 *
 * **This module owns the one decoder.** The scene keeps the decoded record per part and asks this
 * for the polylines it draws and the screen-space tests of `topologypick.ts` consume.
 */

/** The one topology format version this module reads; §15.2 bumps it independently of mesh v2. */
export const TOPOLOGY_VERSION = 3; // 2 each edge's face ids (T-124a); 3 face areas and solid volumes

/** A decoded edge: its id, the figures the fingerprints use, and its polyline in metres. */
export interface TopoEdge {
  id: number;
  /** True B-rep length, metres (never the polyline length). */
  length: number;
  /** Centre of mass of the curve, metres. */
  centre: [number, number, number];
  start: [number, number, number];
  end: [number, number, number];
  /** Unit tangent at mid-parameter; all zero when the curve has none. */
  tangent: [number, number, number];
  /** OCCT curve type name, or "" when unavailable. */
  curve: string;
  /** The sampled polyline, metres: `(points, 3)` flattened, always a multiple of three. */
  points: Float32Array;
  /** Number of points in `points` (its length is three times this). */
  pointsCount: number;
  /** False for a degenerate edge: reported, never picked (§15.2). */
  pickable: boolean;
  /**
   * The face ids this edge borders, in the mesh's numbering (T-124a). One for a boundary edge, two
   * for an interior one. Empty only for a record the reader refused, which cannot happen for a
   * version 2 payload.
   */
  faces: number[];
}

/** A decoded vertex: its id, position and local incidence. */
export interface TopoVertex {
  id: number;
  position: [number, number, number];
  /** How many edges meet here; `edges.length` equals it. */
  degree: number;
  /** The incident edge ids, sorted. */
  edges: number[];
  /** Their lengths, in the same order. */
  lengths: number[];
}

/** One part's decoded topology record. */
export interface PartTopology {
  /** The part node id this record belongs to. */
  part: string;
  /** The B-rep version the ids refer to; compared against a tag's basis to detect staleness. */
  basis: string;
  /** Metres per unit of the *shape* (informational: every figure above is already metres). */
  scale: number;
  edges: TopoEdge[];
  vertices: TopoVertex[];
  /**
   * Per-face areas, by face id (v3). Empty for a shape whose faces could not be measured.
   *
   * Carried here rather than summed from the display triangles because §15.1 wants a model figure:
   * a triangle sum changes with the mesh setting, so the same face would report different areas.
   */
  faceSizes: TopoFaceSize[];
  /** The part's solid bodies, measured (v3); empty for a shape with no solid. */
  solids: TopoSolid[];
}

/** One face's measured size, in the mesh's face numbering (v3). */
export interface TopoFaceSize {
  /** Face id, matching the mesh's face ids and a tag member's. */
  id: number;
  /** True B-rep area, square metres (never the triangles' sum: that would follow mesh quality). */
  area: number;
  /** Area-weighted centre of the surface, metres. */
  centroid: [number, number, number];
}

/**
 * One solid body's measured size (v3).
 *
 * A part can hold several bodies, so this is a list entry rather than the part's single volume.
 */
export interface TopoSolid {
  /** Enclosed volume, cubic metres. */
  volume: number;
  /** Centre of mass, metres. */
  centroid: [number, number, number];
  /** Total surface area of the body's own faces, square metres. */
  area: number;
}

/** Raised when a record cannot be read; the caller treats it as "no topology" and shows a state. */
export class TopologyFormatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TopologyFormatError";
  }
}

/** The largest header this module will accept, matching the server's cap (1 MiB). */
export const MAX_HEADER_BYTES = 1 << 20;
/** The largest number of points one edge may declare, matching the server's cap. */
export const MAX_POINTS_PER_EDGE = 4096;

function isInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

function triple(value: unknown, where: string): [number, number, number] {
  if (!Array.isArray(value) || value.length !== 3) {
    throw new TopologyFormatError(`topology ${where} must be 3 numbers`);
  }
  for (const v of value) {
    if (typeof v !== "number" || !Number.isFinite(v)) {
      throw new TopologyFormatError(`topology ${where} must be finite numbers`);
    }
  }
  return [value[0] as number, value[1] as number, value[2] as number];
}

function numberAt(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TopologyFormatError(`topology ${where} must be a finite number`);
  }
  return value;
}

/**
 * Parse a topology payload. Throws `TopologyFormatError` for anything malformed; the caller shows
 * "no edges" rather than drawing a partial record.
 */
/**
 * An edge's face ids from the parsed header (T-124a), refusing anything malformed.
 *
 * The Python decoder already range-checks these, so a payload that passes it is trustworthy; the
 * client still validates its own read, because the transport is untrusted at every boundary
 * (`docs/engineering/security.md`) and a face id that is not a small non-negative integer would
 * silently match no hidden face and make a hidden edge pickable.
 */
function readFaceIds(value: unknown): number[] {
  if (!Array.isArray(value)) {
    throw new TopologyFormatError("a topology edge's faces must be an array");
  }
  const out: number[] = [];
  for (const item of value) {
    if (typeof item !== "number" || !Number.isInteger(item) || item < 0) {
      throw new TopologyFormatError("a topology edge's face id must be a non-negative integer");
    }
    out.push(item);
  }
  return out;
}

export function parseTopology(buffer: ArrayBuffer): PartTopology {
  const view = new DataView(buffer);
  if (buffer.byteLength < 4) throw new TopologyFormatError("topology payload is too short");
  const headerLength = view.getUint32(0, true);
  if (headerLength < 2 || headerLength % 4 || 4 + headerLength > buffer.byteLength) {
    throw new TopologyFormatError("topology header length is invalid");
  }
  if (headerLength > MAX_HEADER_BYTES) throw new TopologyFormatError("topology header is too large");

  let header: unknown;
  try {
    const text = new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength)).replace(/\0+$/, "");
    header = JSON.parse(text);
  } catch {
    throw new TopologyFormatError("topology header is not JSON");
  }
  if (typeof header !== "object" || header === null || Array.isArray(header)) {
    throw new TopologyFormatError("topology header must be an object");
  }
  const h = header as Record<string, unknown>;
  if (h.version !== TOPOLOGY_VERSION) {
    throw new TopologyFormatError(`unknown topology version ${String(h.version)}`);
  }
  if (h.units !== "m") throw new TopologyFormatError("topology units must be metres");
  const part = h.part;
  const basis = h.basis;
  if (typeof part !== "string" || part === "") {
    throw new TopologyFormatError("topology part must be a non-empty string");
  }
  if (typeof basis !== "string") throw new TopologyFormatError("topology basis must be a string");
  const scale = numberAt(h.scale, "scale");
  if (scale <= 0) throw new TopologyFormatError("topology scale must be positive");

  const edgesRaw = h.edges;
  const verticesRaw = h.vertices;
  if (!Array.isArray(edgesRaw) || !Array.isArray(verticesRaw)) {
    throw new TopologyFormatError("topology edges and vertices must be lists");
  }
  const facesTotal = isInt(h.faces) && (h.faces as number) >= 0 ? (h.faces as number) : 0;
  const faceSizesRaw = h.faceSizes;
  if (!Array.isArray(faceSizesRaw) || faceSizesRaw.length > facesTotal) {
    throw new TopologyFormatError("topology face sizes must be a list no longer than the face count");
  }
  const solidsRaw = h.solids;
  if (!Array.isArray(solidsRaw)) throw new TopologyFormatError("topology solids must be a list");

  // Validate every declared point count before slicing a single buffer, then require the frames to
  // fill the payload exactly: a short or over-long record is an error, not a partial read.
  const counts: number[] = [];
  for (const item of edgesRaw) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new TopologyFormatError("topology edge must be an object");
    }
    const points = (item as Record<string, unknown>).points;
    if (!isInt(points) || points < 0 || points > MAX_POINTS_PER_EDGE) {
      throw new TopologyFormatError("topology edge points out of range");
    }
    counts.push(points);
  }
  const start = 4 + headerLength;
  const total = counts.reduce((sum, n) => sum + n, 0);
  if (start + total * 12 !== buffer.byteLength) {
    throw new TopologyFormatError("topology point frames do not fill the payload");
  }

  const edges: TopoEdge[] = [];
  let offset = start;
  for (let index = 0; index < edgesRaw.length; index++) {
    const item = edgesRaw[index] as Record<string, unknown>;
    const points = counts[index];
    if (item.id !== index) throw new TopologyFormatError(`topology edge id must be ${index}`);
    const flattened = new Float32Array(buffer.slice(offset, offset + points * 12));
    offset += points * 12;
    const pickable = item.pickable;
    edges.push({
      id: index,
      length: numberAt(item.length, "edge length"),
      centre: triple(item.centre, "edge centre"),
      start: triple(item.start, "edge start"),
      end: triple(item.end, "edge end"),
      tangent: triple(item.tangent, "edge tangent"),
      curve: typeof item.curve === "string" ? item.curve : "",
      points: flattened,
      pointsCount: points,
      pickable: typeof pickable === "boolean" ? pickable : true,
      faces: readFaceIds(item.faces),
    });
  }

  const vertices: TopoVertex[] = [];
  for (let index = 0; index < verticesRaw.length; index++) {
    const item = verticesRaw[index];
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new TopologyFormatError("topology vertex must be an object");
    }
    const v = item as Record<string, unknown>;
    if (v.id !== index) throw new TopologyFormatError(`topology vertex id must be ${index}`);
    const ids = v.edges;
    const lengths = v.lengths;
    if (!Array.isArray(ids) || !Array.isArray(lengths) || ids.length !== lengths.length) {
      throw new TopologyFormatError("topology vertex edges and lengths must have equal length");
    }
    const degree = v.degree;
    if (!isInt(degree) || degree !== ids.length) {
      throw new TopologyFormatError("topology vertex degree must equal its edge count");
    }
    vertices.push({
      id: index,
      position: triple(v.position, "vertex position"),
      degree,
      edges: ids.map((e) => numberAt(e, "vertex edge id")),
      lengths: lengths.map((l) => numberAt(l, "vertex edge length")),
    });
  }

  const faceSizes: TopoFaceSize[] = [];
  for (const item of faceSizesRaw) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new TopologyFormatError("topology face size must be an object");
    }
    const f = item as Record<string, unknown>;
    const id = f.id;
    if (!isInt(id) || id < 0 || id >= facesTotal) {
      throw new TopologyFormatError("topology face id out of range");
    }
    // A NaN area would poison every sum without ever comparing unequal, so it is refused here
    // rather than reaching the readout as "NaN m²".
    const area = numberAt(f.area, "face area");
    if (!(area >= 0) || !Number.isFinite(area)) {
      throw new TopologyFormatError("topology face area must be a finite, non-negative number");
    }
    faceSizes.push({ id, area, centroid: triple(f.centroid, "face centroid") });
  }

  const solids: TopoSolid[] = [];
  for (const item of solidsRaw) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new TopologyFormatError("topology solid must be an object");
    }
    const s = item as Record<string, unknown>;
    const volume = numberAt(s.volume, "solid volume");
    const area = numberAt(s.area, "solid area");
    if (!(volume >= 0) || !Number.isFinite(volume)) {
      throw new TopologyFormatError("topology solid volume must be finite and non-negative");
    }
    if (!(area >= 0) || !Number.isFinite(area)) {
      throw new TopologyFormatError("topology solid area must be finite and non-negative");
    }
    solids.push({ volume, area, centroid: triple(s.centroid, "solid centroid") });
  }

  return { part, basis, scale, edges, vertices, faceSizes, solids };
}

/** One projected polyline point: device pixels plus the NDC depth `Projected` in topologypick.ts wants. */
export interface Point3 {
  x: number;
  y: number;
  z: number;
}

/** A 4x4 matrix in column-major order, the layout three.js uses (`Matrix4.elements`). */
export type Mat4 = ArrayLike<number>;

/** One projected point: device pixels plus depth, the shape `topologypick.ts` consumes. */
export interface ScreenPoint {
  x: number;
  y: number;
  depth: number;
}

/**
 * Project a world point to device pixels through a view-projection matrix and a viewport.
 *
 * `width`/`height` are in **device** pixels (CSS pixels times the device pixel ratio), so the
 * tolerance the hit tests apply is in the same units the canvas draws in — §15.3's
 * "device-pixel-aware tolerance". A point behind the camera has `w <= 0`; it is returned with
 * `depth` beyond the far plane so callers can reject it (`visible: false`).
 */
export function projectPoint(
  point: Point3,
  matrix: Mat4,
  width: number,
  height: number,
): ScreenPoint & { visible: boolean } {
  const m = matrix;
  const x = m[0] * point.x + m[4] * point.y + m[8] * point.z + m[12];
  const y = m[1] * point.x + m[5] * point.y + m[9] * point.z + m[13];
  const z = m[2] * point.x + m[6] * point.y + m[10] * point.z + m[14];
  const w = m[3] * point.x + m[7] * point.y + m[11] * point.z + m[15];
  if (!(w > 0)) {
    return { x: 0, y: 0, depth: Number.POSITIVE_INFINITY, visible: false };
  }
  const ndcX = x / w;
  const ndcY = y / w;
  return {
    x: ((ndcX + 1) / 2) * width,
    y: ((1 - ndcY) / 2) * height,
    depth: z / w,
    visible: true,
  };
}

/**
 * An edge's polyline as screen **runs**, in device pixels, clipped at the near plane.
 *
 * Two things this must get right, and the first version got the second wrong:
 *
 * 1. **A point behind the camera has no screen position.** `projectPoint` returns `(0, 0)` for it,
 *    which is the viewport's *top-left corner*; pushing that produced a phantom segment from the
 *    corner to the visible end, and it could win a pick near the corner (A1).
 * 2. **An edge may leave the frustum and re-enter.** Clipping by pushing the crossing "outward from
 *    the screen centre" (the first attempt) invented a segment joining the two visible runs and the
 *    hit test then treated it as part of the edge. The correct shape is a list of *runs*: the visible
 *    portions only, each a chain of screen points, with no point invented at all.
 *
 * The clipping is done in **clip space** against `w > 0`, which is the near plane for the projection
 * this renderer uses, and the crossing point is interpolated **along the segment** — the only
 * defensible choice, since interpolating in screen space is where the made-up geometry came from.
 * A crossing has `w = 0` and so cannot be projected by `projectPoint`; it is placed by interpolating
 * the two endpoints' *screen* positions by the same parameter, which is exact for the segment's
 * direction and keeps the run's ends on the line the eye sees.
 */
/**
 * A pointer event's position in **canvas-relative CSS pixels** (A5).
 *
 * This is the value `SceneManager.topologyAt` takes: it multiplies by the device pixel ratio itself,
 * in exactly one place. Extracted as a pure function because the defect it guards against was a
 * *second* multiply at the call site — the hover path passed `(clientX - left) * dpr`, so on a 1.25x
 * display it sampled 1.25x too far from the origin and missed the edge under the pointer. A rule that
 * cannot be stated without a `WebGLRenderer` is a rule no test can hold; this one can.
 */
export function canvasCssPoint(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number },
): { x: number; y: number } {
  return { x: clientX - rect.left, y: clientY - rect.top };
}

/**
 * How far in front of the near plane a clipped crossing is placed, in clip-space `w`.
 *
 * A crossing lies at `w = 0` exactly, where the projection is infinite, so it is placed a hair
 * inside instead: the projected point is then far outside the viewport but on the right ray, which
 * is all a clipped run needs. Small enough that the point is beyond any plausible viewport, large
 * enough that `w` stays positive and the projection stays finite.
 */
const W_EPS = 1e-4;

export function projectPolyline(
  points: Float32Array,
  count: number,
  matrix: Mat4,
  width: number,
  height: number,
): { runs: ScreenPoint[][]; visible: boolean } {
  const runs: ScreenPoint[][] = [];
  const world = (index: number): Point3 => ({
    x: points[index * 3],
    y: points[index * 3 + 1],
    z: points[index * 3 + 2],
  });
  const wAt = (index: number): number => {
    const m = matrix;
    return (
      m[3] * points[index * 3] + m[7] * points[index * 3 + 1] + m[11] * points[index * 3 + 2] + m[15]
    );
  };
  const screen = (index: number): ScreenPoint => {
    const p = projectPoint(world(index), matrix, width, height);
    return { x: p.x, y: p.y, depth: p.depth };
  };

  /**
   * Where the segment `from` → `to` crosses the near plane, projected.
   *
   * The crossing is found in **world space** by solving `w(t) = W_EPS` and then projected, because a
   * point with `w = 0` has no finite projection at all (it is at infinity) and `projectPoint` of a
   * behind point returns the `(0, 0)` corner — the very value that caused A1. `W_EPS` is a hair in
   * front of the near plane, so the crossing is a real, if far off-screen, point in the correct
   * direction; anything within the tolerance of it is genuinely near the edge of the view.
   */
  const crossing = (from: number, to: number): ScreenPoint => {
    const wFrom = wAt(from);
    const wTo = wAt(to);
    const t = (wFrom - W_EPS) / (wFrom - wTo);
    const a = world(from);
    const b = world(to);
    const p = projectPoint(
      { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t },
      matrix,
      width,
      height,
    );
    return { x: p.x, y: p.y, depth: p.depth };
  };

  let run: ScreenPoint[] = [];
  const closeRun = (): void => {
    // Two points is the shortest drawable piece; a lone point is not a segment.
    if (run.length >= 2) runs.push(run);
    run = [];
  };

  for (let i = 0; i < count; i++) {
    const front = wAt(i) > 0;
    if (i === 0) {
      if (front) run.push(screen(0));
      continue;
    }
    const wasFront = wAt(i - 1) > 0;
    if (wasFront && front) {
      run.push(screen(i));
    } else if (wasFront && !front) {
      // Leaving the frustum: the run ends at the crossing, not at a corner.
      run.push(crossing(i - 1, i));
      closeRun();
    } else if (!wasFront && front) {
      // Re-entering: a NEW run begins at the crossing, and must not be joined to the previous one.
      run.push(crossing(i - 1, i));
      run.push(screen(i));
    }
    // Behind to behind: contributes nothing, and a run is already closed.
  }
  closeRun();
  return { runs, visible: runs.length > 0 };
}

/**
 * The same runs as one flat list, for a caller that only needs the points (a bounds check, a
 * readout). **Do not use this for hit testing**: joining the runs is exactly the fabricated segment
 * A1 is about.
 */
export function flattenRuns(runs: readonly ScreenPoint[][]): ScreenPoint[] {
  return runs.flat();
}
