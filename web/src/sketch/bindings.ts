/**
 * Expressions on a drawn sketch's coordinates (ADR-0037): `W / 2` instead of 10 on a point, so the
 * drawing follows its dimensions, and the dimensions follow the project's parameters.
 *
 * A binding is an expression text on one coordinate of one entity, keyed `<entity>.<point>.<axis>`
 * (`3.b.0` is the u of entity 3's end), or on one of its sizes: `<entity>.r` for a circle's or a
 * polygon's radius, `<entity>.w` and `<entity>.h` for a rectangle's width and height. The editor
 * draws the value the server last evaluated for it (`expression.evaluate`); the stored form keeps
 * the text, which the server evaluates when it builds the part. Binding a point binds every
 * coordinate at that point on that axis, so joined ends stay joined. Pure: the tests run in Node.
 */
import { samePoint, type Entity, type Point } from "./model";
import { readShape, storedShape, type Coord } from "./shapes";

export type Bindings = Record<string, string>;
export type Axis = 0 | 1;

const MM_PER_M = 1000;
const POINT_KEYS = ["a", "b", "m", "c"] as const;

/** Every point of an entity with its key (`a`, `b`, `m`, `c`, or `p<n>` for a spline's points). */
function pointsOf(e: Entity): Array<[string, Point]> {
  switch (e.kind) {
    case "line":
      return [["a", e.a], ["b", e.b]];
    case "arc":
      return [["a", e.a], ["m", e.m], ["b", e.b]];
    case "circle":
    case "rectangle":
    case "polygon":
      return [["c", e.c]];
    case "spline":
      return e.points.map((p, n) => [`p${n}`, p]);
  }
}

/** The size keys an entity has besides its points: a circle's or polygon's `r`, a rectangle's `w`, `h`. */
export function scalarsOf(e: Entity): string[] {
  return e.kind === "circle" || e.kind === "polygon" ? ["r"] : e.kind === "rectangle" ? ["w", "h"] : [];
}

/** Bind (or, with null, free) the given axis of every coordinate at point `at`. */
export function bindPoint(entities: readonly Entity[], bindings: Bindings, at: Point, axis: Axis, expr: string | null): Bindings {
  const out = { ...bindings };
  entities.forEach((e, i) => {
    for (const [k, p] of pointsOf(e)) {
      if (!samePoint(p, at)) continue;
      const key = `${i}.${k}.${axis}`;
      if (expr === null) delete out[key];
      else out[key] = expr;
    }
  });
  return out;
}

/** Bind (or free) one size of an entity: `r`, `w` or `h`. */
export function bindScalar(bindings: Bindings, index: number, key: string, expr: string | null): Bindings {
  const out = { ...bindings };
  if (expr === null) delete out[`${index}.${key}`];
  else out[`${index}.${key}`] = expr;
  return out;
}

/** Bind (or free) a circle's radius. */
export function bindRadius(bindings: Bindings, index: number, expr: string | null): Bindings {
  return bindScalar(bindings, index, "r", expr);
}

/** Free every binding of entity `index` (its points and its sizes): it was reshaped by hand. */
export function freeEntity(bindings: Bindings, index: number): Bindings {
  return Object.fromEntries(Object.entries(bindings).filter(([k]) => k.split(".")[0] !== String(index)));
}

/** Free both axes of every coordinate at `at` (a point dragged by hand). */
export function freePoint(entities: readonly Entity[], bindings: Bindings, at: Point): Bindings {
  return bindPoint(entities, bindPoint(entities, bindings, at, 0, null), at, 1, null);
}

/** The binding of one coordinate, or undefined. */
export function bindingOf(bindings: Bindings, index: number, point: string, axis: Axis): string | undefined {
  return bindings[`${index}.${point}.${axis}`];
}

/** The bindings after entity `index` is deleted: its own go, the later ones move down. */
export function afterDelete(bindings: Bindings, index: number): Bindings {
  const out: Bindings = {};
  for (const [key, expr] of Object.entries(bindings)) {
    const [head, ...rest] = key.split(".");
    const i = Number(head);
    if (i === index) continue;
    out[[i > index ? i - 1 : i, ...rest].join(".")] = expr;
  }
  return out;
}

/** The distinct expression texts, for evaluation. */
export function expressionsOf(bindings: Bindings): string[] {
  return [...new Set(Object.values(bindings))];
}

