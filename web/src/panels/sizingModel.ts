/**
 * The Sizing page's pure model (brief M-503): what each row shows, from the same numbers the
 * snapshot resolves — the placeholder IS the size the dictionary will use, so the page cannot
 * disagree with it.
 *
 * **Pure on purpose**, like every panel model here: the reply shapes are the backend's
 * (`mesh.estimate`, `mesh.precheck`, the tree's nodes) and everything the tests pin — the
 * placeholder words, the achieved size, the deviation flag, the estimate line — is decided here.
 */

import { followsShape } from "./mesherKinds";

/** One body row's inputs, from the tree. */
export interface BodyRow {
  id: string;
  name: string;
  /** The body's own stored size, in metres, or null (the empty = inherit state). */
  size: number | null;
  refinement: number;
  /** Whether this body is the one the fluid mesh is built from. */
  isDomain: boolean;
}

/** One boundary row's inputs, from the tree. */
export interface BoundaryRow {
  name: string;
  /** The boundary's own stored size, in metres, or null. */
  size: number | null;
}

/** The sizing facts a row is resolved against: the settings and the estimate's levels. */
export interface SizingInputs {
  baseSize: number;
  surfaceSize: number;
  mesher: string;
  /** The estimate's per-surface rows, by surface name (`mesh.estimate`'s `levels`). */
  levels: Array<{ surface: string; level: number; achieved: number; deviation: number }>;
}

/** What a size field's placeholder says: the value that will really be used, and where from. */
export function placeholderFor(
  own: number | null,
  inherited: number,
): string {
  if (own !== null && own > 0) {
    // The field holds a value; the placeholder is moot, and an empty string is what a
    // text input shows when it must say nothing.
    return "";
  }
  return `from the mesh settings: ${formatMm(inherited)}`;
}

/** The surface-size field's placeholder: what 0 actually means, in words (brief M-503 §UI). */
export const SURFACE_SIZE_PLACEHOLDER =
  "walls half the base size, other faces the base size";

/** A length in metres as the page shows it: millimetres when that reads better. */
export function formatMm(metres: number): string {
  if (metres >= 0.001) {
    const mm = metres * 1000;
    return `${trim(mm)} mm`;
  }
  return `${trim(metres * 1_000_000)} µm`;
}

function trim(value: number): string {
  // Two decimals, no trailing zeros: "4 mm", "2.5 mm", "500 µm".
  return String(parseFloat(value.toFixed(2)));
}

/** One body row as the page draws it, resolved through the same levels the estimate carries. */
export interface BodyView {
  id: string;
  name: string;
  /** The size that will really be used on this body's faces, in metres. */
  effective: number;
  fromSettings: boolean;
  level: number;
  achieved: number;
  /** True when the achieved size misses the ask by more than a fifth (the deviation warning). */
  deviation: boolean;
  editable: boolean;
}

/** The level a body's faces get, from the estimate's levels or the rule: walls one level above
 * the others, which is what the rule resolves to when nothing nearer is set. */
function levelForBody(
  row: BodyRow,
  inputs: SizingInputs,
  domainPart: { size: number | null; refinement: number } | null,
): { achieved: number; level: number; deviation: boolean } {
  const effective = effectiveSize(row, inputs, domainPart);
  // level = round(log2(base / size)), the dictionary's own arithmetic (stokes.mesh.levels).
  const level = Math.max(0, Math.round(Math.log2(inputs.baseSize / effective)));
  const achieved = inputs.baseSize / 2 ** level;
  const deviation = Math.abs(achieved - effective) / effective > 0.2;
  return { achieved, level, deviation };
}

/** The size that will really be used on a body's faces: body > surface_size (>0) > rule. */
export function effectiveSize(
  row: BodyRow,
  inputs: SizingInputs,
  _domainPart: { size: number | null; refinement: number } | null,
): number {
  if (row.size !== null && row.size > 0) return row.size;
  if (inputs.surfaceSize > 0) return inputs.surfaceSize;
  return inputs.baseSize / 2;
}

/**
 * The bodies table: every part, resolved; the domain body editable, the others not (brief M-503
 * §UI: "Only the domain body is editable; any other body's row reads 'not meshed in this fluid
 * mesh'").
 */
export function bodyViews(
  rows: BodyRow[],
  inputs: SizingInputs,
  domainId: string | null,
): BodyView[] {
  const domain = rows.find((r) => r.id === domainId) ?? null;
  const domainData = domain !== null ? { size: domain.size, refinement: domain.refinement } : null;
  return rows.map((row) => {
    const resolved = levelForBody(row, inputs, domainData);
    return {
      id: row.id,
      name: row.name,
      effective: resolved.achieved > 0 ? effectiveSize(row, inputs, domainData) : resolved.achieved,
      fromSettings: row.size === null || row.size <= 0,
      level: resolved.level,
      achieved: resolved.achieved,
      deviation: resolved.deviation,
      editable: row.id === domainId,
    };
  });
}

/** One boundary row as the page draws it. */
export interface BoundaryView {
  name: string;
  /** The boundary's own size, or null (empty = the body's). */
  size: number | null;
  effective: number;
  level: number;
  achieved: number;
}

/** The boundaries table: same precedence, the boundary's size first. */
export function boundaryViews(
  rows: BoundaryRow[],
  inputs: SizingInputs,
): BoundaryView[] {
  return rows.map((row) => {
    const effective =
      row.size !== null && row.size > 0
        ? row.size
        : inputs.surfaceSize > 0
          ? inputs.surfaceSize
          : inputs.baseSize / 2;
    const level = Math.max(0, Math.round(Math.log2(inputs.baseSize / effective)));
    const achieved = inputs.baseSize / 2 ** level;
    return { name: row.name, size: row.size, effective, level, achieved };
  });
}

/** The estimate line's words: "About 180k-310k cells (limit 5M)". */
export function estimateLine(numbers: {
  low: number;
  high: number;
  max_cells: number;
  over: boolean;
}): string {
  return `About ${formatK(numbers.low)}-${formatK(numbers.high)} cells (limit ${formatK(
    numbers.max_cells,
  )})`;
}

/** The warning the over-the-limit line carries (brief M-503 §UI). */
export const OVER_LIMIT_ADVICE = "Raise the base size or the limit";

function formatK(cells: number): string {
  if (cells >= 1_000_000) {
    return `${trim(cells / 1_000_000)}M`;
  }
  if (cells >= 1000) {
    return `${Math.round(cells / 1000)}k`;
  }
  return String(cells);
}

/** Whether the page shows the local-size controls at all: blockMesh has no surfaces to refine. */
export function localSizesApply(mesher: string): boolean {
  return followsShape(mesher);
}

/** The blockMesh page's one honest line (brief M-503 §UI). */
export const BLOCKMESH_NOTE = "Local sizes apply to snappyHexMesh and the cfMesh types only";