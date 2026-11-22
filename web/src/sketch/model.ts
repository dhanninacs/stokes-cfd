/**
 * The sketcher's model (ADR-0036 decisions 1, 2 and 4): entities in a sketch plane's (u, v), the
 * tools' click rules, snapping, and the live check of the profile's loops. Pure data and pure
 * functions: the editor (`SketchEditor.tsx`) draws and dispatches, the tests run in Node.
 *
 * Coordinates are millimetres here (what the user reads and types); the stored form of the
 * `implicit_sketch` operation's `entities` parameter is metres (`toStored`, `fromStored`).
 * Rectangles and polygons are one entity each here and their lines in the stored form
 * (`shapes.ts`).
 */
import { explode, isShape, linesOf, moveShapePoint, polygonFrom, readShape, rectangleFrom, shapeTargets, storedShape } from "./shapes";

export type Point = [number, number];

export type Entity =
  | { kind: "line"; a: Point; b: Point }
  | { kind: "arc"; a: Point; m: Point; b: Point }
  | { kind: "circle"; c: Point; r: number }
  | { kind: "spline"; points: Point[] }
  /** Axis-aligned, by its centre and its sizes along u (w) and v (h). */
  | { kind: "rectangle"; c: Point; w: number; h: number }
  /** Regular, by its centre, corner radius, number of sides and the angle of its first corner (rad). */
  | { kind: "polygon"; c: Point; r: number; n: number; angle: number };

export type Tool = "select" | "line" | "rectangle" | "circle" | "arc" | "spline" | "polygon";

/** The clicks a tool has taken so far, before it makes its entities. */
export interface Draft {
  tool: Tool;
  points: Point[];
}

/** The tools, each with its single-key shortcut while the sketch is open. */
export const TOOLS: ReadonlyArray<{ id: Tool; label: string; key: string; hint: string }> = [
  { id: "select", label: "Select", key: "V", hint: "Click to select, drag a point to move it, Delete removes the selection" },
  { id: "line", label: "Line", key: "L", hint: "Click points, or type a length, Tab, an angle, Enter; joining an open end ends the chain; right-click or Esc stops" },
  { id: "rectangle", label: "Rectangle", key: "R", hint: "Click a corner, then the opposite one; or type a width, Tab, a height, Enter" },
  { id: "circle", label: "Circle", key: "C", hint: "Click the centre, then a point on it; or type a radius (Tab for a diameter), Enter" },
  { id: "arc", label: "Arc", key: "A", hint: "Click the start, the end, then a point the arc passes through" },
  { id: "spline", label: "Spline", key: "S", hint: "Click the points the curve passes through; Enter, double-click or right-click to finish" },
  { id: "polygon", label: "Polygon", key: "P", hint: "Click the centre, then a corner; or type the corner radius, Enter" },
];

const MM_PER_M = 1000;

/** The same point, to the nanometre (the backend joins endpoints within 1e-9 m). */
export function samePoint(p: Point, q: Point): boolean {
  return Math.abs(p[0] - q[0]) < 1e-6 && Math.abs(p[1] - q[1]) < 1e-6;
}

// ------------------------------------------------------------------------------- stored form
export function toStored(entities: readonly Entity[]): unknown[] {
  const m = (p: Point): Point => [p[0] / MM_PER_M, p[1] / MM_PER_M];
  return entities.flatMap((e, i): unknown[] => {
    switch (e.kind) {
      case "rectangle":
      case "polygon":
        return storedShape(e, i, m(e.c), {});
      case "line":
        return [{ kind: "line", a: m(e.a), b: m(e.b) }];
      case "arc":
        return [{ kind: "arc", a: m(e.a), m: m(e.m), b: m(e.b) }];
      case "circle":
        return [{ kind: "circle", c: m(e.c), r: e.r / MM_PER_M }];
      case "spline":
        return [{ kind: "spline", points: e.points.map(m) }];
    }
  });
}

function asPoint(value: unknown): Point | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const [u, v] = value;
  return typeof u === "number" && typeof v === "number" && Number.isFinite(u) && Number.isFinite(v) ? [u * MM_PER_M, v * MM_PER_M] : null;
}

