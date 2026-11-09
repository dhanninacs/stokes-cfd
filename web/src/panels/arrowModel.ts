/**
 * Vector arrows on a slice or through the volume: the direction a colour map cannot show.
 *
 * **What a CFD user reads on a cross-section** (claude-win2's item 2): an arrow per sample, coloured by
 * speed, so the secondary flow and the recirculation are visible where a scalar field hides them. It
 * needs **no new data** - the slice already carries the full three-component velocity, and the probe
 * work proved the route.
 *
 * **Decimated, not one per point.** The audit's slice holds 2761 points; 2761 arrows over a 20 mm strip
 * is a black smear. The rule here picks a readable grid and the density slider moves it.
 */
export interface ArrowSeed {
  point: readonly [number, number, number];
  /** The velocity there, in m/s. */
  vector: readonly [number, number, number];
  /** The speed, which is what the colour follows. */
  speed: number;
}

/** Where the arrows are drawn: on the slice's samples, or on an even lattice through the volume (`run.vectors`). */
export type ArrowPlace = "slice" | "volume";

/** The arrows' view state (the store's `arrows`). No `where` is a slice, as before the volume existed. */
export interface ArrowView {
  shown: boolean;
  density: number;
  scale: number;
  where?: ArrowPlace;
}

/** The arrows sampled through the volume, as `run.vectors` answered (SI), and where the ask stands. */
export interface VectorCloud {
  phase: "idle" | "computing" | "ready" | "failed";
  points: readonly (readonly [number, number, number])[];
  vectors: readonly (readonly [number, number, number])[];
  /** The lattice step in metres: the length a full-speed arrow is drawn at scale 1. */
  spacing: number;
  message: string;
}

export const NO_VECTOR_CLOUD: VectorCloud = { phase: "idle", points: [], vectors: [], spacing: 0, message: "" };

/** How many volume arrows a density asks for: the slice's ladder scaled to a volume (192 on a slice is 1,600 in 3-D). */
export function volumeCountOf(density: number): number {
  return Math.max(50, Math.min(2000, Math.round((density * 25) / 3)));
}

/**
 * A full-speed volume arrow's length, in metres: under the lattice step at scale 1 (the arrow mesh runs 1.35 of its
 * length, tail to tip), so neighbours do not run into each other; the scale slider stretches it.
 */
export function volumeArrowLength(spacing: number, scale: number): number {
  return Math.max(0, spacing) * 0.7 * Math.max(0, scale);
}

/** Whether the arrows drawn now are the volume's. */
export function volumeArrowsShown(arrows: ArrowView | undefined): boolean {
  return arrows?.shown === true && arrows.where === "volume";
}

/** How many arrows the density slider offers, from sparse to dense. */
export const ARROW_DENSITIES = [12, 24, 48, 96, 192] as const;
export const MAX_ARROWS = 400;

/**
 * A decimated set of arrows from a slice's samples, **spread evenly over the cut**.
 *
 * **Chosen by POSITION, not by speed** (2026-11-04, the owner's "vectors are not correct"). The first
 * rule took the fastest samples, which on a mixing elbow put every arrow in the jet's core: the slow
 * main stream, the recirculation behind the jet and the corner - the places a vector plot is read
 * for - had none, and an empty region read as "no flow". A vector plot is a sample of the field on a
 * grid, and a slow region must show short arrows, not nothing.
 *
 * The cut's samples are binned into square cells (cubes for a 3-D cloud) whose size is searched so
 * that the occupied cells are as many as `count` allows; each cell keeps the sample nearest its centre.
 * The result is in the slice's own order. A sample with no flow is skipped (a zero-length arrow is a dot
 * that reads as a bug).
 */
export function arrowSeeds(
  points: readonly (readonly [number, number, number])[],
  vectors: readonly (readonly [number, number, number])[],
  count: number,
): ArrowSeed[] {
  const want = Math.min(MAX_ARROWS, Math.max(0, Math.trunc(count)));
  if (want === 0) return [];
  const seeds: ArrowSeed[] = [];
  for (let i = 0; i < points.length && i < vectors.length; i++) {
    const v = vectors[i]!;
    const speed = Math.hypot(v[0], v[1], v[2]);
    if (!(speed > 0)) continue;
    seeds.push({ point: points[i]!, vector: v, speed });
  }
  if (seeds.length <= want) return seeds;
  return evenlySpread(seeds, want);
}

