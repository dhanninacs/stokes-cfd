/**
 * The seven standard views (T-172), as unit vectors from the target to the camera, world Y up as in
 * `camera.ts`. The viewport menu's View entry and the `view.from.*` actions frame the content from one
 * of these (`CameraRig.fit(dir)`). Pure: no three.js, so the tests run in Node.
 */

export type ViewDirection = "front" | "back" | "left" | "right" | "top" | "bottom" | "iso";

/** The directions in menu order, with the words the View list shows. */
export const VIEW_DIRECTIONS: readonly { id: ViewDirection; label: string }[] = [
  { id: "front", label: "Front" },
  { id: "back", label: "Back" },
  { id: "left", label: "Left" },
  { id: "right", label: "Right" },
  { id: "top", label: "Top" },
  { id: "bottom", label: "Bottom" },
  { id: "iso", label: "Isometric" },
];

const CORNER = 1 / Math.sqrt(3);

/** The unit vector from the target to the camera: the camera sits on the front side for "front". */
export function directionVector(d: ViewDirection): [number, number, number] {
  switch (d) {
    case "front":
      return [0, 0, 1];
    case "back":
      return [0, 0, -1];
    case "right":
      return [1, 0, 0];
    case "left":
      return [-1, 0, 0];
    case "top":
      return [0, 1, 0];
    case "bottom":
      return [0, -1, 0];
    case "iso":
      return [CORNER, CORNER, CORNER];
  }
}

/** The box of the fit's targets: just the corners, so the rule needs no three.js. */
export interface UpBox {
  min: readonly [number, number, number];
  max: readonly [number, number, number];
}

const WORLD_UP: [number, number, number] = [0, 1, 0];

/** The axis (0 x, 1 y, 2 z) a unit direction lies along to within rounding, or null for an oblique one. */
function axisAlong(from: readonly [number, number, number]): 0 | 1 | 2 | null {
  const length = Math.hypot(from[0], from[1], from[2]);
  if (length < 1e-9) return null;
  for (const a of [0, 1, 2] as const) if (Math.abs(from[a]) / length > 0.9999) return a;
  return null;
}

/**
 * The camera's up vector for a fit looking from `from` at content whose box is `box` (R4e): **a face-on view puts the
 * longer of the two in-plane axes left to right**, so a lengthwise slice of a pipe along z lies across the screen
 * instead of standing on end. The camera is world-Y up ([0,1,0]), which puts X (from +/-Z) or Z (from +/-X) across
 * the screen, and from +/-Y picks X; when the other in-plane axis is the longer one the up vector is turned onto
 * the across axis (sign chosen so the longer axis runs along its + direction, left to right). A tie, an oblique
 * `from` and a missing box keep world Y (the rule before). Pure.
 */
export function upFor(from: readonly [number, number, number], box: UpBox | null): [number, number, number] {
  const a = axisAlong(from);
  if (a === null || box === null) return [...WORLD_UP];
  // The axis that runs across the screen with world Y up, and the one that stands on it.
  const across: 0 | 1 | 2 = a === 0 ? 2 : 0;
  const stands: 0 | 1 | 2 = a === 1 ? 2 : 1;
  const extent = (axis: 0 | 1 | 2): number => Math.abs(box.max[axis] - box.min[axis]);
  const longer = extent(stands), shorter = extent(across);
  if (!(longer > shorter * (1 + 1e-6))) return [...WORLD_UP];
  // The across axis becomes up; screen right is forward x up, and must point to +stands.
  const forward = [-from[0], -from[1], -from[2]];
  const up: [number, number, number] = [0, 0, 0];
  up[across] = 1;
  const right = [
    forward[1] * up[2] - forward[2] * up[1],
    forward[2] * up[0] - forward[0] * up[2],
    forward[0] * up[1] - forward[1] * up[0],
  ];
  if (right[stands] < 0) up[across] = -1;
  return [up[0] + 0, up[1] + 0, up[2] + 0];
}
