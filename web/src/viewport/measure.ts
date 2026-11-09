/**
 * The Measure tool's arithmetic: how far apart two points picked in the view are.
 *
 * The viewport had a scale (T-097) but no way to ask "how far apart are these two things": the scale
 * says how long a metre looks, not how long a feature is. This is the number behind the readout —
 * the straight-line distance between two surface points in world metres, with the per-axis
 * differences CAD users check a dimension with, and the midpoint an HTML label hangs at.
 *
 * **Why it is a module of its own.** The arithmetic is the part that can be wrong without anything
 * failing to draw: a swapped axis, a sign lost, a unit ladder step skipped, a degenerate pair that
 * prints `NaN m`. Keeping it here — plain `{x, y, z}` in, plain numbers out, no three.js, no scene,
 * no DOM — is what lets it be tested directly (measure.test.ts) instead of through a renderer.
 *
 * **View state, not model state.** Measuring mutates nothing: no API command, no journal record and
 * nothing for undo, exactly like the scale bar. It reads two world points the picker already had and
 * says how far apart they are.
 *
 * **Reuse.** Every length printed here goes through `formatLength` (scale.ts), the one place the
 * unit ladder (km / m / mm / µm, no centimetre) and the three-significant-figure rule live, so the
 * readout and the scale bar can never disagree about what a length looks like.
 */

import { formatArea, formatLength, formatVolume } from "./scale";



/** A point in world space, in metres. Plain numbers, so this module needs no renderer. */
export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/** One axis of a measurement: how much further along it the second point is than the first. */
export interface AxisDelta {
  /** The signed difference `b - a` on this axis, in metres. */
  metres: number;
  /** That difference with its sign and its unit (`+30 mm`, `-2.5 mm`), or `—` for no difference. */
  label: string;
}

/** What two picked points say about each other. */
export interface Measurement {
  /** The straight-line distance between them, in metres. */
  metres: number;
  /** The distance through `formatLength` (`30 mm`); `—` when the two points are the same. */
  label: string;
  /** Signed differences along the world axes, each with its own label. */
  dx: AxisDelta;
  dy: AxisDelta;
  dz: AxisDelta;
  /** The point halfway between them, in world metres: where the readout's label belongs. */
  midpoint: Vec3;
}

/**
 * What the store carries and the label component draws: the distance as text, and where to print it
 * in CSS pixels of the viewport. `x` and `y` are the projected midpoint, already pulled inside the
 * viewport's edges by the scene, which is the only place that knows both the camera and the size.
 */
export interface MeasureReadout {
  /** The figure, already formatted (`30 mm`, `900 mm²`, `27 mm³`). */
  label: string;
  /**
   * What the figure is, e.g. `Area of face · pipe:3`.
   *
   * A bare number at a label position does not say what was measured. With four kinds of result —
   * a distance, a length, an area, a volume — "12 mm" and "12 mm²" must not be confusable, and a
   * reader has to know whether the figure is the gap between two things or the size of one.
   */
  caption: string;
  x: number;
  y: number;
}

/**
 * The em dash `formatLength` prints for a length it refuses. A zero difference has no direction and
 * no unit — it is not "0 mm", it is nothing to measure on that axis — so it takes the same mark as
 * the distance between two identical points. One rule for "no length here" rather than two.
 */
const NONE = "—";

/**
 * A signed difference as a figure: the sign on the front, the magnitude through `formatLength`.
 *
 * The sign cannot go in: `formatLength` refuses a negative length (a length is not negative, and
 * `-30 mm` printed as `30 mm` would be a lie). So the axis delta carries its own sign and hands the
 * magnitude to the ladder, which is also what keeps `+30 mm` and `-30 mm` the same width in a column.
 */
function axisDelta(metres: number): AxisDelta {
  if (!Number.isFinite(metres) || metres === 0) return { metres, label: NONE };
  return { metres, label: `${metres > 0 ? "+" : "-"}${formatLength(Math.abs(metres))}` };
}

/**
 * The measurement between two world points. Order matters only to the sign of the deltas: `a` is
 * where the user clicked first, so `dx` reads "the second point is this much further along X".
 *
 * Two identical points are a real thing to ask for (a double click, a click that lands where the
 * last one did), and the answer is honest rather than invented: a distance of 0 metres, the `—`
 * `formatLength` gives a length it refuses, and a `—` on each axis, with no `NaN` and no unit
 * claimed for a figure that has none. The midpoint is still the point itself, so the caller has
 * something to hang a label on.
 */
export function measureBetween(a: Vec3, b: Vec3): Measurement {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const dz = b.z - a.z;
  const metres = Math.hypot(dx, dy, dz);
  return {
    metres,
    label: formatLength(metres),
    dx: axisDelta(dx),
    dy: axisDelta(dy),
    dz: axisDelta(dz),
    midpoint: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 },
  };
}

// ---------------------------------------------------------------------------------------------- elements
/**
 * What Measure can target (T-113): a B-rep *face*, *edge* or *vertex*, not just an arbitrary
 * surface point. The distinction matters to an engineer — "how far is this boss from that wall" is
 * a face-to-face distance — and reporting the gap between two arbitrarily clicked surface points
 * would be a different, usually larger, number.
 *
 * Each element carries only the geometry the arithmetic needs, in metres, so this stays a pure
 * module: no three.js, no OCP, no topology record. The caller (the scene) projects a pick into one.
 */
