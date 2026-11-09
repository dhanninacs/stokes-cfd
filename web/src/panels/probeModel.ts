/**
 * The probe's state and what the panel shows: one point, or a line of them.
 *
 * **A probe answers two questions the audit's users asked for** (results item 2): *"what is |U| at this
 * point?"* and *"what is the profile along this line?"*. Both are the same command - `run.probe` takes
 * a `point` or a `polyline` - so the model here is only about WHICH and WHAT WENT WRONG, kept pure so
 * the panel and the viewport agree without a second copy.
 *
 * **"Preparing" is a state, not an error** (claude-win2's contract). The command answers
 * `post.triangulation_preparing` while the surface's triangulation is built in the background, and the
 * panel must show that as *"preparing…"* and retry rather than as a failure - a user who sees "error"
 * for a value that arrives a moment later learns to distrust the panel.
 */
export type ProbePhase = "idle" | "preparing" | "ready" | "failed" | "outside";

export interface ProbeState {
  phase: ProbePhase;
  /** The points asked about, in order: one for a point probe, many for a line. */
  points: readonly (readonly [number, number, number])[];
  /** The values `run.probe` answered with, one per point. Empty unless `phase` is `ready`. */
  values: readonly number[];
  /** The field probed, so a stale answer is not drawn under a new field's name. */
  field: string | null;
  /** What went wrong, in words, or "". */
  message: string;
}

export const NO_PROBE: ProbeState = { phase: "idle", points: [], values: [], field: null, message: "" };

/** The error codes `run.probe` can answer with, and what each means to the panel. */
export function phaseOfCode(code: string | null | undefined): ProbePhase {
  if (code === "post.triangulation_preparing") return "preparing";
  // **"Outside the surface" is a different fact from a failure**: the probe worked and the point is
  // not on anything, which a user fixes by picking again rather than by retrying.
  if (code === "post.probe_outside" || code === "post.probe_invalid") return "outside";
  return code ? "failed" : "idle";
}

/** True when the panel should try again by itself - the only phase where retrying helps. */
export function shouldRetry(phase: ProbePhase): boolean {
  return phase === "preparing";
}

/**
 * The probe's points as the line plot needs them: distance along the line from the first point, and
 * the value there.
 *
 * **A single point has no distance**, so its plot is one marker at 0; a polyline's x-axis is the
 * cumulative chord length, which is what a user reads as "along the line". Using the index instead
 * would stretch a plot of unevenly spaced picks into evenly spaced ones - a chart that lies about
 * where the readings were taken.
 */
export function probeCurve(points: readonly (readonly [number, number, number])[], values: readonly number[]): { at: number; value: number }[] {
  if (points.length === 0 || values.length !== points.length) return [];
  const out: { at: number; value: number }[] = [];
  let along = 0;
  for (let i = 0; i < points.length; i++) {
    if (i > 0) {
      const a = points[i - 1]!;
      const b = points[i]!;
      along += Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
    }
    out.push({ at: along, value: values[i]! });
  }
  return out;
}

/** The readings as a table row set, for the panel's numbers under the plot. */
export function probeRows(values: readonly number[]): { index: number; value: string }[] {
  // **No formatting niceties**: a probe shows a number at a point, and `formatTick` is the panel's
  // existing convention for "three significant digits, e-notation at the extremes".
  return values.map((value, index) => ({ index, value: String(Number(value.toPrecision(4))) }));
}

/** A point on the probe's plot: where it is along the line, in metres, and the value there. */
export interface ChartPoint {
  x: number;
  y: number;
}

/** The plot for a probe: its points mapped into the box, and the axis ends. */
export interface ProbeChart {
  points: ChartPoint[];
  /** The path for the `<polyline>`, or "" when there is nothing to draw. */
  path: string;
  /** The distance and value at each end, for the axis labels. */
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
}

/**
 * The probe's line plot, as geometry.
 *
 * **A LINE, not a chart of categories**: the x-axis is distance along the line (from `probeCurve`), so
 * a profile has a shape a user reads directly - where the velocity rises, where it flattens. Points are
 * joined in the order they were taken, which is the order of the polyline.
 *
 * **A flat value still draws**, because "every reading the same" is a result: `yMin === yMax` would
 * divide by zero, so the span is widened by one to put the line across the middle of the box.
 */
export function probeChart(
  curve: readonly { at: number; value: number }[],
  width: number,
  height: number,
  pad = 12,
): ProbeChart {
  if (curve.length === 0 || width <= 2 * pad || height <= 2 * pad) {
    return { points: [], path: "", xMin: 0, xMax: 0, yMin: 0, yMax: 0 };
  }
  const xs = curve.map((p) => p.at);
  const ys = curve.map((p) => p.value);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  let yMin = Math.min(...ys);
  let yMax = Math.max(...ys);
  if (yMax - yMin < 1e-12) {
    // A single reading, or every reading equal: a span of one keeps the line mid-box.
    yMin -= 0.5;
    yMax += 0.5;
  }
  const spanX = xMax - xMin < 1e-12 ? 1 : xMax - xMin;
  const px = (v: number) => pad + ((v - xMin) / spanX) * (width - 2 * pad);
  // **The value axis is inverted on the way to the screen**: SVG's y grows downward.
  const py = (v: number) => height - pad - ((v - yMin) / (yMax - yMin)) * (height - 2 * pad);
  const points = curve.map((p) => ({ x: px(p.at), y: py(p.value) }));
  return {
    points,
    path: points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" "),
    xMin,
    xMax,
    yMin,
    yMax,
  };
}
