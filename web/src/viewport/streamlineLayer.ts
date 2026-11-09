/**
 * Streamlines drawn in the viewport: the direction a contour cannot show.
 *
 * **The geometry is a pure function** (`streamlineSegments`), so what gets drawn can be tested without a
 * renderer - the same split `featureEdges`/`edgeLines` uses.
 *
 * **A line is drawn as SEGMENTS, not a polyline**, because the colour changes along it: `LineSegments2`
 * takes one colour per segment, so a streamline's speed can be drawn as the colour of each short piece.
 * That needs the points broken into consecutive pairs, which is what this module does.
 */
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { DEFAULT_COLORMAP, colorAtIn, fractionIn, type Colormap, type ColormapId } from "../state/colormap";

/** One streamline as the server returns it: points in metres and the speed at each. */
export interface StreamlineLine {
  points: readonly (readonly [number, number, number])[];
  speeds: readonly number[];
}

/** Segment end points (six floats per segment) and the colour of each segment (three floats). */
export interface StreamlineSegments {
  positions: Float32Array;
  colors: Float32Array;
}

/**
 * The drawn form of `lines`: consecutive point pairs, each coloured by the speed ALONG it.
 *
 * **A segment's colour is the MEAN of its two ends**, which is the speed over that piece - taking one
 * end would bias every segment towards where it started, and a line drawn with alternating bias looks
 * like a measurement that wobbles.
 *
 * **`speedRange` is the caller's, not the data's**: every streamline in one picture must use the same
 * map, or two lines drawn at the same speed would come out different colours. The caller computes it
 * once across all the lines it is drawing.
 *
 * **The colour map is the view's** (`map`, `reverse`: `results.view`, as the slices, the iso-surface and the legend
 * read it), so lines, cuts and legend cannot be three different maps; viridis when none is given.
 */
export function streamlineSegments(
  lines: readonly StreamlineLine[],
  speedRange: { min: number; max: number },
  map: Colormap | ColormapId = DEFAULT_COLORMAP,
  reverse = false,
): StreamlineSegments {
  const positions: number[] = [];
  const colors: number[] = [];
  for (const line of lines) {
    // A line of one point has no segment, and a length mismatch means the answer is not about these
    // points: both are skipped rather than drawn with a guessed colour.
    if (line.points.length < 2 || line.points.length !== line.speeds.length) continue;
    for (let i = 1; i < line.points.length; i++) {
      const a = line.points[i - 1]!;
      const b = line.points[i]!;
      positions.push(a[0], a[1], a[2], b[0], b[1], b[2]);
      const speed = (line.speeds[i - 1]! + line.speeds[i]!) / 2;
      const [r, g, bl] = colorAtIn(map, fractionIn(map, speed, speedRange.min, speedRange.max), reverse);
      // **The same colour at both ends of the segment**, so the piece is one flat colour rather than a
      // gradient along a few millimetres - which would be a claim the data cannot support.
      colors.push(r / 255, g / 255, bl / 255, r / 255, g / 255, bl / 255);
    }
  }
  return { positions: new Float32Array(positions), colors: new Float32Array(colors) };
}

/**
 * A line object for `lines`, coloured by speed, or null when there is nothing to draw.
 *
 * **`vertexColors` is on**, and the colour array comes from `streamlineSegments` - so one material
 * serves every picture and only the arrays change.
 */
export function streamlineLines(
  lines: readonly StreamlineLine[],
  speedRange: { min: number; max: number },
  width = 2,
  map: Colormap | ColormapId = DEFAULT_COLORMAP,
  reverse = false,
  resolution: { width: number; height: number } = { width: 1, height: 1 },
  /** `width` is in model units (metres), not pixels: a tube. */
  worldUnits = false,
): LineSegments2 | null {
  const { positions, colors } = streamlineSegments(lines, speedRange, map, reverse);
  if (positions.length === 0) return null;
  const geometry = new LineSegmentsGeometry().setPositions(positions);
  geometry.setColors(colors);
  /*
    **Depth-tested, now that the lines run through the VOLUME** (2026-11-04, the owner's "stream lines are
    not volumetric"). The first lines ran ON the mid-plane slice inside an opaque duct, and `depthTest:
    false` was what made them visible at all. Volume lines kept that setting, and it flattened them: a
    line behind another, behind the slice or behind the far wall was painted on top of it, so the bundle
    read as a decal on the screen with no front and no back. With the depth test on, nearer lines hide
    farther ones. The walls are faded while lines show (`wallsFaded`), and a faded wall is drawn in the
    transparent pass after these, so it tints the lines behind it instead of hiding them.
  */
  /*
    **A `LineMaterial`, the only material `LineSegments2` can draw** (2026-11-03, the owner's "stream lines
    doesn't work" and a frozen Windows app). `LineSegmentsGeometry` is INSTANCED: each segment is an instance of
    one small unit quad, and only `LineMaterial`'s shader stretches that quad between the segment's ends. A
    `LineBasicMaterial` cast to the type drew the bare quad, about a metre across at the origin, once per
    segment: measured on pipe3, 44,000 overlapping quads over the whole screen with depth testing off, every
    frame. The Intel GPU could not finish a frame, Windows reset it (D3D11 DEVICE_HUNG, 0x887A0006) and
    the window froze; the lines themselves never showed. `width` is in pixels, so the material needs the
    canvas size (`resolution`), which the scene keeps up to date (`Scene.setStreamlineResolution`).
  */
  const material = new LineMaterial({
    vertexColors: true,
    linewidth: width,
    worldUnits,
    depthTest: true,
    depthWrite: true,
    toneMapped: false,
  });
  material.resolution.set(Math.max(1, resolution.width), Math.max(1, resolution.height));
  const object = new LineSegments2(geometry, material);
  object.name = "streamlines";
  object.renderOrder = 3;
  return object;
}
