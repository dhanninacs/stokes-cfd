/**
 * Where a popover opened at a point goes. Pure arithmetic, no DOM, so a test can check it:
 * `Popover` reads the sizes and calls this.
 */

export interface Point {
  x: number;
  y: number;
}
export interface Size {
  w: number;
  h: number;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * The top-left corner for a surface of `size` opened at `at`, in a `viewport` of the same units.
 * The corner sits at the point; a surface that would run past the right or bottom edge (less
 * `pad`) flips to the other side of the point, and the result stays `pad` inside every edge.
 */
export function placeAt(at: Point, size: Size, viewport: Size, pad = 8): Point {
  let x = at.x;
  let y = at.y;
  if (x + size.w > viewport.w - pad) x = at.x - size.w;
  if (y + size.h > viewport.h - pad) y = at.y - size.h;
  return {
    x: clamp(x, pad, Math.max(pad, viewport.w - size.w - pad)),
    y: clamp(y, pad, Math.max(pad, viewport.h - size.h - pad)),
  };
}
