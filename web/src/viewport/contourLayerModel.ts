import type { ContourSet } from "../panels/isoModel";
/** What the viewport drew of a set of 3-D contours: pure, so the store can hold it without importing the layer. */

/** What the layer has drawn of the set asked for. */
export interface ContoursDrawn {
  key: string;
  shown: number;
  empty: number;
  failed: number;
  total: number;
}

export const NO_CONTOURS_DRAWN: ContoursDrawn = { key: "", shown: 0, empty: 0, failed: 0, total: 0 };

/** The key a set is fetched under: a new run, field or list of values is a new set; the opacity is a restyle. */
export function contourKey(set: ContourSet): string {
  return `${set.run}|${set.field}|${set.values.join(",")}`;
}

/** `contours T: 5 levels, 315 to 335 K` while they arrive and once in; a level the field never takes is said. */
export function contoursLine(set: ContourSet, drawn: ContoursDrawn): string {
  const lo = set.values[0];
  const hi = set.values[set.values.length - 1];
  const span = lo === undefined || hi === undefined ? "" : `, ${lo} to ${hi}${set.units !== "" ? ` ${set.units}` : ""}`;
  const done = drawn.shown + drawn.empty + drawn.failed;
  const coming = done < drawn.total ? ` · cutting ${done + 1} of ${drawn.total}` : "";
  const missing = drawn.failed > 0 ? ` · ${drawn.failed} not cut` : "";
  return `contours ${set.field}: ${drawn.shown} level${drawn.shown === 1 ? "" : "s"}${span}${coming}${missing}`;
}
