/**
 * One glyph per boundary (ADR-0022 §8.1, T-046): where it sits, which way it points, and how it is
 * scaled — the arithmetic, kept apart from the instanced meshes so it can be tested without a
 * renderer.
 *
 * §8.1 is specific about the placement and it is the part that can be wrong invisibly:
 *
 * * The instance sits at the anchor's **centroid**, points along the anchor's **normal**, and — this
 *   is the rule that matters — **points inward for an inlet and outward for an outlet**. The normal
 *   that comes back from `tag.anchor` points *out of* the surface; an inlet's arrow must therefore
 *   be the opposite of it, or every inlet would read as an outlet.
 * * It keeps a **constant 28 px on screen**, so the per-frame work is one scale over tens of
 *   instances, never over faces. "Constant on screen" means the scale depends on the camera's
 *   distance and the viewport height: this module computes it as a world size from a pixel size.
 * * Colour is the **tag's** colour (the kind colour by default, a user's override follows).
 *
 * A kind whose glyph shape is unknown falls back to a marker rather than vanishing, so a kind added
 * to the catalogue server-side still shows *something* at the right place.
 */

import type { ConditionKind } from "../api/types";

/** One anchor exactly as `tag.anchor` returns it (metres, SI). */
export interface TagAnchor {
  /** The tag node's path, which is how a boundary finds its glyph. */
  tag: string;
  centroid: [number, number, number];
  /**
   * Where the glyph is drawn: the centroid when it is on the faces, else the nearest point on them (an
   * L-shaped plane's centroid lies off it). Absent from an older server, which drew at the centroid.
   */
  position?: [number, number, number];
  /** Unit normal of the tag's faces, pointing out of the surface. */
  normal: [number, number, number];
  /** Total area of the faces, square metres. */
  area: number;
  /** Diagonal of the bounding box of the faces' centroids, metres. */
  extent: number;
  faces: number;
  parts: number;
  /** True when a member part has no mesh yet, so the anchor is incomplete. */
  partial: boolean;
}

/** The result of `tag.anchor`. */
export interface AnchorResult {
  anchors: TagAnchor[];
}

/**
 * The glyph shapes §8.1 names, plus `none` for a kind the catalogue draws no glyph for (the `wall`
 * kind carries `glyph: "none"`), and `diamond` as the fallback for a shape this build does not know.
 */
export type GlyphShape = "arrow_in" | "arrow_out" | "plane" | "pair" | "diamond" | "none";

/**
 * Which way a glyph's arrow points, from the boundary's kind. §8.1: *inward for inlets, outward for
 * outlets*. Everything that is neither is a plane or a marker with no direction to flip.
 */
export type GlyphFlow = "in" | "out" | "none";

/**
 * The flow direction of a condition kind, from its id and family.
 *
 * Derived rather than hard-coded per kind so a new kind in the same family inherits the right
 * direction: the `inlet` family flows in, the `outlet` family out. A `wall` or `symmetry` has no
 * direction. The names are checked as a fallback because a kind's id is stable while its family has
 * been renamed once already (`constraint` holds both symmetry and cyclic).
 */
export function glyphFlow(kind: Pick<ConditionKind, "id" | "family">): GlyphFlow {
  if (kind.family === "inlet") return "in";
  if (kind.family === "outlet") return "out";
  const id = kind.id;
  if (id.endsWith("_inlet")) return "in";
  if (id.endsWith("_outlet")) return "out";
  return "none";
}

/**
 * The shape to draw for a kind. The catalogue's own `glyph` field wins when it names one of the
 * shapes; otherwise the flow picks an arrow, and a directionless kind gets a plane. A future shape
 * the catalogue names but this build does not know falls back to `diamond`, which is visible and
 * directionless — never nothing.
 */
