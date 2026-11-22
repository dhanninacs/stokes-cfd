/**
 * A sketch's profile as closed polylines in the plane (ADR-0039): what the client-side ghost
 * extrudes while the mesher works, and what the default pull depth is measured from.
 *
 * Drawn profiles chain their lines, arcs and splines end to end (as the server does, ADR-0036
 * decision 2); a circle is a loop by itself. Named profiles (rectangle, circle…) are drawn centred
 * on the plane's origin, as `stokes/implicit/sketch.py` builds them. Millimetres throughout. Pure:
 * the tests run in Node.
 */
import { arcPoints, samePoint, splinePoints, type Entity, type Point } from "../sketch/model";
import { explode } from "../sketch/shapes";

export type Loop = Point[];

const CIRCLE_SEGMENTS = 64;

function circleLoop(c: Point, rx: number, ry = rx, n = CIRCLE_SEGMENTS): Loop {
  const out: Loop = [];
  for (let i = 0; i < n; i++) {
    const t = (2 * Math.PI * i) / n;
    out.push([c[0] + rx * Math.cos(t), c[1] + ry * Math.sin(t)]);
  }
  return out;
}

/** The points of one open entity from its start to its end. */
function polyline(e: Entity): Point[] {
  switch (e.kind) {
    case "line":
      return [e.a, e.b];
    case "arc":
      return arcPoints(e.a, e.m, e.b);
    case "spline":
      return splinePoints(e.points);
    default:
      return [];
  }
}

/**
 * The closed loops of drawn entities, or null when they do not close (an open end or a branch):
 * a ghost of an open profile would show a body the server will refuse.
 */
export function drawnLoops(entities: readonly Entity[]): Loop[] | null {
  const loops: Loop[] = [];
  const open: Point[][] = [];
  for (const e of explode(entities)) {
    if (e.kind === "circle") loops.push(circleLoop(e.c, e.r));
    else {
      const p = polyline(e);
      if (p.length >= 2) open.push(p);
    }
  }
  const used = new Array<boolean>(open.length).fill(false);
  for (let s = 0; s < open.length; s++) {
    if (used[s]) continue;
    used[s] = true;
    const loop: Point[] = [...open[s]];
    const start = loop[0];
    let guard = open.length + 1;
    while (!samePoint(loop[loop.length - 1], start) && guard-- > 0) {
      const end = loop[loop.length - 1];
      let found = false;
      for (let i = 0; i < open.length && !found; i++) {
        if (used[i]) continue;
        const p = open[i];
        if (samePoint(p[0], end)) loop.push(...p.slice(1));
        else if (samePoint(p[p.length - 1], end)) loop.push(...[...p].reverse().slice(1));
        else continue;
        used[i] = true;
        found = true;
      }
      if (!found) return null;
    }
    if (!samePoint(loop[loop.length - 1], start)) return null;
    loop.pop();
    if (loop.length >= 3) loops.push(loop);
  }
  return loops.length > 0 ? loops : null;
}

/** The sizes a named profile reads, in mm (the operation's parameters). */
export interface NamedSizes {
  width: number;
  height: number;
  radius: number;
  corner_radius: number;
  slot_length: number;
  sides: number;
}

/** The loop of a named profile, centred on the origin; null for a kind this does not draw. */
export function namedLoops(kind: string, s: NamedSizes): Loop[] | null {
  const hu = s.width / 2;
  const hv = s.height / 2;
  switch (kind) {
    case "rectangle":
      return hu > 0 && hv > 0 ? [[[-hu, -hv], [hu, -hv], [hu, hv], [-hu, hv]]] : null;
    case "rounded_rectangle": {
      if (!(hu > 0 && hv > 0)) return null;
      const r = Math.max(0, Math.min(s.corner_radius, hu, hv));
      if (r <= 0) return namedLoops("rectangle", s);
      const loop: Loop = [];
      const corners: Array<[number, number, number]> = [[hu - r, hv - r, 0], [-hu + r, hv - r, 90], [-hu + r, -hv + r, 180], [hu - r, -hv + r, 270]];
      for (const [cx, cy, a0] of corners) {
        for (let i = 0; i <= 8; i++) {
          const t = ((a0 + (90 * i) / 8) * Math.PI) / 180;
          loop.push([cx + r * Math.cos(t), cy + r * Math.sin(t)]);
        }
      }
      return [loop];
    }
    case "circle":
      return s.radius > 0 ? [circleLoop([0, 0], s.radius)] : null;
    case "ellipse":
      return hu > 0 && hv > 0 ? [circleLoop([0, 0], hu, hv)] : null;
    case "slot": {
      const r = s.radius;
      const h = s.slot_length / 2;
      if (!(r > 0) || !(h >= 0)) return null;
      const loop: Loop = [];
      for (let i = 0; i <= 16; i++) {
        const t = -Math.PI / 2 + (Math.PI * i) / 16;
        loop.push([h + r * Math.cos(t), r * Math.sin(t)]);
      }
      for (let i = 0; i <= 16; i++) {
        const t = Math.PI / 2 + (Math.PI * i) / 16;
        loop.push([-h + r * Math.cos(t), r * Math.sin(t)]);
      }
      return [loop];
    }
    case "polygon": {
      const n = Math.round(s.sides);
      if (!(n >= 3 && n <= 64) || !(s.radius > 0)) return null;
      // A flat side at the bottom: the corners start half a side past straight down.
      const start = -Math.PI / 2 + Math.PI / n;
      return [Array.from({ length: n }, (_, i): Point => [s.radius * Math.cos(start + (2 * Math.PI * i) / n), s.radius * Math.sin(start + (2 * Math.PI * i) / n)])];
    }
    default:
      return null;
  }
}

/** The bounding box of loops: [min u, min v, max u, max v], or null for none. */
export function loopsBox(loops: readonly Loop[]): [number, number, number, number] | null {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const loop of loops) {
    for (const [x, y] of loop) {
      x0 = Math.min(x0, x);
      y0 = Math.min(y0, y);
      x1 = Math.max(x1, x);
      y1 = Math.max(y1, y);
    }
  }
  return x0 <= x1 && y0 <= y1 ? [x0, y0, x1, y1] : null;
}

/** Whether `p` is inside the polygon `loop` (even-odd). */
export function inside(p: Point, loop: Loop): boolean {
  let hit = false;
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const [xi, yi] = loop[i];
    const [xj, yj] = loop[j];
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

/**
 * Loops as outlines with their holes (ADR-0036 decision 2's even-odd rule): a loop inside an even
 * number of others is an outline, inside an odd number a hole of the smallest outline around it.
 */
export function nest(loops: readonly Loop[]): Array<{ outer: Loop; holes: Loop[] }> {
  const depth = loops.map((l, i) => loops.filter((m, j) => j !== i && inside(l[0], m)).length);
  const area = (l: Loop) => Math.abs(l.reduce((s, p, i) => s + p[0] * l[(i + 1) % l.length][1] - l[(i + 1) % l.length][0] * p[1], 0)) / 2;
  const out = new Map<number, { outer: Loop; holes: Loop[] }>();
  loops.forEach((l, i) => {
    if (depth[i] % 2 === 0) out.set(i, { outer: l, holes: [] });
  });
  loops.forEach((l, i) => {
    if (depth[i] % 2 === 0) return;
    let best = -1;
    for (const [j, shape] of out) {
      if (depth[j] === depth[i] - 1 && inside(l[0], shape.outer) && (best < 0 || area(shape.outer) < area(loops[best]))) best = j;
    }
    if (best >= 0) out.get(best)?.holes.push(l);
  });
  return [...out.values()];
}
