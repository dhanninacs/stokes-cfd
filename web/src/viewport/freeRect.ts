/**
 * Where the model may be drawn: the window minus whatever the docks cover.
 *
 * **The owner's third request** (claude-win2, 1930): *"the 3D canvas is full-window behind the cards,
 * so the model centres on the whole window and hides under the cards."* The canvas stays full-window -
 * that is what lets a panel float over it without a separate render target - but the CAMERA is told
 * that the middle of its image is the middle of this rectangle, through `setViewOffset`.
 *
 * **A pure function of the four numbers, deliberately.** The alternative - measuring the DOM inside
 * the render loop - is what makes this class of bug hard to reproduce: the result depends on when it
 * ran. This takes the geometry as arguments, so a test can name the five arrangements the owner asked
 * for and compare them.
 */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** What the docks cover, in window pixels, with the top and bottom furniture. */
export interface Obstruction {
  /** Width of the left dock in pixels, 0 when it is collapsed or absent. */
  left: number;
  /** Width of the right dock, 0 when it is collapsed or absent. */
  right: number;
  /** Height of the bar above the viewport (the stage bar), in pixels. */
  top: number;
  /** Height of whatever sits below the viewport: the toolbar, the drawer. */
  bottom: number;
  /** The window, in CSS pixels. */
  window: { width: number; height: number };
}

/**
 * The unobstructed rectangle.
 *
 * **Never smaller than `MIN_SIDE`**, and that floor is the one judgement here: a window narrow enough
 * for the docks to meet (or a mistaken measurement) would otherwise give a zero-width rectangle, and
 * `setViewOffset` with a zero width produces a camera that renders nothing at all - a blank viewport
 * rather than a wrong one. A small rectangle is a bad framing; an empty one is a black screen.
 */
export const MIN_SIDE = 32;

export function freeRect(o: Obstruction): Rect {
  const width = Math.max(MIN_SIDE, o.window.width - o.left - o.right);
  const height = Math.max(MIN_SIDE, o.window.height - o.top - o.bottom);
  return { x: o.left, y: o.top, width, height };
}