/** The stored entities (metres), or the JSON text of them, as editor entities; bad ones are dropped. */
export function fromStored(value: unknown): Entity[] {
  let list = value;
  if (typeof value === "string") {
    try {
      list = JSON.parse(value);
    } catch {
      return [];
    }
  }
  if (!Array.isArray(list)) return [];
  const out: Entity[] = [];
  const items = list as unknown[];
  for (let at = 0; at < items.length; at++) {
    const shape = readShape(items, at, 0);
    if (shape !== null) {
      out.push(shape.entity);
      at += shape.count - 1;
      continue;
    }
    const raw = items[at];
    if (typeof raw !== "object" || raw === null) continue;
    const e = raw as Record<string, unknown>;
    if (e.kind === "line") {
      const a = asPoint(e.a);
      const b = asPoint(e.b);
      if (a && b) out.push({ kind: "line", a, b });
    } else if (e.kind === "arc") {
      const a = asPoint(e.a);
      const m = asPoint(e.m);
      const b = asPoint(e.b);
      if (a && m && b) out.push({ kind: "arc", a, m, b });
    } else if (e.kind === "circle") {
      const c = asPoint(e.c);
      if (c && typeof e.r === "number" && e.r > 0) out.push({ kind: "circle", c, r: e.r * MM_PER_M });
    } else if (e.kind === "spline" && Array.isArray(e.points)) {
      const points = e.points.map(asPoint).filter((p): p is Point => p !== null);
      if (points.length >= 3) out.push({ kind: "spline", points });
    }
  }
  return out;
}

// ------------------------------------------------------------------------------- geometry
/** The ends of an open entity (none for a circle or a shape). */
export function endsOf(e: Entity): Point[] {
  if (e.kind === "line" || e.kind === "arc") return [e.a, e.b];
  if (e.kind === "spline") return [e.points[0], e.points[e.points.length - 1]];
  return [];
}

/** Every point a snap may land on: ends, midpoints of lines, centres of circles. */
export function snapTargets(entities: readonly Entity[]): Point[] {
  const out: Point[] = [];
  for (const e of entities) {
    out.push(...endsOf(e));
    if (e.kind === "line") out.push([(e.a[0] + e.b[0]) / 2, (e.a[1] + e.b[1]) / 2]);
    if (e.kind === "circle") out.push(e.c);
    if (e.kind === "arc") out.push(e.m);
    if (e.kind === "spline") out.push(...e.points.slice(1, -1));
    if (isShape(e)) out.push(...shapeTargets(e));
  }
  return out;
}

/** Points that bound an entity, for fitting the view. */
export function extentOf(e: Entity): Point[] {
  switch (e.kind) {
    case "circle":
      return [[e.c[0] - e.r, e.c[1] - e.r], [e.c[0] + e.r, e.c[1] + e.r]];
    case "spline":
      return e.points;
    case "arc":
      return arcPoints(e.a, e.m, e.b, 16);
    case "line":
      return [e.a, e.b];
    default:
      return linesOf(e).map((l) => (l as { a: Point }).a);
  }
}

/** The circle through three points, or null when they lie on a line. */
export function circleThrough(a: Point, m: Point, b: Point): { c: Point; r: number } | null {
  const d = 2 * (a[0] * (m[1] - b[1]) + m[0] * (b[1] - a[1]) + b[0] * (a[1] - m[1]));
  if (Math.abs(d) < 1e-12) return null;
  const a2 = a[0] ** 2 + a[1] ** 2;
  const m2 = m[0] ** 2 + m[1] ** 2;
  const b2 = b[0] ** 2 + b[1] ** 2;
  const cx = (a2 * (m[1] - b[1]) + m2 * (b[1] - a[1]) + b2 * (a[1] - m[1])) / d;
  const cy = (a2 * (b[0] - m[0]) + m2 * (a[0] - b[0]) + b2 * (m[0] - a[0])) / d;
  return { c: [cx, cy], r: Math.hypot(a[0] - cx, a[1] - cy) };
}

