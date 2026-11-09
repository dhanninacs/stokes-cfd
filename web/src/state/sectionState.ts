/**
 * The section plane's rules, as pure functions (T-065).
 *
 * **A module of its own, and not part of `store.ts`.** The store imports `treeSync.ts`, which touches
 * `window` at module scope, so Vitest — which runs in `node` — cannot import the store at all: a test
 * that did failed with `ReferenceError: window is not defined` before collecting a single case. The
 * rule was already in the decision log from the T-046 work ("pure functions belong in modules with no
 * browser imports"); keeping the section's arithmetic here is that rule applied, and it is why these
 * tests run at all.
 *
 * The store's `setSection`/`setSectionPlane` are thin wrappers over these, so there is one
 * implementation of the sign and the guard rather than two.
 */

/** The axis a section plane's normal points along, in the world frame. */
export type SectionAxis = "x" | "y" | "z";

/**
 * A section plane: a world axis and a signed offset along it, in metres (T-065).
 *
 * Deliberately axis-aligned. A free plane needs a second control for its rotation, and the three axis
 * cuts are what a CFD user takes to see inside a pipe, a plate or a duct; a normal that is not an axis
 * is a feature to add when someone asks for it rather than one to guess at now.
 */
export interface SectionPlane {
  axis: SectionAxis;
  /** Signed distance along `axis`, metres. The side the axis points to is the side kept. */
  offset: number;
  /**
   * Keep the OTHER half of the model.
   *
   * The plane is a half-space: without this the side the axis points to is kept, and with it the
   * side behind is. It is one flag rather than a signed offset on purpose — a user who has found the
   * cut they want and then flips it expects the SAME cut, not a cut on the far side of the model, and
   * negating only the offset would keep one cut but jump the plane to the mirrored position.
   */
  invert: boolean;
}

/** The plane a section opens at when it has none yet. */
export const DEFAULT_SECTION: SectionPlane = { axis: "z", offset: 0, invert: false };

/**
 * The plane after turning the section on or off; `null` means off.
 *
 * Turning it on **keeps the plane it had**, so the toggle is "show the section again" rather than
 * "start over at the origin" — the offset a user found is the state they are toggling. Turning it off
 * is `null` rather than a hidden boolean: the viewport reads `null` to mean "no clipping", and one
 * source of truth cannot disagree with itself.
 */
export function toggleSection(current: SectionPlane | null, on: boolean): SectionPlane | null {
  if (!on) return null;
  return current ?? DEFAULT_SECTION;
}

/**
 * The plane after a move, or `null` when the section is off.
 *
 * A **non-finite** offset is refused and the origin is used instead. `NaN` in a clip plane's constant
 * makes the shader discard every fragment, so the viewport goes blank with nothing on screen to
 * explain it; a typed field can produce `NaN` ("-" or "" while being edited), and the popover's clamp
 * does not catch it because `Math.min(NaN, x)` is `NaN`. `Infinity` is refused for the same reason.
 */
export function moveSection(
  current: SectionPlane | null,
  axis: SectionAxis,
  offset: number,
): SectionPlane | null {
  if (current === null) return null;
  // `invert` is CARRIED, not reset: moving the cut keeps the half the user chose to keep. Losing it
  // here would flip the model back on every slider drag, which reads as the slider being broken.
  return { ...current, axis, offset: Number.isFinite(offset) ? offset : 0 };
}

/**
 * The plane after flipping which half is kept, or `null` when the section is off.
 *
 * A no-op while the section is off, as `moveSection` is: the control that flips lives in the popover,
 * which cannot be reached with the section off, but a stale event must not switch it on either.
 */
export function invertSection(current: SectionPlane | null, invert: boolean): SectionPlane | null {
  if (current === null) return null;
  return { ...current, invert };
}

/**
 * The section after the project changes: none.
 *
 * **A section belongs to the document it was made in** (Claude's defect 4). Opening or closing a
 * project kept the plane, so the new project opened with a cut carried over from the old one —
 * chosen against the old model's bounds, so it cut nothing visible while the popover still showed it
 * as on and the handle sat wherever the old model had put it.
 *
 * A function rather than a bare `null` at the call site so the rule is stated once and can be tested:
 * the store's own reset lives beside `window` code and cannot be imported by Vitest.
 */
export function sectionOnProjectChange(): SectionPlane | null {
  return null;
}

// ---------------------------------------------------------------------------------------------- the slice cut
/**
 * The mid-plane slice is an interior plane, and the closed patches around it hide it. While it is on, the
 * Section view clips the patches at the slice plane so the cut face IS the slice. These are the rules, pure:
 * the plane the slice lies in, which half is kept, and what is saved and restored of the user's own section.
 */

/** A point or direction in the world frame. */
export type Vec3 = readonly [number, number, number];

const AXES: readonly SectionAxis[] = ["x", "y", "z"];

function isVec3(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3 && value.every((v) => typeof v === "number" && Number.isFinite(v));
}

/**
 * The axis a normal points along, and its sign, or null when the normal is not along one world axis (the
 * Section view cuts along axes only) or is not a normal at all.
 */
export function axisOfNormal(normal: Vec3): { axis: SectionAxis; sign: 1 | -1 } | null {
  const mag = Math.hypot(normal[0], normal[1], normal[2]);
  if (!(mag > 0)) return null;
  for (let i = 0; i < 3; i++) {
    const c = normal[i] / mag;
    if (Math.abs(Math.abs(c) - 1) < 1e-6) return { axis: AXES[i], sign: c > 0 ? 1 : -1 };
  }
  return null;
}