export type ElementKind = "vertex" | "edge" | "face";

/** A vertex target. §15.1: a "point" means a B-rep vertex, never a mesh node. */
export interface VertexElement {
  kind: "vertex";
  point: Vec3;
}

/** An edge target: a straight segment, or a sampled polyline when the edge is a curve. */
export interface EdgeElement {
  kind: "edge";
  /** At least one point; the first and last are the endpoints. */
  points: Vec3[];
  /**
   * Which B-rep edge this is, and whose, when the pick could name one.
   *
   * The polyline is enough to measure a DISTANCE to, but not to report the edge's own length: that
   * is the record's true B-rep figure, and the chord sum of the samples is a different (always
   * shorter) number. The identity is what lets the readout say "Length of edge" with the real value.
   */
  part?: string;
  edgeId?: number;
  /** The record's true length and centre of mass for this edge, when the caller has them. */
  length?: number;
  centre?: Vec3;
  /** True for a straight edge (an OCCT line): only then does it have one direction to take an angle to. */
  straight?: boolean;
}

/**
 * A whole body target: the part and, when known, its measured volume.
 *
 * A body is not a sub-shape with an id of its own in the record — the record lists the part's solids
 * — so it is identified by the part and the index of the solid the pick resolved to. A part that is
 * one body is the common case, and its volume is that body's.
 */
export interface BodyElement {
  kind: "body";
  part: string;
  /** The solid's index in the part's record, or 0 for the first. */
  solidId: number;
  /** Enclosed volume, cubic metres, when the record measured it. */
  volume?: number;
  /** Centre of mass, metres. */
  centre?: Vec3;
}

/** A planar face target: the unit `normal` through `point` defines its plane. */
export interface FaceElement {
  kind: "face";
  point: Vec3;
  /** Unit normal. A curved face is not a plane: pass it as an edge or a vertex instead. */
  normal: Vec3;
  /**
   * Which face this is, as `"<part>:<face id>"`, or "" when the pick cannot name one.
   *
   * **This is what tells "one face picked twice" from "two faces of the same body"**, and without it
   * the distinction is unrepresentable: two clicks on the barrel of a pipe both arrive as
   * `kind: "face"`, so the plane-vs-plane branch ran and reported the perpendicular gap between the
   * two *normals* — `1.92e-1 µm` for points 150 mm apart. Two distinct planar faces that are not
   * parallel do meet along a line, so `metres: 0` is right for them; the same face picked twice is a
   * plain point-to-point distance.
   *
   * A curved face's normal varies over the surface, so its plane is not a plane at all. The identity
   * cannot fix that on its own — see `measureElements`, which treats a same-face pair as points.
   */
  face?: string;
  /** The owning part and the face id, for a face's own area and centroid. */
  part?: string;
  faceId?: number;
  /** The record's true area and centroid for this face, when the caller has them. */
  area?: number;
  /** The face's area-weighted centre, metres: where its area label belongs. */
  centre?: Vec3;
  /**
   * False for a curved face (a pipe's barrel, a fillet). Its `normal` is only the normal at the click,
   * so its "plane" is a tangent plane that touches the face along one line: measuring to it reported
   * `—` for a flange 70 mm away, because the flange's point happened to lie near that tangent plane.
   * A curved face is measured through `samples` instead. Absent means planar, the older callers' case.
   */
  planar?: boolean;
  /**
   * Points on the face, world metres (its tessellation's vertices), for the minimum distance to a
   * curved face. A distance, unlike an area, has no B-rep figure on the client; the samples are as
   * close as the display mesh, which is the view the user clicked on.
   */
  samples?: Vec3[];
  /**
   * The face's triangles, world metres, three points each. A long cylinder's tessellation has its
   * vertices only at the barrel's two ends, so the vertex `samples` alone miss a nearest point halfway
   * along it; the triangles make the minimum distance exact for the drawn surface.
   */
  triangles?: Vec3[];
}

export type MeasureElement = VertexElement | EdgeElement | FaceElement | BodyElement;

/** How the two elements related, which is what the readout's title explains. */
export type MeasureRelation = "point" | "parallel" | "intersecting" | "closest";

/** The measurement between two elements, with the relation that produced the distance. */
export interface ElementMeasurement extends Measurement {
  relation: MeasureRelation;
  /** The point on the first element the distance was measured from. */
  from: Vec3;
  /** The point on the second element the distance was measured to. */
  to: Vec3;
  /**
   * The angle between the two elements in degrees, 0 to 90, when both have a direction: two planar
   * faces (between their planes), two straight edges (between their lines), or a straight edge and a
   * planar face (between the line and the plane). Null otherwise: a vertex or a curved face has none.
   */
  angle: number | null;
}

const EPS = 1e-12;