export function glyphShape(kind: Pick<ConditionKind, "id" | "family" | "glyph">): GlyphShape {
  const named = kind.glyph;
  // The catalogue spells "no glyph" as the literal `"none"` (the `wall` kind uses it), which is a
  // real answer, not a missing one: such a boundary draws nothing.
  if (named === NO_GLYPH) return "none";
  if (named !== undefined && named !== "" && SHAPES.includes(named as GlyphShape)) {
    return named as GlyphShape;
  }
  // A name this build does not know is deliberately NOT treated as "no name": the catalogue is
  // server-side and may ship a shape first, and silently drawing a plane for it would hide that.
  // `diamond` is visible and directionless, so an unknown shape is obvious rather than absent.
  if (named !== undefined && named !== "") return "diamond";
  const flow = glyphFlow(kind);
  if (flow === "in") return "arrow_in";
  if (flow === "out") return "arrow_out";
  return "plane";
}

const SHAPES: readonly GlyphShape[] = ["arrow_in", "arrow_out", "plane", "pair", "diamond"];

/** The catalogue's spelling for "draw no glyph"; not one of the drawn shapes. */
const NO_GLYPH = "none";

/**
 * One glyph to draw: where, which way, how big, and in what colour.
 *
 * `direction` is the **unit vector the glyph's arrow points along**, already flipped for an inlet.
 * `scale` is a world-space size that renders as `GLYPH_PIXELS` on screen at the given camera
 * distance — the caller multiplies the instance's own geometry by it.
 */
export interface Glyph {
  /** The boundary node path, so a hover or a click can name it. */
  tag: string;
  position: [number, number, number];
  direction: [number, number, number];
  /** World-space size for a constant on-screen size at this camera distance. */
  scale: number;
  shape: GlyphShape;
  /** `#rrggbb`, the tag's colour. */
  color: string;
  /** Faces the anchor covers; 0 means there is nothing to point at. */
  faces: number;
}

/** The on-screen size §8.1 requires the glyph to keep. */
export const GLYPH_PIXELS = 28;

/** A degenerate normal (a closed face, or a tag whose mesh has gone) is not a direction. */
function unit(v: [number, number, number]): [number, number, number] {
  const n = Math.hypot(v[0], v[1], v[2]);
  if (!(n > 0)) return [0, 0, 0];
  return [v[0] / n, v[1] / n, v[2] / n];
}

/**
 * The world size a `GLYPH_PIXELS`-tall glyph needs at `distance` metres from the camera.
 *
 * The frustum's world height at a distance is `2 * distance * tan(fov / 2)`; dividing by the
 * viewport's height in pixels gives metres per pixel, and multiplying by the glyph's pixel size
 * gives the world scale. `fov` is in **degrees** (three.js's convention) and `height` in device
 * pixels, so the result is right on a HiDPI canvas as well as a normal one.
 */
export function glyphScale(distance: number, fovDegrees: number, heightPixels: number): number {
  if (!(distance > 0) || !(fovDegrees > 0) || !(heightPixels > 0)) return 0;
  const worldHeight = 2 * distance * Math.tan((fovDegrees * Math.PI) / 360);
  return (worldHeight / heightPixels) * GLYPH_PIXELS;
}

/**
 * The smallest on-screen fraction of `GLYPH_PIXELS` a glyph may shrink to.
 *
 * **T-046-fix-4.** `glyphScale` sizes a glyph for its distance from the camera, which keeps it 28 px
 * when the surface it sits on faces the camera. A surface seen **edge-on** is foreshortened instead:
 * the projection of a face whose normal is perpendicular to the view direction collapses towards
 * zero, so the glyph shrinks to a sliver and a boundary the user is looking straight at becomes
 * unpickable. A floor keeps it findable without pretending the surface is not edge-on.
 *
 * 0.45 is chosen as "smaller, but still a target": a 28 px glyph bottoms out at about 13 px, which is
 * above the 8 px the interaction harness treats as too small to grab and below the 20 px its scans
 * require. It is a **floor on the world scale**, so the screen size a user sees also depends on
 * whether the glyph is drawn as a billboard or turned to the face — the caller decides that.
 */
export const GLYPH_MIN_FRACTION = 0.45;

