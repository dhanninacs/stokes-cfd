/**
 * The streamlines control's state and its seeds: where the lines start, and what went wrong.
 *
 * **Seeds come from a SLICE, not a wall** - measured on the audit's duct: the inlet and outlet are
 * perpendicular to the flow and the walls carry exactly zero by no-slip, so **no patch of that model can
 * carry a streamline**. A slice through the fluid can, and it is the surface a user is already looking
 * at. The seeds are therefore sampled ON the slice's plane.
 *
 * **A rake, not one seed**: a single streamline shows one path, and what a user wants from a cut is the
 * pattern across it. So the seeds are a grid over the slice's extent, and the count is bounded because
 * each line is an integration.
 */
export type StreamlinePhase = "idle" | "computing" | "ready" | "failed";

export interface StreamlineState {
  phase: StreamlinePhase;
  /** The lines as the command answered: points in metres and the speed at each. */
  lines: readonly { points: readonly (readonly [number, number, number])[]; speeds: readonly number[] }[];
  /** The speed range across ALL lines, so one picture uses one colour map. */
  range: { min: number; max: number } | null;
  /** What went wrong, in words, or "". */
  message: string;
  /** Drawn as tubes of a width in metres, not hairlines (the Style choice). */
  tubes?: boolean;
}

export const NO_STREAMLINES: StreamlineState = { phase: "idle", lines: [], range: null, message: "" };

/** How many seeds a rake may use: each one is an integration, so this is a cost limit, not a taste one. */
export const MAX_SEEDS = 40;

/**
 * A grid of seed points over a slice's extent, at the slice's own height.
 *
 * `count` is the number wanted; the grid is as close to square as that allows, so a wide cut gets more
 * seeds across than along - which is where the flow changes faster. **`count <= 0` gives no seeds**, and
 * the caller says so rather than drawing nothing silently.
 */
export function seedRake(
  extent: { min: readonly [number, number, number]; max: readonly [number, number, number] },
  normal: 0 | 1 | 2,
  count: number,
): [number, number, number][] {
  const wanted = Math.min(MAX_SEEDS, Math.max(0, Math.trunc(count)));
  if (wanted === 0) return [];
  // The two axes the SLICE spans: the normal's axis is constant.
  const axes = [0, 1, 2].filter((a) => a !== normal) as [number, number];
  const along = Math.max(1, Math.round(Math.sqrt(wanted)));
  const across = Math.max(1, Math.round(wanted / along));
  const out: [number, number, number][] = [];
  for (let i = 0; i < along; i++) {
    for (let j = 0; j < across; j++) {
      if (out.length >= wanted) break;
      const p: [number, number, number] = [0, 0, 0];
      // **The seeds sit INSIDE the extent**: at the very edge a streamline starts on the boundary and
      // leaves at once, which is the "no lines" answer the walls gave.
      p[normal] = (extent.min[normal] + extent.max[normal]) / 2;
      p[axes[0]] = extent.min[axes[0]] + ((i + 0.5) / along) * (extent.max[axes[0]] - extent.min[axes[0]]);
      p[axes[1]] = extent.min[axes[1]] + ((j + 0.5) / across) * (extent.max[axes[1]] - extent.min[axes[1]]);
      out.push(p);
    }
  }
  return out;
}

/** The speed range across every line, for the colour map, or null when there is nothing to colour. */
export function speedRangeOf(
  lines: readonly { speeds: readonly number[] }[],
): { min: number; max: number } | null {
  const all = lines.flatMap((l) => [...l.speeds]).filter((s) => Number.isFinite(s));
  if (all.length === 0) return null;
  return { min: Math.min(...all), max: Math.max(...all) };
}