function sub(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function len(a: Vec3): number {
  return Math.hypot(a.x, a.y, a.z);
}

function dot(a: Vec3, b: Vec3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

/** A unit vector, or the zero vector when the input has no length (a caller error, not a guess). */
export function unit(a: Vec3): Vec3 {
  const n = len(a);
  return n > EPS ? { x: a.x / n, y: a.y / n, z: a.z / n } : { x: 0, y: 0, z: 0 };
}

/** The endpoints of an edge: the first and last of its polyline. */
export function edgeEnds(edge: EdgeElement): [Vec3, Vec3] {
  const points = edge.points;
  if (points.length === 0) return [{ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 0 }];
  return [points[0], points[points.length - 1]];
}

/** The signed distance from `p` to the plane through `origin` with unit `normal`. */
export function planeDistance(p: Vec3, origin: Vec3, normal: Vec3): number {
  return dot(sub(p, origin), unit(normal));
}

/** The projection of `p` onto a face's plane: the closest point of that plane to `p`. */
export function pointOnPlane(p: Vec3, face: FaceElement): Vec3 {
  const n = unit(face.normal);
  const d = planeDistance(p, face.point, n);
  return { x: p.x - n.x * d, y: p.y - n.y * d, z: p.z - n.z * d };
}

export interface ClosestPair {
  from: Vec3;
  to: Vec3;
  metres: number;
}

/** The closest points on two segments (the standard clamped solution, degenerate cases included). */
export function segmentClosest(p1: Vec3, q1: Vec3, p2: Vec3, q2: Vec3): ClosestPair {
  const d1 = sub(q1, p1);
  const d2 = sub(q2, p2);
  const r = sub(p1, p2);
  const a = dot(d1, d1);
  const e = dot(d2, d2);
  const f = dot(d2, r);
  let s = 0;
  let t = 0;
  if (a <= EPS && e <= EPS) {
    return { from: p1, to: p2, metres: len(r) };
  }
  if (a <= EPS) {
    t = clamp01(f / e);
  } else {
    const c = dot(d1, r);
    if (e <= EPS) {
      s = clamp01(-c / a);
    } else {
      const b = dot(d1, d2);
      const denom = a * e - b * b;
      s = denom > EPS ? clamp01((b * f - c * e) / denom) : 0;
      t = (b * s + f) / e;
      if (t < 0) {
        t = 0;
        s = clamp01(-c / a);
      } else if (t > 1) {
        t = 1;
        s = clamp01((b - c) / a);
      }
    }
  }
  const from = { x: p1.x + d1.x * s, y: p1.y + d1.y * s, z: p1.z + d1.z * s };
  const to = { x: p2.x + d2.x * t, y: p2.y + d2.y * t, z: p2.z + d2.z * t };
  return { from, to, metres: len(sub(from, to)) };
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** The closest point of a polyline to `p`. */
export function closestOnPolyline(p: Vec3, points: readonly Vec3[]): Vec3 {
  if (points.length === 0) return p;
  if (points.length === 1) return points[0];
  let best = points[0];
  let bestSq = Number.POSITIVE_INFINITY;
  for (let i = 1; i < points.length; i++) {
    const found = segmentClosest(p, p, points[i - 1], points[i]).to;
    const d = sub(p, found);
    const sq = dot(d, d);
    if (sq < bestSq) {
      bestSq = sq;
      best = found;
    }
  }
  return best;
}

/** An element's geometry as a list of points: a vertex is one, an edge is its polyline. */
function polylineOf(element: VertexElement | EdgeElement): Vec3[] {
  return element.kind === "vertex" ? [element.point] : element.points;
}

/** The closest point of an element to `p` (its plane projection for a face). */
function closestOf(p: Vec3, element: MeasureElement): Vec3 {
  if (element.kind === "face") return pointOnPlane(p, element);
  // A body has no polyline: it is measured as a whole (its volume), never as one end of a distance.
  // `measureElements` refuses a body before reaching here, so this is the type's honest fallback.
  if (element.kind === "body") return element.centre ?? p;
  return closestOnPolyline(p, polylineOf(element));
}

/** The distance from a segment to a plane: zero when the segment crosses it, else its nearest end. */
function segmentToPlane(p1: Vec3, q1: Vec3, face: FaceElement): ClosestPair {
  const n = unit(face.normal);
  const d1 = planeDistance(p1, face.point, n);
  const d2 = planeDistance(q1, face.point, n);
  if (d1 === 0) return { from: p1, to: p1, metres: 0 };
  if (d2 === 0) return { from: q1, to: q1, metres: 0 };
  if (d1 < 0 !== d2 < 0) return { from: p1, to: p1, metres: 0 }; // it crosses: the planes meet
  const near = Math.abs(d1) <= Math.abs(d2) ? p1 : q1;
  return { from: near, to: pointOnPlane(near, face), metres: Math.abs(Math.min(Math.abs(d1), Math.abs(d2))) };
}

/** The closest pair between a face and a vertex or edge. */
function faceToElement(face: FaceElement, other: VertexElement | EdgeElement): ClosestPair {
  if (other.kind === "vertex") {
    const metres = Math.abs(planeDistance(other.point, face.point, face.normal));
    return { from: other.point, to: pointOnPlane(other.point, face), metres };
  }
  const points = other.points;
  if (points.length === 0) return { from: face.point, to: face.point, metres: 0 };
  if (points.length === 1) {
    const metres = Math.abs(planeDistance(points[0], face.point, face.normal));
    return { from: points[0], to: pointOnPlane(points[0], face), metres };
  }
  let best: ClosestPair | null = null;
  for (let i = 1; i < points.length; i++) {
    const found = segmentToPlane(points[i - 1], points[i], face);
    if (best === null || found.metres < best.metres) best = found;
  }
  return best ?? { from: face.point, to: face.point, metres: 0 };
}

/** The closest pair between two polylines (each segment against each segment). */
function polylineToPolyline(a: readonly Vec3[], b: readonly Vec3[]): ClosestPair {
  if (a.length === 0 || b.length === 0) {
    return { from: a[0] ?? b[0] ?? { x: 0, y: 0, z: 0 }, to: b[0] ?? a[0] ?? { x: 0, y: 0, z: 0 }, metres: 0 };
  }
  const aSeg = a.length === 1 ? [[a[0], a[0]]] : a.slice(1).map((p, i) => [a[i], p]);
  const bSeg = b.length === 1 ? [[b[0], b[0]]] : b.slice(1).map((p, i) => [b[i], p]);
  let best: ClosestPair | null = null;
  for (const [p1, q1] of aSeg) {
    for (const [p2, q2] of bSeg) {
      const found = segmentClosest(p1, q1, p2, q2);
      if (best === null || found.metres < best.metres) best = found;
    }
  }
  return best ?? { from: a[0], to: b[0], metres: len(sub(a[0], b[0])) };
}

/**
 * The distance between two elements (T-113), with the relation that produced it.
 *
 * The cases are recognised in this order, because the earlier ones give the answer an engineer
 * actually means while a generic closest-approach would not:
 *
 * * **two parallel planes** report the *normal* gap (`parallel`). This is deliberately not the
 *   closest approach of two sample points, which would depend on where each was clicked.
 * * **two non-parallel planes** meet, so the distance is 0 (`intersecting`).
 * * **a plane against a vertex or edge** is the perpendicular distance (`closest`), zero if they
 *   cross.
 * * **everything else** is the closest approach between the two polylines (`closest`), and two
 *   vertices are a plain point distance (`point`).
 *
 * A vertex is a one-point polyline internally, so the vertex cases need no separate branch and
 * cannot disagree with the edge ones.
 */
export function measureElements(a: MeasureElement, b: MeasureElement): ElementMeasurement {
  // Both clicks on ONE face is a point-to-point distance, whatever that face's normal says. Two
  // clicks on a cylinder's barrel arrive as two `face` elements whose normals differ by 90°, and the
  // intersecting branch then reported a meaningless perpendicular gap for points far apart on the
  // same surface. The identity is the only thing that can tell the two cases apart.
  const sameFace =
    a.kind === "face" && b.kind === "face" && a.face !== undefined && a.face !== "" && a.face === b.face;
  // **"Two planes meet" is only true of one SOLID.** Two faces are planes, and two non-parallel
  // planes do meet along a line — but only if they belong to the same body. A face on the pipe and a
  // face on the flange are two points in space: they are a metre apart and meet nowhere. Reading them
  // as planes reported `metres: 0` and the caption "The two faces meet" for a gap of 449 mm.
  //
  // The part is what tells them apart, and only a part that names it can: a mesh-only import has no
  // part id on its face, and for those the closest-approach answer is used, which is right for two
  // surfaces whatever they belong to.
  const samePart =
    a.kind === "face" && b.kind === "face" && a.part !== undefined && a.part === b.part;
  // **A curved face is not a plane** (see `FaceElement.planar`): any pair with one is the minimum
  // distance between the two point sets, which is what a CAD measure reports for a cylinder.
  const curved = !sameFace && (isCurved(a) || isCurved(b));
  const relation: MeasureRelation =
    sameFace
      ? "point"
      : curved
        ? "closest"
      : a.kind === "vertex" && b.kind === "vertex"
        ? "point"
        : a.kind === "face" && b.kind === "face"
          ? Math.abs(Math.abs(dot(unit(a.normal), unit(b.normal))) - 1) < 1e-9
            ? "parallel"
            : samePart
              ? "intersecting"
              : "closest"
          : "closest";

  let pair: ClosestPair;
  if (a.kind === "body" || b.kind === "body") {
    // A body is not a distance target: its own size is its volume, reported by `intrinsicOfElement`.
    // Checked FIRST so a body never reaches the face branches, which would read `.point` off it and
    // report a meaningless gap. Zero here is a placeholder the caller does not use.
    const at = a.kind === "body" ? (a.centre ?? { x: 0, y: 0, z: 0 }) : (b.kind === "body" ? (b.centre ?? { x: 0, y: 0, z: 0 }) : { x: 0, y: 0, z: 0 });
    pair = { from: at, to: at, metres: 0 };
  } else if (curved) {
    pair = shapeClosest(shapeOf(a), shapeOf(b));
  } else if (sameFace) {
    // The distance between the two points the user actually clicked, in the plane they clicked them
    // on. For a planar face this is the in-plane distance, which is the useful figure; for a curved
    // one it is the surface distance along the chord, which is the only honest answer available
    // without the geodesic.
    pair = { from: a.point, to: b.point, metres: len(sub(b.point, a.point)) };
  } else if (relation === "parallel" && a.kind === "face" && b.kind === "face") {
    const n = unit(a.normal);
    const gap = planeDistance(b.point, a.point, n);
    // The foot on B's plane straight across from A's point, so the drawn line and dX/dY/dZ are the
    // normal gap itself. The version before put `to` in A's OWN plane (B's point moved back by the
    // gap), so the line lay along face A and its components said nothing about the gap.
    const to = { x: a.point.x + n.x * gap, y: a.point.y + n.y * gap, z: a.point.z + n.z * gap };
    pair = { from: a.point, to, metres: Math.abs(gap) };
  } else if (relation === "intersecting" && a.kind === "face" && b.kind === "face") {
    // Two non-parallel planes share a line; report the shared point nearest the first face's origin.
    const point = closestOf(a.point, b);
    pair = { from: pointOnPlane(a.point, a), to: point, metres: 0 };
  } else if (relation === "closest" && a.kind === "face" && b.kind === "face") {
    // Two faces that do not share a solid. Their planes may cross, but the surfaces need not — a face
    // on the pipe and a face on the flange are two surfaces in space. The honest figure is the gap
    // between them, which is what the second face's own point reports against the first face's plane.
    //
    // This branch also covers a face with no part id (a mesh-only import), where "same solid" cannot
    // be established: closest approach is the reading that is true either way.
    const metres = Math.abs(planeDistance(b.point, a.point, unit(a.normal)));
    pair = { from: b.point, to: pointOnPlane(b.point, a), metres };
  } else if (a.kind === "face" && b.kind !== "face") {
    pair = faceToElement(a, b);
  } else if (b.kind === "face" && a.kind !== "face") {
    const inner = faceToElement(b, a);
    pair = { from: inner.to, to: inner.from, metres: inner.metres };
  } else if (a.kind === "vertex" && b.kind === "vertex") {
    pair = { from: a.point, to: b.point, metres: len(sub(b.point, a.point)) };
  } else if (a.kind !== "face" && b.kind !== "face") {
    // An edge or a vertex on each side: two vertices are one-point polylines, so this is the same
    // code path as segment-to-segment and the two cannot disagree.
    pair = polylineToPolyline(polylineOf(a), polylineOf(b));
  } else {
    // Unreachable: the face cases above cover every face, and the two non-face branches cover the
    // rest. Kept so the dispatch has no implicit fall-through, and written on `closestOf` so it
    // needs no narrowing of `a` or `b`.
    const from = closestOf({ x: 0, y: 0, z: 0 }, a);
    pair = { from, to: from, metres: 0 };
  }

  return {
    ...measureBetween(pair.from, pair.to),
    metres: pair.metres,
    label: formatLength(pair.metres),
    relation,
    from: pair.from,
    to: pair.to,
    angle: sameFace ? null : angleBetween(a, b),
  };
}

function isCurved(element: MeasureElement): boolean {
  return element.kind === "face" && element.planar === false;
}

/** An element as points: a face's samples (or its click), an edge's polyline, a vertex. */
function samplesOf(element: MeasureElement): Vec3[] {
  switch (element.kind) {
    case "face":
      return element.samples !== undefined && element.samples.length > 0 ? element.samples : [element.point];
    case "edge":
      return element.points;
    case "vertex":
      return [element.point];
    default:
      return element.centre === undefined ? [] : [element.centre];
  }
}

/**
 * The closest pair between two point sets, by exhaustive search. The caller bounds the sets (the scene
 * strides a face's samples down to a few thousand), so this is a few million subtractions at most,
 * once per click.
 */
export function pointSetClosest(a: readonly Vec3[], b: readonly Vec3[]): ClosestPair {
  if (a.length === 0 || b.length === 0) {
    const at = a[0] ?? b[0] ?? { x: 0, y: 0, z: 0 };
    return { from: at, to: at, metres: 0 };
  }
  let bestSq = Number.POSITIVE_INFINITY;
  let from = a[0];
  let to = b[0];
  for (const p of a) {
    for (const q of b) {
      const dx = q.x - p.x;
      const dy = q.y - p.y;
      const dz = q.z - p.z;
      const sq = dx * dx + dy * dy + dz * dz;
      if (sq < bestSq) {
        bestSq = sq;
        from = p;
        to = q;
      }
    }
  }
  return { from, to, metres: Math.sqrt(bestSq) };
}

/**
 * An element as primitives for the exact minimum distance: points, segments and triangles. A face with
 * triangles is its triangles; without them, its samples as points. An edge is its polyline's segments.
 */
export interface Shape {
  points: Vec3[];
  /** Pairs: `[a0, b0, a1, b1, ...]`. */
  segments: Vec3[];
  /** Triples: `[a0, b0, c0, a1, ...]`. */
  triangles: Vec3[];
}

export function shapeOf(element: MeasureElement): Shape {
  if (element.kind === "face" && element.triangles !== undefined && element.triangles.length >= 3) {
    return { points: [], segments: [], triangles: element.triangles };
  }
  if (element.kind === "edge" && element.points.length >= 2) {
    const segments: Vec3[] = [];
    for (let i = 1; i < element.points.length; i++) segments.push(element.points[i - 1], element.points[i]);
    return { points: [], segments, triangles: [] };
  }
  return { points: samplesOf(element), segments: [], triangles: [] };
}

/** The closest point of triangle `abc` to `p` (Ericson, Real-Time Collision Detection §5.1.5). */
export function closestOnTriangle(p: Vec3, a: Vec3, b: Vec3, c: Vec3): Vec3 {
  const ab = sub(b, a);
  const ac = sub(c, a);
  const ap = sub(p, a);
  const d1 = dot(ab, ap);
  const d2 = dot(ac, ap);
  if (d1 <= 0 && d2 <= 0) return a;
  const bp = sub(p, b);
  const d3 = dot(ab, bp);
  const d4 = dot(ac, bp);
  if (d3 >= 0 && d4 <= d3) return b;
  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) {
    const v = d1 / (d1 - d3);
    return { x: a.x + ab.x * v, y: a.y + ab.y * v, z: a.z + ab.z * v };
  }
  const cp = sub(p, c);
  const d5 = dot(ab, cp);
  const d6 = dot(ac, cp);
  if (d6 >= 0 && d5 <= d6) return c;
  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) {
    const w = d2 / (d2 - d6);
    return { x: a.x + ac.x * w, y: a.y + ac.y * w, z: a.z + ac.z * w };
  }
  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) {
    const w = (d4 - d3) / (d4 - d3 + (d5 - d6));
    return { x: b.x + (c.x - b.x) * w, y: b.y + (c.y - b.y) * w, z: b.z + (c.z - b.z) * w };
  }
  const denom = 1 / (va + vb + vc);
  const v = vb * denom;
  const w = vc * denom;
  return { x: a.x + ab.x * v + ac.x * w, y: a.y + ab.y * v + ac.y * w, z: a.z + ab.z * v + ac.z * w };
}