/** The entities with each bound coordinate set to its expression's value (mm); unknown ones kept. */
export function applyValues(entities: readonly Entity[], bindings: Bindings, values: Record<string, number>): Entity[] {
  const value = (key: string, current: number): number => {
    const expr = bindings[key];
    const v = expr === undefined ? undefined : values[expr];
    return v !== undefined && Number.isFinite(v) ? v : current;
  };
  // A size must stay positive whatever an expression gives; a bad one keeps the drawn value.
  const size = (key: string, current: number): number => {
    const v = value(key, current);
    return v > 0 ? v : current;
  };
  const pt = (i: number, k: string, p: Point): Point => [value(`${i}.${k}.0`, p[0]), value(`${i}.${k}.1`, p[1])];
  return entities.map((e, i): Entity => {
    switch (e.kind) {
      case "line":
        return { ...e, a: pt(i, "a", e.a), b: pt(i, "b", e.b) };
      case "arc":
        return { ...e, a: pt(i, "a", e.a), m: pt(i, "m", e.m), b: pt(i, "b", e.b) };
      case "circle":
        return { ...e, c: pt(i, "c", e.c), r: size(`${i}.r`, e.r) };
      case "rectangle":
        return { ...e, c: pt(i, "c", e.c), w: size(`${i}.w`, e.w), h: size(`${i}.h`, e.h) };
      case "polygon":
        return { ...e, c: pt(i, "c", e.c), r: size(`${i}.r`, e.r) };
      case "spline":
        return { ...e, points: e.points.map((p, n) => pt(i, `p${n}`, p)) };
    }
  });
}

/**
 * The stored form (metres), with each bound coordinate as its expression text. A rectangle or
 * polygon is written as its lines; a bound size makes its corners expressions (`(W) / 2`).
 */
export function boundStored(entities: readonly Entity[], bindings: Bindings): unknown[] {
  const c = (key: string, mm: number): Coord => bindings[key] ?? mm / MM_PER_M;
  const pt = (i: number, k: string, p: Point): [Coord, Coord] => [c(`${i}.${k}.0`, p[0]), c(`${i}.${k}.1`, p[1])];
  return entities.flatMap((e, i): unknown[] => {
    switch (e.kind) {
      case "line":
        return [{ kind: "line", a: pt(i, "a", e.a), b: pt(i, "b", e.b) }];
      case "arc":
        return [{ kind: "arc", a: pt(i, "a", e.a), m: pt(i, "m", e.m), b: pt(i, "b", e.b) }];
      case "circle":
        return [{ kind: "circle", c: pt(i, "c", e.c), r: c(`${i}.r`, e.r) }];
      case "rectangle":
        return storedShape(e, i, pt(i, "c", e.c), { w: c(`${i}.w`, e.w), h: c(`${i}.h`, e.h) });
      case "polygon":
        return storedShape(e, i, pt(i, "c", e.c), { r: c(`${i}.r`, e.r) });
      case "spline":
        return [{ kind: "spline", points: e.points.map((p, n) => pt(i, `p${n}`, p)) }];
    }
  });
}

/**
 * The stored entities as numbers (mm) and bindings: an expression text becomes a binding, and its
 * coordinate starts at `fallback` (mm) until the server's value arrives. A shape's lines come back
 * as the one shape. Malformed entities are dropped, as `fromStored` drops them.
 */
export function parseStored(value: unknown, fallback = 0): { entities: Entity[]; bindings: Bindings } {
  let list = value;
  if (typeof value === "string") {
    try {
      list = JSON.parse(value);
    } catch {
      return { entities: [], bindings: {} };
    }
  }
  if (!Array.isArray(list)) return { entities: [], bindings: {} };
  const entities: Entity[] = [];
  const bindings: Bindings = {};
  const num = (raw: unknown, key: string): number | null => {
    if (typeof raw === "number" && Number.isFinite(raw)) return raw * MM_PER_M;
    if (typeof raw === "string" && raw.trim() !== "") {
      bindings[key] = raw.trim();
      return fallback;
    }
    return null;
  };
  const point = (raw: unknown, i: number, k: string): Point | null => {
    if (!Array.isArray(raw) || raw.length !== 2) return null;
    const u = num(raw[0], `${i}.${k}.0`);
    const v = num(raw[1], `${i}.${k}.1`);
    return u === null || v === null ? null : [u, v];
  };
  const items = list as unknown[];
  for (let at = 0; at < items.length; at++) {
    const i = entities.length;
    const shape = readShape(items, at, fallback);
    if (shape !== null) {
      entities.push(shape.entity);
      for (const [k, text] of Object.entries(shape.texts)) bindings[`${i}.${k}`] = text;
      at += shape.count - 1;
      continue;
    }
    const raw = items[at];
    if (typeof raw !== "object" || raw === null) continue;
    const e = raw as Record<string, unknown>;
    if (e.kind === "line") {
      const a = point(e.a, i, "a");
      const b = point(e.b, i, "b");
      if (a && b) entities.push({ kind: "line", a, b });
    } else if (e.kind === "arc") {
      const a = point(e.a, i, "a");
      const m = point(e.m, i, "m");
      const b = point(e.b, i, "b");
      if (a && m && b) entities.push({ kind: "arc", a, m, b });
    } else if (e.kind === "circle") {
      const c = point(e.c, i, "c");
      const r = num(e.r, `${i}.r`);
      if (c && r !== null) entities.push({ kind: "circle", c, r: r > 0 ? r : 1 });
    } else if (e.kind === "spline" && Array.isArray(e.points)) {
      const points = e.points.map((p, n) => point(p, i, `p${n}`)).filter((p): p is Point => p !== null);
      if (points.length >= 3) entities.push({ kind: "spline", points });
    }
  }
  return { entities, bindings };
}

export { POINT_KEYS };
