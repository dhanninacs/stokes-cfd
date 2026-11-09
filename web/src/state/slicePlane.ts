/**
 * The planes the results are cut with (slices and the section's cap), as pure functions.
 *
 * A plane is an `origin` and a `normal`, in metres in the world frame, exactly the contract of the cut routes
 * (`GET /api/results/slice/{run}?plane=ox,oy,oz,nx,ny,nz`). The server rounds each of the six numbers to six
 * significant digits and hashes the text as the cache key, so the client builds the SAME text for the same
 * plane: the normal is made a unit vector, the origin is moved to the point of the plane nearest the world
 * origin (any point of the plane names it; this one is the same for all of them), and every number is
 * rounded to six significant digits. Two requests for one plane then share one computation on the server and
 * one entry in the client's caches.
 *
 * Import rule: no React, no store, no DOM. Types only from `sectionState`, so this loads in Node.
 */

import { axisOfNormal, type SectionAxis, type SectionPlane, type Vec3 } from "./sectionState";

export type { Vec3 } from "./sectionState";

/** A plane: a point on it and its normal. */
export interface Plane {
  origin: Vec3;
  normal: Vec3;
}

const AXES: readonly SectionAxis[] = ["x", "y", "z"];

/** A number to six significant digits, with `-0` as `0`; `NaN` for anything not finite. */
export function round6(value: number): number {
  if (!Number.isFinite(value)) return Number.NaN;
  const rounded = Number(value.toPrecision(6));
  return rounded === 0 ? 0 : rounded;
}

function isVec3(value: readonly number[]): boolean {
  return value.length === 3 && value.every((v) => Number.isFinite(v));
}

/**
 * The plane the server would key a cache entry on: unit normal, origin nearest the world origin, six
 * significant digits. Null for a plane the server refuses (a number that is not finite, or no normal).
 */
export function canonicalPlane(plane: Plane): Plane | null {
  if (!isVec3(plane.origin) || !isVec3(plane.normal)) return null;
  const length = Math.hypot(plane.normal[0], plane.normal[1], plane.normal[2]);
  if (!(length > 0)) return null;
  const n: [number, number, number] = [plane.normal[0] / length, plane.normal[1] / length, plane.normal[2] / length];
  const d = n[0] * plane.origin[0] + n[1] * plane.origin[1] + n[2] * plane.origin[2];
  const origin: Vec3 = [round6(n[0] * d), round6(n[1] * d), round6(n[2] * d)];
  const normal: Vec3 = [round6(n[0]), round6(n[1]), round6(n[2])];
  if (!isVec3(origin) || !isVec3(normal)) return null;
  return { origin, normal };
}

/** A number as it goes into the query: no `+` (it would read as a space in a query string). */
function numberText(value: number): string {
  return String(value).replace("e+", "e");
}

/** `ox,oy,oz,nx,ny,nz`, the value of the `plane` query parameter; null when the plane is not one. */
export function planeQuery(plane: Plane): string | null {
  const canon = canonicalPlane(plane);
  return canon === null ? null : [...canon.origin, ...canon.normal].map(numberText).join(",");
}

/** What a plane is called in a cache: its query text. Null for a plane the server would refuse. */
export function planeKey(plane: Plane): string | null {
  return planeQuery(plane);
}

/** True when two planes are the same cut (the same canonical numbers). */
export function samePlane(a: Plane, b: Plane): boolean {
  const ka = planeKey(a);
  return ka !== null && ka === planeKey(b);
}

/** The plane back from its query text, or null for text that is not six finite numbers with a normal. */
export function parsePlaneQuery(text: string): Plane | null {
  const parts = text.split(",");
  if (parts.length !== 6 || parts.some((p) => p.trim() === "")) return null;
  const numbers = parts.map((p) => Number(p));
  if (!numbers.every((n) => Number.isFinite(n))) return null;
  const plane: Plane = { origin: [numbers[0], numbers[1], numbers[2]], normal: [numbers[3], numbers[4], numbers[5]] };
  return canonicalPlane(plane);
}

/** The plane normal to a world axis at a position along it (metres). */
export function axisPlane(axis: SectionAxis, position: number): Plane {
  const i = AXES.indexOf(axis);
  const origin: [number, number, number] = [0, 0, 0];
  const normal: [number, number, number] = [0, 0, 0];
  origin[i] = position;
  normal[i] = 1;
  return { origin, normal };
}

/**
 * The plane of the Section view: its axis at its offset. Which half is kept does not change the cut, so the
 * normal is always the axis's positive direction.
 */
export function planeOfSection(section: SectionPlane): Plane {
  return axisPlane(section.axis, section.offset);
}

/** The axis and position of a plane that is normal to a world axis, or null for an oblique one. */
export function axisOfPlane(plane: Plane): { axis: SectionAxis; position: number } | null {
  const found = axisOfNormal(plane.normal);
  if (found === null) return null;
  return { axis: found.axis, position: plane.origin[AXES.indexOf(found.axis)] };
}

/** The distance of a point from the plane, positive on the side the normal points to. */
export function signedDistance(plane: Plane, point: Vec3): number {
  const length = Math.hypot(plane.normal[0], plane.normal[1], plane.normal[2]);
  if (!(length > 0)) return Number.NaN;
  return ((point[0] - plane.origin[0]) * plane.normal[0] + (point[1] - plane.origin[1]) * plane.normal[1] + (point[2] - plane.origin[2]) * plane.normal[2]) / length;
}

/** The box the plane does not touch: every corner on one side of it, beyond `tolerance` metres. */
export function planeMissesBox(plane: Plane, box: { min: Vec3; max: Vec3 }, tolerance = 0): boolean {
  let below = 0;
  let above = 0;
  for (let i = 0; i < 8; i++) {
    const corner: Vec3 = [i & 1 ? box.max[0] : box.min[0], i & 2 ? box.max[1] : box.min[1], i & 4 ? box.max[2] : box.min[2]];
    const d = signedDistance(plane, corner);
    if (!Number.isFinite(d)) return true;
    if (d > tolerance) above++;
    else if (d < -tolerance) below++;
  }
  return above === 8 || below === 8;
}

/** The middle of the box along an axis, for a slice that starts in the middle of the model. */
export function middleAlong(axis: SectionAxis, box: { min: Vec3; max: Vec3 }): number {
  const i = AXES.indexOf(axis);
  return (box.min[i] + box.max[i]) / 2;
}

/**
 * Where a section plane starts when a user picks an axis: the MIDDLE of that axis's extent.
 *
 * **Why this is a function with a test** (G-301 step 10): the section popover computed it from the extent
 * of the PREVIOUSLY chosen axis, and that is null before any section exists - so `middle` came out 0 and
 * the plane was written at the world origin. A slice added "from the section plane" then asked the server
 * for `plane=0,0,0,0,0,1`, which lies outside the model, and got a 404 with nothing on screen.
 *
 * The extent is snapped OUTWARD to whole steps first, so the middle is the middle of what the slider can
 * actually reach rather than of the raw geometry.
 */
export function startingPosition(extent: readonly [number, number] | null, stepMm: number): number {
  if (extent === null) return 0;
  const low = Math.floor((extent[0] * 1000) / stepMm) * stepMm;
  const high = Math.ceil((extent[1] * 1000) / stepMm) * stepMm;
  return (low + high) / 2;
}