/** Every point of a shape: its points and the corners of its segments and triangles. */
function cornersOf(s: Shape): Vec3[] {
  return [...s.points, ...s.segments, ...s.triangles];
}

/** Every segment of a shape: its own and its triangles' three edges each. */
function edgesOf(s: Shape): Vec3[] {
  const edges = [...s.segments];
  for (let i = 0; i + 2 < s.triangles.length; i += 3) {
    const [a, b, c] = [s.triangles[i], s.triangles[i + 1], s.triangles[i + 2]];
    edges.push(a, b, b, c, c, a);
  }
  return edges;
}

/** The closest point of shape `s` to `p`, and its distance. */
function closestOnShape(p: Vec3, s: Shape): ClosestPair {
  let best: ClosestPair = { from: p, to: p, metres: Number.POSITIVE_INFINITY };
  const consider = (q: Vec3) => {
    const d = len(sub(q, p));
    if (d < best.metres) best = { from: p, to: q, metres: d };
  };
  for (const q of s.points) consider(q);
  for (let i = 0; i + 1 < s.segments.length; i += 2) consider(segmentClosest(p, p, s.segments[i], s.segments[i + 1]).to);
  for (let i = 0; i + 2 < s.triangles.length; i += 3) consider(closestOnTriangle(p, s.triangles[i], s.triangles[i + 1], s.triangles[i + 2]));
  return best;
}

