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

/** One line as the command answers it: points, the speed at each, and the other fields it carried. */
export interface TracedLine {
  points: readonly (readonly [number, number, number])[];
  speeds: readonly number[];
  /** Scalar fields along the line (T, p, ...), one value per point; absent from an older host. */
  values?: Readonly<Record<string, readonly number[]>>;
}

export interface StreamlineState {
  phase: StreamlinePhase;
  /**
   * The lines as drawn: points in metres and, in `speeds`, the value each point is coloured by (the speed,
   * or the field named by `colorBy`).
   */
  lines: readonly TracedLine[];
  /** The lines as the command answered, kept so the colouring can change without tracing again. */
  traced?: readonly TracedLine[];
  /** The field the lines are coloured by: "" for the speed. */
  colorBy?: string;
  /** The speed range across ALL lines, so one picture uses one colour map. */
  range: { min: number; max: number } | null;
  /** What went wrong, in words, or "". */
  message: string;
  /** Drawn as tubes of a width in metres, not hairlines (the Style choice). */
  tubes?: boolean;
}

export const NO_STREAMLINES: StreamlineState = { phase: "idle", lines: [], range: null, message: "" };

/** The scalar fields every traced line carries, in the order the host listed them. */
export function carriedFields(lines: readonly TracedLine[]): string[] {
  if (lines.length === 0) return [];
  const first = Object.keys(lines[0].values ?? {});
  return first.filter((name) => lines.every((l) => (l.values?.[name]?.length ?? -1) === l.points.length));
}

/**
 * The lines coloured by `field` ("" for the speed): the field's values take the place of the speeds, which is
 * what the drawing and the range read. Null when a line does not carry the field.
 */
export function coloredBy(traced: readonly TracedLine[], field: string): TracedLine[] | null {
  if (field === "") return traced.map((l) => ({ ...l }));
  const out: TracedLine[] = [];
  for (const line of traced) {
    const values = line.values?.[field];
    if (values === undefined || values.length !== line.points.length) return null;
    out.push({ ...line, speeds: values });
  }
  return out;
}

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

/**
 * The speed range across every line, for the colour map, or null when there is nothing to colour.
 *
 * **Stray samples do not set the range.** A traced line can carry one sample far above the field
 * (measured: 0.834 m/s on a field whose maximum is 0.277, where a line leaves through a wall cell);
 * as the maximum it squeezed every real speed into the bottom third of the colour map, so the lines
 * drew one dull colour while the legend read the field's range. The maximum is therefore the largest
 * sample within 1.5 times the 99.5th percentile, and the minimum likewise from below; samples
 * outside are drawn with the end colours.
 */
export function speedRangeOf(
  lines: readonly { speeds: readonly number[] }[],
): { min: number; max: number } | null {
  const all: number[] = [];
  for (const l of lines) for (const s of l.speeds) if (Number.isFinite(s)) all.push(s);
  if (all.length === 0) return null;
  all.sort((a, b) => a - b);
  const at = (q: number) => all[Math.min(all.length - 1, Math.max(0, Math.round(q * (all.length - 1))))];
  const low = at(0.005);
  const high = at(0.995);
  const span = Math.max(high - low, 0);
  const ceiling = high + 0.5 * Math.max(span, Math.abs(high) * 0.5);
  const floor = low - 0.5 * Math.max(span, Math.abs(low) * 0.5);
  let min = all[0];
  let max = all[all.length - 1];
  if (max > ceiling) for (let i = all.length - 1; i >= 0; i -= 1) if (all[i] <= ceiling) { max = all[i]; break; }
  if (min < floor) for (let i = 0; i < all.length; i += 1) if (all[i] >= floor) { min = all[i]; break; }
  return { min, max };
}

/**
 * What to draw: the lines coloured by the field the view shows when they carry it (temperature on a heat
 * exchanger's lines, so the lines and the legend agree), else as they are. A colouring chosen on purpose
 * (`colorBy` set, the Full panel's chips) is kept. The speed fields colour by the speed.
 */
export function linesForField(state: StreamlineState, field: string | null): { lines: readonly TracedLine[]; range: StreamlineState["range"] } {
  if (state.colorBy !== undefined || field === null || field === "" || field === "U" || field === "U_mag") return { lines: state.lines, range: state.range };
  const again = coloredBy(state.traced ?? state.lines, field);
  return again === null ? { lines: state.lines, range: state.range } : { lines: again, range: speedRangeOf(again) };
}
