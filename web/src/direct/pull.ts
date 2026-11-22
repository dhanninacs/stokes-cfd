/**
 * The pull's rules (ADR-0039): the depth a new pull starts at, the depth a drag of the handle
 * means, and how a depth is written to the operation. Millimetres throughout. Pure: the tests run
 * in Node.
 */
import { loopsBox, type Loop } from "./profile";

/** The nearest of 1, 2, 2.5 and 5 times a power of ten to `x` (x > 0). */
export function niceNumber(x: number): number {
  if (!(x > 0) || !Number.isFinite(x)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(x)));
  const f = x / p;
  const step = f < 1.5 ? 1 : f < 2.25 ? 2 : f < 3.5 ? 2.5 : f < 7.5 ? 5 : 10;
  return Number((step * p).toPrecision(6));
}

/**
 * The depth a sketch is pulled to on Finish: half the profile's larger side, rounded to a nice
 * number (a 40 × 20 rectangle pulls 20 mm, a 10 mm circle 5 mm). Discovery's pull starts from the
 * face and the user drags; a body that already looks like a body is the better start for a CFD
 * domain, and the handle is right there to change it. 10 mm when there is no profile.
 */
export function defaultDepth(loops: readonly Loop[] | null): number {
  const box = loops ? loopsBox(loops) : null;
  if (box === null) return 10;
  return niceNumber(Math.max(box[2] - box[0], box[3] - box[1]) / 2);
}

/** The snapping step of a drag: the nice length that is at least four pixels on screen. */
export function dragStep(pxPerMm: number): number {
  if (!(pxPerMm > 0)) return 1;
  const raw = 4 / pxPerMm;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const f of [1, 2, 5, 10]) if (f * p >= raw) return Number((f * p).toPrecision(6));
  return 10 * p;
}

/**
 * The depth after dragging the handle by `delta` pixels, from `start` mm.
 *
 * `axis` is where one millimetre along the pull direction moves on screen (pixels); the drag counts
 * only along it, so a sideways wobble does nothing. A symmetric pull grows both ways from the
 * plane, so its end face moves half the depth: `factor` 2. The result snaps to `dragStep` and is
 * at least one step: a pull cannot go to nothing or through the plane.
 */
export function depthFromDrag(start: number, axis: readonly [number, number], delta: readonly [number, number], factor = 1): number {
  const len2 = axis[0] * axis[0] + axis[1] * axis[1];
  if (len2 < 1e-6) return start;
  const mm = (delta[0] * axis[0] + delta[1] * axis[1]) / len2;
  const step = dragStep(Math.sqrt(len2) / factor);
  const raw = start + factor * mm;
  return Math.max(step, Math.round(raw / step) * step);
}

/** A depth as the parameter text the server stores: `20 [mm]`. */
export function depthText(mm: number): string {
  return `${Number(mm.toFixed(6))} [mm]`;
}

/** A depth for the field at the handle: two decimals at most. */
export function depthLabel(mm: number): string {
  return `${Number(mm.toFixed(2))}`;
}

/**
 * What a typed depth means: a bare number is millimetres (ADR-0036 addendum, one length unit in the
 * modeller), anything else goes to the server as it is (`25 [mm]`, `2 * W`); empty or not positive
 * is null.
 */
export function typedDepth(text: string): string | null {
  const t = text.trim();
  if (t === "") return null;
  if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(t)) {
    const n = Number(t);
    return n > 0 ? depthText(n) : null;
  }
  return t;
}