/**
 * The exact minimum distance between two shapes of points, segments and triangles.
 *
 * For two disjoint sets of these primitives the closest pair is always either a corner of one against
 * the other (point to point, segment or triangle) or an edge of one against an edge of the other — so
 * checking those two families both ways is exact, which a vertex-only search is not (it misses the
 * middle of a long triangle). The work is bounded by the caller's triangle budget.
 */
export function shapeClosest(a: Shape, b: Shape): ClosestPair {
  let best: ClosestPair = { from: { x: 0, y: 0, z: 0 }, to: { x: 0, y: 0, z: 0 }, metres: Number.POSITIVE_INFINITY };
  for (const p of cornersOf(a)) {
    const found = closestOnShape(p, b);
    if (found.metres < best.metres) best = found;
  }
  for (const p of cornersOf(b)) {
    const found = closestOnShape(p, a);
    if (found.metres < best.metres) best = { from: found.to, to: found.from, metres: found.metres };
  }
  const ea = edgesOf(a);
  const eb = edgesOf(b);
  for (let i = 0; i + 1 < ea.length; i += 2) {
    for (let j = 0; j + 1 < eb.length; j += 2) {
      const found = segmentClosest(ea[i], ea[i + 1], eb[j], eb[j + 1]);
      if (found.metres < best.metres) best = found;
    }
  }
  if (!Number.isFinite(best.metres)) {
    const at = cornersOf(a)[0] ?? cornersOf(b)[0] ?? { x: 0, y: 0, z: 0 };
    return { from: at, to: at, metres: 0 };
  }
  return best;
}