/**
 * The arrows of a VOLUME sample (`run.vectors`): already an even lattice through the fluid, so nothing is
 * decimated here - only a sample with no flow is skipped, as on a slice.
 */
export function volumeArrowSeeds(
  points: readonly (readonly [number, number, number])[],
  vectors: readonly (readonly [number, number, number])[],
): ArrowSeed[] {
  const seeds: ArrowSeed[] = [];
  for (let i = 0; i < points.length && i < vectors.length; i++) {
    const v = vectors[i]!;
    const speed = Math.hypot(v[0], v[1], v[2]);
    if (speed > 0) seeds.push({ point: points[i]!, vector: v, speed });
  }
  return seeds;
}

/** At most `want` of `seeds`, one per occupied cell of the coarsest even grid that still fills `want` cells. */
function evenlySpread(seeds: readonly ArrowSeed[], want: number): ArrowSeed[] {
  const low = [Infinity, Infinity, Infinity];
  const high = [-Infinity, -Infinity, -Infinity];
  for (const s of seeds) {
    for (let a = 0; a < 3; a++) {
      low[a] = Math.min(low[a]!, s.point[a]!);
      high[a] = Math.max(high[a]!, s.point[a]!);
    }
  }
  const span = Math.max(high[0]! - low[0]!, high[1]! - low[1]!, high[2]! - low[2]!);
  if (!(span > 0)) return seeds.slice(0, want);
  const pick = (size: number): Map<string, number> => {
    const best = new Map<string, number>();
    const distance = new Map<string, number>();
    seeds.forEach((s, i) => {
      const cell = [0, 1, 2].map((a) => Math.floor((s.point[a]! - low[a]!) / size));
      const key = cell.join(",");
      let d = 0;
      for (let a = 0; a < 3; a++) d += (s.point[a]! - (low[a]! + (cell[a]! + 0.5) * size)) ** 2;
      const was = distance.get(key);
      if (was === undefined || d < was) {
        distance.set(key, d);
        best.set(key, i);
      }
    });
    return best;
  };
  // The occupied cells shrink as the cell grows: search for the smallest cell that gives at most `want`.
  let small = span * 1e-6;
  let large = span * 1.0001;
  let chosen = pick(large);
  for (let step = 0; step < 30; step++) {
    const mid = Math.sqrt(small * large);
    const cells = pick(mid);
    if (cells.size > want) small = mid;
    else {
      large = mid;
      chosen = cells;
    }
  }
  const keep = [...chosen.values()].sort((a, b) => a - b);
  return keep.map((i) => seeds[i]!);
}

/**
 * The largest extent of a cut, from its own points - the size an arrow is drawn against.
 *
 * **Pure and separate** so it can be tested without a scene: the first version used the MODEL's bounds,
 * which made a 20 mm cut carry 50 mm arrows.
 */
export function cutSpan(points: readonly (readonly [number, number, number])[]): number {
  if (points.length === 0) return 0;
  let span = 0;
  for (let axis = 0; axis < 3; axis++) {
    let min = Infinity;
    let max = -Infinity;
    for (const p of points) {
      const v = p[axis]!;
      if (v < min) min = v;
      if (v > max) max = v;
    }
    span = Math.max(span, max - min);
  }
  return span;
}

/** The longest arrow, in metres, for a slice of `span` - so arrows scale with the model, not the screen. */
export function arrowLength(span: number, scale: number): number {
  // **A tenth of the span at scale 1**: long enough to read a direction, short enough that neighbouring
  // arrows do not overlap on the audit's 20 mm strip.
  return Math.max(0, span) * 0.1 * Math.max(0, scale);
}

/**
 * Whether a field NAME is a vector, by the names a solver writes them under.
 *
 * **A named list, not a shape**: `run.fields` carries no component count, so a caller cannot ask "is this
 * three-component" - it can only recognise the velocity by its conventional name.
 */
export function isVectorFieldName(name: string): boolean {
  return name === "U" || name === "velocity" || name === "V";
}

/**
 * The fields whose magnitude IS a velocity's, so a slice behind them can carry arrows.
 *
 * **`U_mag` counts**: the slice layer asks the server for `U` when asked to paint `|U|`
 * (`sliceValues.requestedFieldOf`, decision c), and the header ranges `U` over its magnitude - so the
 * arrows get their directions from the same request that colours the cut.
 */
export const VELOCITY_MAGNITUDE: ReadonlySet<string> = new Set(["U_mag"]);