/** Points along the arc from a through m to b (a straight line when they are collinear). */
export function arcPoints(a: Point, m: Point, b: Point, n = 48): Point[] {
  const circle = circleThrough(a, m, b);
  if (circle === null) return [a, b];
  const angle = (p: Point) => Math.atan2(p[1] - circle.c[1], p[0] - circle.c[0]);
  const ccw = (s: number, e: number) => (((e - s) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  let sweep = ccw(angle(a), angle(b));
  if (ccw(angle(a), angle(m)) > sweep) sweep -= 2 * Math.PI;
  const start = angle(a);
  return Array.from({ length: n + 1 }, (_, i) => {
    if (i === 0) return a;
    if (i === n) return b;
    const t = start + (sweep * i) / n;
    return [circle.c[0] + circle.r * Math.cos(t), circle.c[1] + circle.r * Math.sin(t)] as Point;
  });
}

/** The SVG path of an arc from a through m to b. */
export function arcPath(a: Point, m: Point, b: Point, y: (v: number) => number, x: (u: number) => number, scale: number): string {
  const circle = circleThrough(a, m, b);
  if (circle === null) return `M ${x(a[0])} ${y(a[1])} L ${x(b[0])} ${y(b[1])}`;
  const angle = (p: Point) => Math.atan2(p[1] - circle.c[1], p[0] - circle.c[0]);
  const ccw = (s: number, e: number) => (((e - s) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  let sweep = ccw(angle(a), angle(b));
  const counter = ccw(angle(a), angle(m)) <= sweep;
  if (!counter) sweep = 2 * Math.PI - sweep;
  const large = sweep > Math.PI ? 1 : 0;
  // Screen y points down: counter-clockwise in the sketch is clockwise on screen (sweep flag 0).
  const flag = counter ? 0 : 1;
  const r = circle.r * scale;
  return `M ${x(a[0])} ${y(a[1])} A ${r} ${r} 0 ${large} ${flag} ${x(b[0])} ${y(b[1])}`;
}

/** Catmull-Rom points of a spline, for drawing (the backend tessellates the same curve). */
export function splinePoints(points: readonly Point[], per = 12): Point[] {
  if (points.length < 2) return [...points];
  const padded = [points[0], ...points, points[points.length - 1]];
  const out: Point[] = [points[0]];
  for (let i = 1; i < padded.length - 2; i++) {
    const [p0, p1, p2, p3] = [padded[i - 1], padded[i], padded[i + 1], padded[i + 2]];
    for (let k = 1; k <= per; k++) {
      const t = k / per;
      const t2 = t * t;
      const t3 = t2 * t;
      const c = (j: 0 | 1) => 0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2 + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3);
      out.push([c(0), c(1)]);
    }
  }
  return out;
}

/** The regular polygon of `sides` with its centre and one corner, as lines. */
export function polygonLines(centre: Point, corner: Point, sides: number): Entity[] {
  const n = Math.max(3, Math.min(64, Math.round(sides)));
  const r = Math.hypot(corner[0] - centre[0], corner[1] - centre[1]);
  const start = Math.atan2(corner[1] - centre[1], corner[0] - centre[0]);
  const at = (k: number): Point => {
    const angle = start + (2 * Math.PI * k) / n;
    return k % n === 0 ? corner : [centre[0] + r * Math.cos(angle), centre[1] + r * Math.sin(angle)];
  };
  return Array.from({ length: n }, (_, k) => ({ kind: "line" as const, a: at(k), b: at(k + 1) }));
}

export function rectangleLines(p: Point, q: Point): Entity[] {
  const c: Point[] = [p, [q[0], p[1]], q, [p[0], q[1]]];
  return c.map((a, i) => ({ kind: "line" as const, a, b: c[(i + 1) % 4] }));
}

// ------------------------------------------------------------------------------- snapping
export interface Snap {
  point: Point;
  /** What it snapped to, for the cursor's label. */
  kind: "point" | "grid" | "horizontal" | "vertical" | "free";
}

/**
 * The point a click at `raw` means: an existing point within `radius` (mm), else the grid, with
 * horizontal and vertical alignment to `from` (the draft's last point) inside the same radius.
 */
export function snap(raw: Point, targets: readonly Point[], grid: number, radius: number, from?: Point | null): Snap {
  let best: Point | null = null;
  let bestD = radius;
  for (const t of targets) {
    const d = Math.hypot(t[0] - raw[0], t[1] - raw[1]);
    if (d <= bestD) {
      best = t;
      bestD = d;
    }
  }
  if (best !== null) return { point: [best[0], best[1]], kind: "point" };
  let point: Point = grid > 0 ? [Math.round(raw[0] / grid) * grid, Math.round(raw[1] / grid) * grid] : [raw[0], raw[1]];
  let kind: Snap["kind"] = grid > 0 ? "grid" : "free";
  if (from) {
    if (Math.abs(raw[1] - from[1]) < radius) {
      point = [point[0], from[1]];
      kind = "horizontal";
    } else if (Math.abs(raw[0] - from[0]) < radius) {
      point = [from[0], point[1]];
      kind = "vertical";
    }
  }
  return { point, kind };
}

/** A grid step of 1, 2 or 5 × 10^n mm that is about `pixels` on screen at `scale` px per mm. */
export function gridStep(scale: number, pixels = 24): number {
  const raw = pixels / Math.max(scale, 1e-9);
  const power = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * power >= raw) return m * power;
  return 10 * power;
}

// ------------------------------------------------------------------------------- tools
export interface ToolResult {
  /** Entities the click completed. */
  add: Entity[];
  /** The draft after the click (null: the tool is ready for a new shape). */
  draft: Draft | null;
}

/**
 * One click of a drawing tool at `p`; `sides` is the polygon tool's count. `openEnds` are the
 * sketch's open ends (`checkProfile(...).open`): a line that lands on one ends its chain there, as it
 * does on the chain's own start (review A9).
 */
export function click(tool: Tool, draft: Draft | null, p: Point, sides = 6, openEnds: readonly Point[] = []): ToolResult {
  const points = draft?.tool === tool ? draft.points : [];
  const next = [...points, p];
  switch (tool) {
    case "line": {
      if (points.length === 0) return { add: [], draft: { tool, points: next } };
      const last = points[points.length - 1];
      if (samePoint(last, p)) return { add: [], draft: { tool, points } };
      const line: Entity = { kind: "line", a: last, b: p };
      // Back on the chain's first point: the loop is closed and the chain ends.
      if (points.length >= 2 && samePoint(points[0], p)) return { add: [line], draft: null };
      if (openEnds.some((q) => samePoint(q, p) && !samePoint(q, points[0]))) return { add: [line], draft: null };
      return { add: [line], draft: { tool, points: next } };
    }
    case "rectangle":
      if (points.length === 0) return { add: [], draft: { tool, points: next } };
      if (samePoint(points[0], p) || points[0][0] === p[0] || points[0][1] === p[1]) return { add: [], draft: { tool, points } };
      return { add: [rectangleFrom(points[0], p)], draft: null };
    case "circle": {
      if (points.length === 0) return { add: [], draft: { tool, points: next } };
      const r = Math.hypot(p[0] - points[0][0], p[1] - points[0][1]);
      if (r <= 1e-9) return { add: [], draft: { tool, points } };
      return { add: [{ kind: "circle", c: points[0], r }], draft: null };
    }
    case "arc":
      if (points.length < 2) return { add: [], draft: { tool, points: points.length === 1 && samePoint(points[0], p) ? points : next } };
      if (circleThrough(points[0], p, points[1]) === null) return { add: [], draft: { tool, points } };
      return { add: [{ kind: "arc", a: points[0], m: p, b: points[1] }], draft: null };
    case "spline":
      if (points.length > 0 && samePoint(points[points.length - 1], p)) return finishSpline(points);
      return { add: [], draft: { tool, points: next } };
    case "polygon": {
      if (points.length === 0) return { add: [], draft: { tool, points: next } };
      if (samePoint(points[0], p)) return { add: [], draft: { tool, points } };
      return { add: [polygonFrom(points[0], p, sides)], draft: null };
    }
    default:
      return { add: [], draft: null };
  }
}

/** Ends a spline draft: the spline when it has three points or more. */
export function finishSpline(points: readonly Point[]): ToolResult {
  return { add: points.length >= 3 ? [{ kind: "spline", points: [...points] }] : [], draft: null };
}

/**
 * Moves every point equal to `from` to `to`, in every entity, so joined ends stay joined. A shape
 * stays its shape (`moveShapePoint`): its corner resizes it, its centre moves it.
 */
export function movePoint(entities: readonly Entity[], from: Point, to: Point): Entity[] {
  const m = (p: Point): Point => (samePoint(p, from) ? [to[0], to[1]] : p);
  return entities.map((e): Entity => {
    switch (e.kind) {
      case "rectangle":
      case "polygon":
        return moveShapePoint(e, from, to);
      case "line":
        return { ...e, a: m(e.a), b: m(e.b) };
      case "arc":
        return { ...e, a: m(e.a), m: m(e.m), b: m(e.b) };
      case "circle":
        return { ...e, c: m(e.c) };
      case "spline":
        return { ...e, points: e.points.map(m) };
    }
  });
}

/** The distance from `p` to entity `e` (mm), for picking. */
export function distanceTo(e: Entity, p: Point): number {
  const seg = (a: Point, b: Point) => {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len2 = dx * dx + dy * dy || 1e-18;
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2));
    return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
  };
  const poly = (pts: readonly Point[]) => pts.slice(1).reduce((d, q, i) => Math.min(d, seg(pts[i], q)), Infinity);
  switch (e.kind) {
    case "line":
      return seg(e.a, e.b);
    case "circle":
      return Math.abs(Math.hypot(p[0] - e.c[0], p[1] - e.c[1]) - e.r);
    case "arc":
      return poly(arcPoints(e.a, e.m, e.b));
    case "spline":
      return poly(splinePoints(e.points));
    default:
      return Math.min(...linesOf(e).map((l) => distanceTo(l, p)));
  }
}