/** The direction an element has for an angle: a planar face's normal, a straight edge's line. */
function directionOf(element: MeasureElement): { kind: "plane" | "line"; dir: Vec3 } | null {
  if (element.kind === "face") return element.planar === false ? null : { kind: "plane", dir: unit(element.normal) };
  if (element.kind === "edge" && element.straight === true && element.points.length >= 2) {
    const [start, end] = edgeEnds(element);
    const dir = unit(sub(end, start));
    return len(dir) === 0 ? null : { kind: "line", dir };
  }
  return null;
}

/**
 * The angle between two elements, in degrees from 0 to 90, or null when either has no direction.
 *
 * Two planes and two lines are measured between themselves (the acute angle, as a CAD measure gives
 * it: the angle between two faces of a box is 90°, not 270°); a line and a plane is 90° minus the
 * angle to the plane's normal, so a line lying in the plane reads 0°.
 */
export function angleBetween(a: MeasureElement, b: MeasureElement): number | null {
  const da = directionOf(a);
  const db = directionOf(b);
  if (da === null || db === null) return null;
  const c = Math.min(1, Math.abs(dot(da.dir, db.dir)));
  const radians = da.kind === db.kind ? Math.acos(c) : Math.asin(c);
  return (radians * 180) / Math.PI;
}

/** An angle for the readout: `90°`, `37.5°`, `0.12°`; three significant figures, no trailing zeros. */
export function formatAngle(degrees: number): string {
  if (!Number.isFinite(degrees)) return NONE;
  if (degrees === 0) return "0°";
  const shown = degrees >= 100 ? degrees.toFixed(0) : degrees >= 10 ? degrees.toFixed(1) : degrees.toFixed(2);
  return `${String(Number(shown))}°`;
}

