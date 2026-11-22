/**
 * Rectangles and regular polygons as ONE entity each (review B5): a rectangle keeps its centre,
 * width and height, so editing one size keeps it a rectangle instead of making a trapezoid of four
 * free lines. The stored form is unchanged for the server: each shape is written as its lines
 * (ADR-0036 decision 1), and every one of those lines carries a `shape` record (ignored by the
 * builder) that reads the shape back when the sketch is opened again. Pure: tests run in Node.
 */
import type { Entity, Point } from "./model";

export type Rectangle = Extract<Entity, { kind: "rectangle" }>;
export type Polygon = Extract<Entity, { kind: "polygon" }>;
export type Shape = Rectangle | Polygon;
/** The entities the server knows: what a shape is drawn and stored as. */
export type Primitive = Exclude<Entity, Shape>;

export function isShape(e: Entity): e is Shape {
  return e.kind === "rectangle" || e.kind === "polygon";
}

/** Corner signs of a rectangle, counter-clockwise from the lower left. */
export const RECT_SIGNS: ReadonlyArray<[number, number]> = [[-1, -1], [1, -1], [1, 1], [-1, 1]];

export function cornersOf(e: Shape): Point[] {
  if (e.kind === "rectangle") return RECT_SIGNS.map(([su, sv]) => [e.c[0] + (su * e.w) / 2, e.c[1] + (sv * e.h) / 2]);
  return Array.from({ length: e.n }, (_, k) => {
    const t = e.angle + (2 * Math.PI * k) / e.n;
    return [e.c[0] + e.r * Math.cos(t), e.c[1] + e.r * Math.sin(t)] as Point;
  });
}

/** The shape as the closed chain of lines it stands for. */
export function linesOf(e: Shape): Primitive[] {
  const c = cornersOf(e);
  return c.map((a, k) => ({ kind: "line" as const, a, b: c[(k + 1) % c.length] }));
}

/** Every entity as the server's kinds: shapes become their lines. */
export function explode(entities: readonly Entity[]): Primitive[] {
  return entities.flatMap((e) => (isShape(e) ? linesOf(e) : [e]));
}

/** The rectangle with corners p and q (any two opposite ones). */
export function rectangleFrom(p: Point, q: Point): Rectangle {
  return { kind: "rectangle", c: [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2], w: Math.abs(q[0] - p[0]), h: Math.abs(q[1] - p[1]) };
}

/** The regular polygon with this centre and one corner. */
export function polygonFrom(c: Point, corner: Point, sides: number): Polygon {
  const n = Math.max(3, Math.min(64, Math.round(sides)));
  return { kind: "polygon", c: [c[0], c[1]], r: Math.hypot(corner[0] - c[0], corner[1] - c[1]), n, angle: Math.atan2(corner[1] - c[1], corner[0] - c[0]) };
}

/** Points a snap may land on: the corners, the centre and (rectangle) the edge midpoints. */
export function shapeTargets(e: Shape): Point[] {
  const c = cornersOf(e);
  const mids = e.kind === "rectangle" ? c.map((a, k) => [(a[0] + c[(k + 1) % 4][0]) / 2, (a[1] + c[(k + 1) % 4][1]) / 2] as Point) : [];
  return [...c, e.c, ...mids];
}

const near = (p: Point, q: Point) => Math.abs(p[0] - q[0]) < 1e-6 && Math.abs(p[1] - q[1]) < 1e-6;

/**
 * The shape after its point `from` is dragged to `to`: the centre moves it; a rectangle's corner
 * resizes it about the opposite corner; a polygon's corner sets its size and turn. Other points
 * leave it as it is.
 */
export function moveShapePoint(e: Shape, from: Point, to: Point): Shape {
  if (near(e.c, from)) return { ...e, c: [e.c[0] + to[0] - from[0], e.c[1] + to[1] - from[1]] };
  const corners = cornersOf(e);
  const k = corners.findIndex((p) => near(p, from));
  if (k < 0) return e;
  if (e.kind === "rectangle") {
    const opposite = corners[(k + 2) % 4];
    const next = rectangleFrom(opposite, to);
    return next.w > 1e-9 && next.h > 1e-9 ? next : e;
  }
  const r = Math.hypot(to[0] - e.c[0], to[1] - e.c[1]);
  if (r <= 1e-9) return e;
  const turn = Math.atan2(to[1] - e.c[1], to[0] - e.c[0]) - Math.atan2(from[1] - e.c[1], from[0] - e.c[0]);
  return { ...e, r, angle: e.angle + turn };
}

// ------------------------------------------------------------------------------- stored form
/** A stored coordinate: metres, or an expression text (ADR-0037). */
export type Coord = number | string;

const MM_PER_M = 1000;