// ------------------------------------------------------------------------------- the profile
export interface ProfileCheck {
  /** Closed loops (a circle counts as one). */
  loops: number;
  /** Ends that are not joined to exactly one other end: where the profile is open. */
  open: Point[];
  /** Points where three or more ends meet. */
  branches: Point[];
  /** What to tell the user, empty when the profile is ready to pull or revolve. */
  message: string;
}

/** The live check of the sketch: are its entities closed loops? (Crossings are checked by the server.) */
export function checkProfile(drawn: readonly Entity[]): ProfileCheck {
  // Shapes are checked as the lines the server receives: a line joined to a corner is a branch.
  const entities = explode(drawn);
  const ends = new Map<string, { point: Point; count: number }>();
  const key = (p: Point) => `${Math.round(p[0] * 1e6)},${Math.round(p[1] * 1e6)}`;
  let circles = 0;
  for (const e of entities) {
    if (e.kind === "circle") {
      circles += 1;
      continue;
    }
    for (const p of endsOf(e)) {
      const k = key(p);
      const at = ends.get(k);
      if (at) at.count += 1;
      else ends.set(k, { point: p, count: 1 });
    }
  }
  const open: Point[] = [];
  const branches: Point[] = [];
  for (const { point, count } of ends.values()) {
    if (count === 1) open.push(point);
    else if (count > 2) branches.push(point);
  }
  // Loops among the open entities: chains closing on themselves (union-find over shared ends).
  const parent = new Map<string, string>();
  const find = (k: string): string => {
    let r = k;
    while (parent.get(r) !== r) r = parent.get(r) ?? r;
    return r;
  };
  for (const k of ends.keys()) parent.set(k, k);
  for (const e of entities) {
    const [a, b] = endsOf(e);
    if (a && b) parent.set(find(key(a)), find(key(b)));
  }
  const roots = new Set<string>();
  for (const k of ends.keys()) roots.add(find(k));
  const loops = open.length === 0 && branches.length === 0 ? roots.size + circles : circles;
  let message = "";
  if (entities.length === 0) message = "Draw a closed profile: lines, rectangles, circles, arcs, splines";
  else if (open.length > 0) message = `The profile is open at ${open.length} point${open.length === 1 ? "" : "s"} (marked): join the ends`;
  else if (branches.length > 0) message = "Three or more entities meet at a point (marked): a profile's loops may only touch end to end";
  return { loops, open, branches, message };
}