/** What the readout's title says about each relation. */
export const RELATION_LABEL: Record<MeasureRelation, string> = {
  // Two vertices, or two clicks on one face: both are the straight line between the picked points.
  point: "Distance between the two picked points",
  parallel: "Normal distance between two parallel faces",
  intersecting: "The two faces meet",
  closest: "Closest distance between the two elements",
};

// ---------------------------------------------------------------------------------------- intrinsics

/**
 * A measurement of one element's OWN size, rather than of the distance between two.
 *
 * The distance measurements above answer "how far apart"; these answer "how big", which is the other
 * half of what a measure tool is for and was missing. A single click in the right mode gives one:
 * an edge's length, a face's area, a solid's volume.
 *
 * **Every figure here is a model figure, never a mesh one.** An edge's length comes from the record's
 * true B-rep length and not from its sampled polyline; a face's area and a solid's volume come from
 * ``BRepGProp`` on the server (`topology_brep._face_sizes`, `_solids`) and not from the display
 * triangles. Summing triangles would make the same face report a different area at a different mesh
 * quality, so the figure would describe the tessellation rather than the part.
 */
export type IntrinsicKind = "length" | "area" | "volume";

export interface IntrinsicMeasurement {
  kind: IntrinsicKind;
  /** The figure in SI base units: metres, square metres, cubic metres. */
  value: number;
  /** The figure through the unit ladder, e.g. `30 mm`, `900 mm²`, `27 cm³`. */
  label: string;
  /** What the figure IS, for the readout's caption: a number alone does not say what was measured. */
  caption: string;
  /** Where the label belongs, world metres: the element's own midpoint or centre. */
  at: Vec3;
}

/** What a caller must give to measure one element's size; the figures come from the record. */
export interface IntrinsicInput {
  kind: IntrinsicKind;
  /** Sl unit value: metres, square metres, or cubic metres. */
  value: number;
  at: Vec3;
  /** A name for what was measured, used in the caption when given (`outer wall`). */
  name?: string;
}

/** The caption for each kind, with the part's own name when the caller has one. */
const INTRINSIC_CAPTION: Record<IntrinsicKind, string> = {
  length: "Length of edge",
  area: "Area of face",
  volume: "Volume of body",
};

/**
 * One element's own size, ready for the readout.
 *
 * Refuses a value that is not a positive finite number rather than printing `NaN m²` or a negative
 * area: a figure the caller cannot have measured is a caller error, and a silently wrong size is
 * worse than no size at all.
 */
export function measureIntrinsic(input: IntrinsicInput): IntrinsicMeasurement | null {
  const { kind, value, at } = input;
  if (!Number.isFinite(value) || value <= 0) return null;
  const format = kind === "length" ? formatLength : kind === "area" ? formatArea : formatVolume;
  const base = INTRINSIC_CAPTION[kind];
  return {
    kind,
    value,
    label: format(value),
    caption: input.name === undefined || input.name === "" ? base : `${base} · ${input.name}`,
    at,
  };
}

/** One selection, as the picker and the topology record hand it over. */
export interface SelectedElement {
  kind: "vertex" | "edge" | "face" | "body";
  /** The owning part, a node id. */
  part: string;
  /** The element's id within the part: an edge id, a face id, or a vertex id. */
  id: number;
  /** The record's figures, when the caller has them (an edge's length and centre). */
  length?: number;
  centre?: Vec3;
  /** Face area and centroid, from the part's face sizes. */
  area?: number;
  /** Body volume and centroid, from the part's solids. */
  volume?: number;
}

/**
 * The intrinsic measurement one selected element supports, or null when it has none.
 *
 * A vertex has no size of its own — it is a point — so it returns null rather than a zero length,
 * and the caller keeps the two-click distance behaviour for it. That is a real distinction and not a
 * gap: "how long is this edge" is a question, "how big is this point" is not.
 */
/**
 * The intrinsic measurement of one measure ELEMENT, when the click named one.
 *
 * An edge uses the record's true B-rep length and centre of mass; a face uses the server's
 * `BRepGProp` area. A vertex has no size (a point has no extent), and an element the pick could not
 * name — an edge with no record loaded, a face on a mesh-only import — returns null so the caller
 * falls back to the two-click distance rather than inventing a figure.
 */
export function intrinsicOfElement(element: MeasureElement): IntrinsicMeasurement | null {
  if (element.kind === "edge") {
    if (element.length === undefined || element.centre === undefined) return null;
    return measureIntrinsic({
      kind: "length",
      value: element.length,
      at: element.centre,
      name: element.part === undefined || element.edgeId === undefined ? undefined : `edge ${element.edgeId}`,
    });
  }
  if (element.kind === "body") {
    if (element.volume === undefined || element.centre === undefined) return null;
    return measureIntrinsic({
      kind: "volume",
      value: element.volume,
      at: element.centre,
      name: `body ${element.solidId}`,
    });
  }
  if (element.kind === "face") {
    if (element.area === undefined || element.point === undefined) return null;
    // The face's own centroid is the honest place for the label; `point` is where the user clicked,
    // which is on the face but not its centre. Fall back to the click point when no centroid came.
    return measureIntrinsic({
      kind: "area",
      value: element.area,
      at: element.centre ?? element.point,
      name: element.part === undefined || element.faceId === undefined ? undefined : `face ${element.faceId}`,
    });
  }
  return null;
}