/** A stored coordinate as expression text: metres become a millimetre quantity. */
function asText(v: Coord): string {
  return typeof v === "string" ? `(${v})` : `(${Number((v * MM_PER_M).toPrecision(12))} [mm])`;
}

/** `base + factor · size`, as a number when both are numbers, else as expression text. */
export function offsetCoord(base: Coord, size: Coord, factor: number): Coord {
  if (typeof base === "number" && typeof size === "number") return base + factor * size;
  const f = Number(factor.toPrecision(12));
  const term = f === 0.5 ? `${asText(size)} / 2` : f === -0.5 ? `-${asText(size)} / 2` : `${asText(size)} * ${f}`;
  if (f === 0) return base;
  if (base === 0) return term;
  return f === -0.5 ? `${asText(base)} - ${asText(size)} / 2` : `${asText(base)} + ${term}`;
}

/**
 * A shape's stored lines (metres or expressions). `c`, `w`, `h` and `r` are the shape's own stored
 * values; every line carries the same `shape` record and its `group` (the editor index), which
 * `readShapes` uses to put the shape together again.
 */
export function storedShape(e: Shape, group: number, c: [Coord, Coord], size: { w?: Coord; h?: Coord; r?: Coord }): unknown[] {
  let corners: Array<[Coord, Coord]>;
  let record: Record<string, unknown>;
  if (e.kind === "rectangle") {
    const w = size.w ?? e.w / MM_PER_M;
    const h = size.h ?? e.h / MM_PER_M;
    corners = RECT_SIGNS.map(([su, sv]) => [offsetCoord(c[0], w, su / 2), offsetCoord(c[1], h, sv / 2)]);
    record = { kind: "rectangle", group, c, w, h };
  } else {
    const r = size.r ?? e.r / MM_PER_M;
    corners = Array.from({ length: e.n }, (_, k) => {
      const t = e.angle + (2 * Math.PI * k) / e.n;
      return [offsetCoord(c[0], r, Math.cos(t)), offsetCoord(c[1], r, Math.sin(t))];
    });
    record = { kind: "polygon", group, c, r, n: e.n, angle: e.angle };
  }
  return corners.map((a, k) => ({ kind: "line", a, b: corners[(k + 1) % corners.length], shape: record }));
}

/** One shape record read back: its numbers (mm) and which of its values are expressions. */
export interface ReadShape {
  entity: Shape;
  /** Lines it stood for in the stored list. */
  count: number;
  /** Expression texts by binding suffix: `c.0`, `c.1`, `w`, `h`, `r`. */
  texts: Record<string, string>;
}

/**
 * The shape whose lines start at `list[start]`, or null when that entry is not the first line of a
 * complete shape (the lines were edited elsewhere: they are read as plain lines). An expression
 * starts at `fallback` mm until the server's value arrives.
 */
export function readShape(list: readonly unknown[], start: number, fallback: number): ReadShape | null {
  const first = list[start] as Record<string, unknown> | null;
  const rec = first && typeof first === "object" ? (first.shape as Record<string, unknown> | undefined) : undefined;
  if (!rec || typeof rec !== "object" || typeof rec.group !== "number") return null;
  const texts: Record<string, string> = {};
  const num = (raw: unknown, key: string): number | null => {
    if (typeof raw === "number" && Number.isFinite(raw)) return raw * MM_PER_M;
    if (typeof raw === "string" && raw.trim() !== "") {
      texts[key] = raw.trim();
      return fallback;
    }
    return null;
  };
  const c = Array.isArray(rec.c) && rec.c.length === 2 ? [num(rec.c[0], "c.0"), num(rec.c[1], "c.1")] : [null, null];
  if (c[0] === null || c[1] === null) return null;
  const centre: Point = [c[0], c[1]];
  let entity: Shape;
  if (rec.kind === "rectangle") {
    const w = num(rec.w, "w");
    const h = num(rec.h, "h");
    if (w === null || h === null) return null;
    entity = { kind: "rectangle", c: centre, w: w > 0 ? w : 1, h: h > 0 ? h : 1 };
  } else if (rec.kind === "polygon") {
    const r = num(rec.r, "r");
    const n = rec.n;
    const angle = rec.angle;
    if (r === null || typeof n !== "number" || !Number.isInteger(n) || n < 3 || n > 64 || typeof angle !== "number") return null;
    entity = { kind: "polygon", c: centre, r: r > 0 ? r : 1, n, angle };
  } else return null;
  const count = entity.kind === "rectangle" ? 4 : entity.n;
  for (let k = 0; k < count; k++) {
    const e = list[start + k] as Record<string, unknown> | undefined;
    const s = e && typeof e === "object" ? (e.shape as Record<string, unknown> | undefined) : undefined;
    if (!e || e.kind !== "line" || !s || s.group !== rec.group) return null;
  }
  return { entity, count, texts };
}
