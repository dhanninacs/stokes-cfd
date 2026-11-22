/**
 * What a pull handle needs to know about the operation that made a body (ADR-0039): its plane,
 * origin, depth and profile, read off the `implicit_sketch` node the tree holds. Pure: the tests
 * run in Node. The handle reads; the depth it writes goes through `parameter.update`.
 */
import type { NodeSummary, ParamValue } from "../api/types";
import { isQuantity } from "../api/types";
import { parseStored } from "../sketch/bindings";
import { isPlane, originOf, type PlaneId, type Vec3 } from "./plane";
import { drawnLoops, namedLoops, type Loop } from "./profile";

/** An extrude a handle can pull. */
export interface PullInfo {
  opId: string;
  plane: PlaneId;
  origin: Vec3;
  /** Millimetres. */
  depth: number;
  symmetric: boolean;
  /** The profile's loops (mm), or null when they cannot be drawn here (a bound coordinate, an open profile). */
  loops: Loop[] | null;
  /** The depth's expression when it is more than a plain length (`2 * W`): a drag would cut the link, so it does not drag. */
  expression: string | null;
}

/** SI value of a quantity parameter, or `fallback`. */
function si(value: ParamValue | undefined, fallback: number): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (isQuantity(value) && Number.isFinite(value.value)) return value.value;
  return fallback;
}

const PLAIN = /^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*(\[\s*[a-zA-Zµ]+\s*\]|[a-zA-Zµ]+)?\s*$/;

/** The pull of `op`, or null when it is not an extrude of a sketch. */
export function pullOf(op: NodeSummary | null): PullInfo | null {
  if (op === null || op.type !== "implicit_sketch") return null;
  const p = op.params;
  if (p.method === "revolve") return null;
  const plane = isPlane(p.plane) ? p.plane : "xy";
  const depth = si(p.depth, 0.01) * 1000;
  if (!(depth > 0)) return null;
  const raw = p.depth;
  const expr = isQuantity(raw as ParamValue) ? ((raw as { expr?: string }).expr ?? null) : null;
  let loops: Loop[] | null;
  const profile = typeof p.profile === "string" ? p.profile : "rectangle";
  if (profile === "custom") {
    const { entities, bindings } = parseStored(p.entities);
    loops = Object.keys(bindings).length === 0 ? drawnLoops(entities) : null;
  } else {
    const mm = (name: string, fallback: number) => si(p[name], fallback) * 1000;
    loops = namedLoops(profile, {
      width: mm("width", 0.01),
      height: mm("height", 0.01),
      radius: mm("radius", 0.005),
      corner_radius: mm("corner_radius", 0.001),
      slot_length: mm("slot_length", 0.01),
      sides: typeof p.sides === "number" ? p.sides : si(p.sides, 6),
    });
  }
  return {
    opId: op.id,
    plane,
    origin: originOf(p.origin),
    depth,
    symmetric: p.symmetric === true,
    loops,
    expression: expr !== null && !PLAIN.test(expr) ? expr : null,
  };
}

/** A body's box (world metres) as a rectangle loop in the plane (mm, relative to `origin`): the ghost when the profile cannot be drawn. */
export function boxLoop(plane: PlaneId, origin: Vec3, box: { min: readonly number[]; max: readonly number[] }): Loop[] {
  const axis = { xy: [0, 1], yz: [1, 2], zx: [2, 0] }[plane];
  const [a, b] = axis;
  const u0 = (box.min[a] - origin[a]) * 1000;
  const u1 = (box.max[a] - origin[a]) * 1000;
  const v0 = (box.min[b] - origin[b]) * 1000;
  const v1 = (box.max[b] - origin[b]) * 1000;
  return [[[u0, v0], [u1, v0], [u1, v1], [u0, v1]]];
}