/**
 * How much a glyph shrinks when its surface is seen at `cosAngle` to the camera.
 *
 * `cosAngle` is the cosine between the surface's outward normal and the direction from the surface to
 * the camera: 1 when the surface faces the camera square on, 0 when it is exactly edge-on. The result
 * is clamped to `[GLYPH_MIN_FRACTION, 1]`.
 *
 * **The pure rule, and the testable half of T-046-fix-4.** The world length of a segment lying in the
 * surface, projected, is proportional to this cosine; the glyph's own extent is not, because it is
 * drawn as a marker rather than as something lying in the surface. So the honest model is a
 * *floor*, not a replacement for the distance scale: full size square-on, `GLYPH_MIN_FRACTION` at
 * and beyond edge-on, and linear in between. Clamping to a fraction rather than to a pixel figure
 * keeps it independent of the viewport and the DPI, which the caller already accounts for by scaling
 * every glyph from the same base.
 *
 * A cosine outside `[-1, 1]` cannot happen from normalised vectors, but a **non-finite** one can
 * (a degenerate normal, a camera at the anchor's own position): those take the floor rather than
 * propagating `NaN` into an instance matrix, which would hide the glyph entirely.
 */
export function glyphShrink(cosAngle: number): number {
  if (!Number.isFinite(cosAngle)) return GLYPH_MIN_FRACTION;
  const facing = Math.abs(cosAngle); // a face seen from behind is as foreshortened as one edge-on
  const clamped = Math.min(1, Math.max(0, facing));
  return GLYPH_MIN_FRACTION + (1 - GLYPH_MIN_FRACTION) * clamped;
}

/**
 * The glyphs to draw, from the anchors the server returned and the boundaries that reference them.
 *
 * `kindOf` maps a tag path to the kind of the boundary using it, so the flow and the shape can be
 * decided; a tag no boundary uses gets `null` and draws as a directionless plane. `viewOf`
 * answers, for an anchor and its outward normal, the camera distance along the view axis (which is
 * what makes the size constant on screen) and the cosine between that normal and the direction to
 * the camera (which is what keeps an edge-on surface's glyph findable).
 *
 * An anchor with no faces (the tag is empty, or its mesh has not arrived) is **dropped**: a glyph
 * floating at the origin for an unmeshed tag would be a lie about where the boundary is. A partial
 * anchor (a member without a mesh) is kept, because the faces that are known still say where it is.
 */
export function glyphsFor(
  anchors: readonly TagAnchor[],
  kindOf: (tag: string) => Pick<ConditionKind, "id" | "family" | "glyph" | "color"> | null,
  viewOf: (position: [number, number, number], normal: [number, number, number]) => {
    distance: number;
    cosAngle: number;
  },
  fovDegrees: number,
  heightPixels: number,
): Glyph[] {
  const out: Glyph[] = [];
  for (const anchor of anchors) {
    if (anchor.faces <= 0) continue;
    const kind = kindOf(anchor.tag);
    const shape: GlyphShape = kind === null ? "plane" : glyphShape(kind);
    // A kind the catalogue gives no glyph (a wall) draws none: §8.1 is "one glyph per boundary" for
    // the boundaries that have one, not a marker on every kind.
    if (shape === "none") continue;
    const flow = kind === null ? "none" : glyphFlow(kind);
    const outward = unit(anchor.normal);
    // §8.1: inward for an inlet, outward for an outlet. The anchor's normal points out of the
    // surface, so an inlet takes its opposite — this is the one place that distinction is made.
    const direction: [number, number, number] =
      flow === "in" ? [-outward[0], -outward[1], -outward[2]] : outward;
    const at = anchor.position ?? anchor.centroid;
    const view = viewOf(at, outward);
    const distance = view.distance;
    // **Distance sets the base size; the view angle sets a floor under it** (T-046-fix-4). A surface
    // seen edge-on would otherwise project its glyph to a sliver, narrow enough that the user cannot
    // find or click the boundary they are looking straight at.
    const scale = glyphScale(distance, fovDegrees, heightPixels) * glyphShrink(view.cosAngle);
    if (!(scale > 0)) continue; // behind the camera, or a viewport with no height
    out.push({
      tag: anchor.tag,
      position: at,
      direction,
      scale,
      shape,
      color: kind?.color ?? "#7f8c99",
      faces: anchor.faces,
    });
  }
  return out;
}