export function intrinsicOf(element: SelectedElement): IntrinsicMeasurement | null {
  const name = `${element.part}:${element.id}`;
  switch (element.kind) {
    case "edge":
      if (element.length === undefined || element.centre === undefined) return null;
      return measureIntrinsic({ kind: "length", value: element.length, at: element.centre, name });
    case "face":
      if (element.area === undefined || element.centre === undefined) return null;
      return measureIntrinsic({ kind: "area", value: element.area, at: element.centre, name });
    case "body":
      if (element.volume === undefined || element.centre === undefined) return null;
      return measureIntrinsic({ kind: "volume", value: element.volume, at: element.centre, name });
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------------------- the panel

/** One line of the Measure panel: what the figure is, and the figure. */
export interface MeasureRow {
  label: string;
  value: string;
}

/**
 * What the Measure panel shows (the CAD-style readout beside the view): what was picked, and every
 * figure the pick supports — the distance with its X, Y and Z components, the angle when both
 * elements have a direction, and each element's own size.
 *
 * The floating label at the measurement's midpoint stays the one-figure glance; this is the full
 * answer, and it is built here rather than in the component so it can be tested without a renderer.
 */
export interface MeasureDetails {
  /** "first" while nothing is picked, "second" after one element, "done" with a distance. */
  step: "first" | "second" | "done";
  /** The panel's heading for the result: the relation, or what one element's figure is. */
  title: string;
  /** The picked elements, described: `Edge 12`, `Face 3 (curved)`, `Vertex`. */
  first: string | null;
  second: string | null;
  rows: MeasureRow[];
}

/** An element in a few words for the panel. */
export function describeElement(element: MeasureElement | null): string {
  if (element === null) return "Point";
  switch (element.kind) {
    case "vertex":
      return "Vertex";
    case "edge":
      return `${element.straight === true ? "Line" : "Edge"}${element.edgeId === undefined ? "" : ` ${element.edgeId}`}`;
    case "face":
      return `Face${element.faceId === undefined ? "" : ` ${element.faceId}`}${element.planar === false ? " (curved)" : ""}`;
    default:
      return `Body ${element.solidId}`;
  }
}

/** A signed coordinate or component: `+30 mm`, `-2.5 mm`, `0`. */
function signedLength(metres: number): string {
  if (!Number.isFinite(metres)) return NONE;
  if (metres === 0) return "0";
  return `${metres > 0 ? "+" : "-"}${formatLength(Math.abs(metres))}`;
}

/** The rows for one element on its own: its size, and a vertex's coordinates. */
function elementRows(element: MeasureElement | null, at: Vec3 | null): MeasureRow[] {
  const rows: MeasureRow[] = [];
  const own = element === null ? null : intrinsicOfElement(element);
  if (own !== null) rows.push({ label: own.kind === "length" ? "Length" : own.kind === "area" ? "Area" : "Volume", value: own.label });
  const point = element?.kind === "vertex" ? element.point : element === null ? at : null;
  if (point !== null) {
    rows.push({ label: "X", value: signedLength(point.x) }, { label: "Y", value: signedLength(point.y) }, { label: "Z", value: signedLength(point.z) });
  }
  return rows;
}

/**
 * The panel's content for the state the tool is in.
 *
 * `a`/`b` are the elements the clicks resolved to (null for a click that named none: a plain surface
 * point, measured as a point), `atA` the first click's world point, and `measurement` the distance
 * once there are two. The distance rows come first because they are what a second click asked for;
 * the elements' own sizes follow, so nothing the first click showed is lost by the second.
 */
export function measureDetails(input: {
  a: MeasureElement | null;
  b: MeasureElement | null;
  atA: Vec3 | null;
  measurement: Measurement | ElementMeasurement | null;
}): MeasureDetails {
  const { a, b, atA, measurement } = input;
  if (atA === null) return { step: "first", title: "Pick a first element", first: null, second: null, rows: [] };
  if (measurement === null) {
    const own = a === null ? null : intrinsicOfElement(a);
    return {
      step: "second",
      title: own === null ? "Pick a second element" : own.caption,
      first: describeElement(a),
      second: null,
      rows: elementRows(a, atA),
    };
  }
  const relation = "relation" in measurement ? measurement.relation : null;
  const rows: MeasureRow[] = [
    { label: "Distance", value: measurement.label },
    { label: "dX", value: measurement.dx.label },
    { label: "dY", value: measurement.dy.label },
    { label: "dZ", value: measurement.dz.label },
  ];
  const angle = "angle" in measurement ? measurement.angle : null;
  if (angle !== null) rows.push({ label: "Angle", value: formatAngle(angle) });
  const sizeA = a === null ? null : intrinsicOfElement(a);
  const sizeB = b === null ? null : intrinsicOfElement(b);
  if (sizeA !== null) rows.push({ label: `${describeElement(a)} ${sizeA.kind}`, value: sizeA.label });
  if (sizeB !== null) rows.push({ label: `${describeElement(b)} ${sizeB.kind}`, value: sizeB.label });
  return {
    step: "done",
    title: relation === null ? "Distance" : RELATION_LABEL[relation],
    first: describeElement(a),
    second: describeElement(b),
    rows,
  };
}