// ------------------------------------------------------------------------------- fillet
/**
 * Round the sharp corners where two lines meet with tangent arcs of `radius` (mm).
 *
 * A corner is a point that is the end of exactly two lines and of nothing else. Each line is cut
 * back by `radius / tan(angle / 2)` and a three-point arc joins the cuts, tangent to both, so the
 * loop stays closed and the pulled body has a round face in place of an edge. `only` limits the
 * work to corners between lines of that set (the selection); without it every corner is rounded.
 *
 * A corner is left as it is when its lines are in line with each other, or when a line is too
 * short to give up the cut (counting what its other end already gave). The arcs are appended, so
 * the entities before them keep their indices and their bindings; `corners` lists the points that
 * were rounded away, whose own bindings the caller frees.
 */
export function filletCorners(entities: readonly Entity[], radius: number, only?: ReadonlySet<number>): { entities: Entity[]; corners: Point[] } {
  if (!(radius > 0) || !Number.isFinite(radius)) return { entities: [...entities], corners: [] };
  const out: Entity[] = entities.map((e) => ({ ...e }) as Entity);
  // Every end of every open entity, to find the points where exactly two lines meet.
  const ends: { index: number; key: "a" | "b"; at: Point; line: boolean }[] = [];
  entities.forEach((e, index) => {
    if (e.kind === "line" || e.kind === "arc") {
      ends.push({ index, key: "a", at: e.a, line: e.kind === "line" }, { index, key: "b", at: e.b, line: e.kind === "line" });
    } else if (e.kind === "spline") {
      ends.push({ index, key: "a", at: e.points[0], line: false }, { index, key: "b", at: e.points[e.points.length - 1], line: false });
    }
  });
  const length = (i: number): number => {
    const e = entities[i] as Extract<Entity, { kind: "line" }>;
    return Math.hypot(e.b[0] - e.a[0], e.b[1] - e.a[1]);
  };
  const given = new Map<number, number>();
  const corners: Point[] = [];
  const arcs: Entity[] = [];
  const done = new Set<number>();
  ends.forEach((first, n) => {
    if (done.has(n)) return;
    const here = ends.map((e, k) => ({ e, k })).filter(({ e }) => samePoint(e.at, first.at));
    for (const { k } of here) done.add(k);
    if (here.length !== 2 || !here.every(({ e }) => e.line) || here[0].e.index === here[1].e.index) return;
    const [p, q] = [here[0].e, here[1].e];
    if (only !== undefined && !(only.has(p.index) && only.has(q.index))) return;
    const far = (end: typeof p): Point => {
      const e = entities[end.index] as Extract<Entity, { kind: "line" }>;
      return end.key === "a" ? e.b : e.a;
    };
    const corner = first.at;
    const [a, b] = [far(p), far(q)];
    const [la, lb] = [length(p.index), length(q.index)];
    if (la < 1e-9 || lb < 1e-9) return;
    const u: Point = [(a[0] - corner[0]) / la, (a[1] - corner[1]) / la];
    const v: Point = [(b[0] - corner[0]) / lb, (b[1] - corner[1]) / lb];
    const cos = Math.max(-1, Math.min(1, u[0] * v[0] + u[1] * v[1]));
    const angle = Math.acos(cos);
    // In line with each other (a straight run or a fold back): no corner to round.
    if (angle < 1e-3 || Math.PI - angle < 1e-3) return;
    const cut = radius / Math.tan(angle / 2);
    const [usedA, usedB] = [given.get(p.index) ?? 0, given.get(q.index) ?? 0];
    if (usedA + cut > la - 1e-6 || usedB + cut > lb - 1e-6) return;
    given.set(p.index, usedA + cut);
    given.set(q.index, usedB + cut);
    const t1: Point = [corner[0] + u[0] * cut, corner[1] + u[1] * cut];
    const t2: Point = [corner[0] + v[0] * cut, corner[1] + v[1] * cut];
    const bis = Math.hypot(u[0] + v[0], u[1] + v[1]);
    const w: Point = [(u[0] + v[0]) / bis, (u[1] + v[1]) / bis];
    // The arc's middle: on the bisector, the centre's distance less the radius from the corner.
    const reach = radius / Math.sin(angle / 2) - radius;
    const m: Point = [corner[0] + w[0] * reach, corner[1] + w[1] * reach];
    (out[p.index] as Extract<Entity, { kind: "line" }>)[p.key] = t1;
    (out[q.index] as Extract<Entity, { kind: "line" }>)[q.key] = t2;
    arcs.push({ kind: "arc", a: t1, m, b: t2 });
    corners.push(corner);
  });
  return { entities: [...out, ...arcs], corners };
}