/**
 * The plane the slice lies in, from the solver settings' `slice_origin` and `slice_normal`, as an axis and an
 * offset. When the settings carry no usable pair, the slice's own bounds say it: a surface flat along one axis
 * (an extent below 1e-6 of the widest) lies in that plane. Null for a slice that is neither.
 */
export function slicePlaneOf(
  params: Record<string, unknown> | null | undefined,
  bounds: { min: Vec3; max: Vec3 } | null,
): { axis: SectionAxis; offset: number } | null {
  const origin = params?.slice_origin;
  const normal = params?.slice_normal;
  if (isVec3(origin) && isVec3(normal)) {
    const found = axisOfNormal(normal);
    if (found !== null) return { axis: found.axis, offset: origin[AXES.indexOf(found.axis)] };
  }
  if (bounds === null) return null;
  const extent = [0, 1, 2].map((i) => bounds.max[i] - bounds.min[i]);
  const widest = Math.max(...extent);
  if (!(widest > 0)) return null;
  const flat = [0, 1, 2].filter((i) => extent[i] <= widest * 1e-6);
  if (flat.length !== 1) return null;
  return { axis: AXES[flat[0]], offset: (bounds.min[flat[0]] + bounds.max[flat[0]]) / 2 };
}

/**
 * The section that exposes a slice: the plane of the slice, with the half between the camera and the slice
 * taken away. `cameraAlong` is the camera's coordinate on the slice's axis. The plane sits `lift` past the
 * slice, on the side that is kept, so the slice itself is never clipped by the plane it lies in (a fragment
 * exactly on a clip plane is a coin toss) and the walls show a thin rim at the cut.
 */
export function sliceSection(plane: { axis: SectionAxis; offset: number }, cameraAlong: number, lift: number): SectionPlane {
  const gap = Math.abs(Number.isFinite(lift) ? lift : 0);
  // The camera is above the slice: the upper half goes (keep -axis, `invert`) and the plane moves up by `gap`.
  const above = cameraAlong >= plane.offset;
  return above ? { axis: plane.axis, offset: plane.offset + gap, invert: true } : { axis: plane.axis, offset: plane.offset - gap, invert: false };
}

/** What the slice cut remembers: the section the user had (null: none) and the one it set. */
export interface SliceCut {
  saved: SectionPlane | null;
  applied: SectionPlane;
}

/** True when two sections are the same cut. */
export function sameSection(a: SectionPlane | null, b: SectionPlane | null): boolean {
  if (a === null || b === null) return a === b;
  return a.axis === b.axis && a.invert === b.invert && a.offset === b.offset;
}

/**
 * The slice is on and can be cut: the section to set, and the cut to remember. With a cut already held the
 * section follows it (the plane may have been re-derived), and what was saved stays the user's own.
 */
export function engageSliceCut(held: SliceCut | null, current: SectionPlane | null, wanted: SectionPlane, forget = false): { section: SectionPlane; cut: SliceCut } {
  // `forget`: the Looks asked for the section the cut would remember to be dropped (`forgetSaved`), so none is kept.
  const saved = forget ? null : held === null ? current : held.saved;
  return { section: wanted, cut: { saved, applied: wanted } };
}

/**
 * The cut without the section it would give back when the slice goes. The Looks keep a section only for the Clip
 * look: a section the user had before the slice came on is not one the Looks asked for, and handing it back on
 * Surface put a z = 0 cut on the walls of a fresh Results (R4b).
 */
export function forgetSaved(held: SliceCut | null): SliceCut | null {
  return held === null || held.saved === null ? held : { ...held, saved: null };
}

/**
 * The slice is off, or the results are gone: the section is what it was before the slice cut. If the user moved
 * or removed the section while the slice was on (`current` is no longer what the cut set), their change stands
 * and nothing is restored. `section` is `undefined` when the store's section is to be left alone.
 */
export function releaseSliceCut(held: SliceCut | null, current: SectionPlane | null): { section: SectionPlane | null | undefined; cut: null } {
  if (held === null) return { section: undefined, cut: null };
  return { section: sameSection(current, held.applied) ? held.saved : undefined, cut: null };
}

/**
 * The section plane after "turn it on AT this plane", in one step.
 *
 * **Two writes are not the same thing** (G-301's D6, three failed attempts). `toggleSection(null, true)`
 * returns `DEFAULT_SECTION` - axis `z` at offset **0, the world origin** - and the slice layer fetches
 * immediately, so the caller's `moveSection` on the next tick arrives too late: the origin plane has
 * already been requested and the server answers **404** for a cut outside the model. Measured: two failed
 * requests every time the Section card was opened from a "start a section" path.
 *
 * A user who opens the Section control means "cut here", and "here" is the middle of the model, not the
 * origin - so the plane is computed before the store is touched and written once.
 */
export function sectionAt(axis: SectionAxis, offset: number): SectionPlane {
  return { axis, offset, invert: false };
}

/**
 * The run's mid-plane slice is on screen inside a whole body: it is shown, its cut engaged once (`cutHeld`), and
 * there is no section now, because the user removed the one the cut set. Then nothing exposes it and the walls
 * fade instead (`panels/sliceModel.ts` `wallsFaded`). Before the cut engages (`cutHeld` false) it is not buried,
 * so the first show does not flash the walls see-through for the frame before the section arrives.
 */
export function midSliceBuried(sliceShown: boolean, cutHeld: boolean, section: SectionPlane | null): boolean {
  return sliceShown && cutHeld && section === null;
}
