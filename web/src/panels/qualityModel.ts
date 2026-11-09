/**
 * The Quality page's pure model (brief M-507): the verdict line, the histogram bars with the
 * limit's position, the three distribution states, and the statistics table's rows.
 *
 * **The data shapes are the backend's**: checkMesh's numbers from the result's `quality`
 * (`mesh.quality`'s `checkmesh`), the distributions from `meta.quality_dist` (`mesh.quality`'s
 * `distributions` — Part B's reply, `{"pending": true}` until the follow-up fetch returns).
 * Everything the tests pin is decided here, so the component only draws.
 */

/** The project's quality limits, from the settings. */
export interface QualityLimits {
  max_non_ortho: number;
  max_skewness: number;
}

/** One metric's histogram, as Part B's reply carries it. */
export interface MetricHistogram {
  unit: string;
  edges: number[];
  counts: number[];
  max: number;
  mean: number;
  valid?: boolean;
  reason?: string;
}

/** The distributions block, in its three states. */
export type Distributions =
  | { pending: true }
  | { error: string }
  | { metrics: Record<string, MetricHistogram>; cell_types?: Record<string, number> };

/** The verdict line's words, from the existing `meshPanelModel` tone. */
export function verdictLine(
  meshOk: boolean | null,
  limits: QualityLimits,
  nonOrthoMax: number | null,
): string {
  if (meshOk === null) return "";
  if (meshOk && nonOrthoMax !== null && nonOrthoMax > limits.max_non_ortho) {
    return `Quality not met: non-orthogonality ${trim(nonOrthoMax)}° over the ${trim(
      limits.max_non_ortho,
    )}° limit`;
  }
  if (meshOk && nonOrthoMax !== null && nonOrthoMax > limits.max_skewness * 20) {
    // A skewness over the limit reads the same way (the limits carry both).
    return `Quality not met: the limits are exceeded`;
  }
  return meshOk ? "Mesh OK · within this project's limits" : "Quality not met";
}

function trim(value: number): string {
  return String(parseFloat(value.toFixed(2)));
}

/** One histogram bar, positioned on the page's axis. */
export interface BarView {
  /** The bin's left edge (the x position the bar grows from). */
  from: number;
  to: number;
  count: number;
  /** True when the bin's right edge is at or beyond the project's limit. */
  overLimit: boolean;
}

/**
 * The bars of one metric's histogram, with the limit's position marked on each bar.
 *
 * **A metric with `valid: false` has no bars**: the caller shows the checkMesh maximum and the
 * reason instead (the page's text mode) — the distributions' own validation rule, surfaced.
 */
export function barsFor(
  metric: MetricHistogram,
  limit: number | null,
): BarView[] {
  if (metric.valid === false) return [];
  const bars: BarView[] = [];
  for (let i = 0; i < metric.counts.length; i++) {
    const from = metric.edges[i];
    const to = metric.edges[i + 1];
    if (from === undefined || to === undefined) continue;
    bars.push({
      from,
      to,
      count: metric.counts[i],
      overLimit: limit !== null && to > limit,
    });
  }
  return bars;
}

/** Whether the aspect-ratio axis is logarithmic (brief M-507 §UI 2: "Aspect ratio on a log axis"). */
export function isLogAxis(unit: string): boolean {
  return unit === "";
}

/** The x axis's label: the unit, or the plain word for a dimensionless metric. */
export function axisLabel(metric: MetricHistogram): string {
  return metric.unit === "" ? "ratio" : metric.unit;
}

/** The three states the page draws, decided once (brief M-507's "API first"). */
export function distributionState(
  dist: Distributions | undefined,
): "pending" | "ready" | "error" | "absent" {
  if (dist === undefined) return "absent";
  if ("pending" in dist) return "pending";
  if ("error" in dist) return "error";
  return "ready";
}

/** The pending line's words: the verdict and statistics are ALREADY shown from checkMesh. */
export const PENDING_LINE = "Working out the distributions…";

/** One worst face, as the distributions' reply carries it (brief M-507 §UI 4). */
export interface WorstFace {
  metric: string;
  face: number;
  value: number;
  centre: [number, number, number];
  owner: number;
  neighbour: number | null;
}

/**
 * The worst-faces rows for one metric, worst first, capped at 10 (brief M-507 §UI 4: "the top
 * 10 per metric"). The position is the face's centre in project units — the page prints the
 * value and the position; the camera work is the viewer half.
 */
export function worstFaces(
  reply: { worst?: WorstFace[] } | undefined,
  metric: string,
): WorstFace[] {
  const all = reply?.worst ?? [];
  return all.filter((face) => face.metric === metric).slice(0, 10);
}

/** The worst face's value, trimmed for the table. */
export function worstValue(value: number, unit: string): string {
  if (unit === "deg") return `${parseFloat(value.toFixed(2))}°`;
  return String(parseFloat(value.toFixed(4)));
}

/** The worst face's position, in project units, as the table prints it. */
export function worstCentre(centre: [number, number, number]): string {
  return centre.map((c) => String(parseFloat(c.toFixed(4)))).join(", ");
}

/** The cell-type table's rows, zero rows hidden (brief M-507 §UI 5). */
export function cellTypeRows(
  cellTypes: Record<string, number> | undefined,
): Array<{ name: string; count: number }> {
  if (!cellTypes) return [];
  const names: Array<[string, string]> = [
    ["hex", "hexahedra"],
    ["prism", "prisms"],
    ["wedge", "wedges"],
    ["pyramid", "pyramids"],
    ["tet_wedge", "tet wedges"],
    ["tet", "tetrahedra"],
    ["poly", "polyhedra"],
  ];
  return names
    .map(([key, name]) => ({ name, count: cellTypes[key] ?? 0 }))
    .filter((row) => row.count > 0);
}