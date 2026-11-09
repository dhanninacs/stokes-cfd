/**
 * The Surface page's pure model (brief M-515): the staged surfaces against the limit, the
 * over-limit tone, and the feature-angle words.
 *
 * The rows come from `mesh.precheck`'s `surfaces` (`{name, triangles, bytes}`) and its
 * `staging_limit` — the SAME numbers the agent stages by (`stokes.geometry.export.MAX_STAGED_BYTES`,
 * 500 MiB), so a bar that reads green here cannot be refused on the host.
 */

import { followsShape } from "./mesherKinds";

/** One staged surface, as `mesh.precheck` returns it. */
export interface SurfaceRow {
  name: string;
  triangles: number;
  bytes: number;
}

/** One row as the page draws it. */
export interface SurfaceView {
  name: string;
  triangles: number;
  bytes: number;
  /** 0..1 of the staging limit; clamped so an over-limit bar still renders. */
  fraction: number;
  /** True when this surface alone is at or over the limit — warning tone, with the finding. */
  over: boolean;
}

/** The default staging limit the page shows a bar against (the backend's own constant). */
export const STAGING_LIMIT_DEFAULT = 500 * 1024 * 1024;

/** The rows with their bar fractions and over-limit flags. */
export function surfaceViews(
  rows: SurfaceRow[],
  limit: number = STAGING_LIMIT_DEFAULT,
): SurfaceView[] {
  return rows.map((row) => ({
    ...row,
    fraction: limit > 0 ? Math.min(1, row.bytes / limit) : 1,
    over: limit > 0 && row.bytes >= limit,
  }));
}

/** The page's one honest sentence about what snappy receives (brief M-503's M-515 sibling). */
export const NO_SURFACE_MESH_NOTE =
  "The mesher builds the volume straight from these surfaces; there is no separate surface mesh " +
  "to inspect, so this page shows what it receives.";

/** The blockMesh page's line, with no field beside it. */
export const BLOCKMESH_NOTE = "Surfaces and sharp edges apply to snappyHexMesh and the cfMesh types only";

/** The feature-angle field's label words: the unit is part of the meaning. */
export function angleLabel(angle: number): string {
  return `Keep edges sharper than ${trim(angle)}°`;
}

/** One line on what the threshold decides, for under the field. */
export const ANGLE_EXPLANATION =
  "A sharper edge is more likely to be a real feature of the shape; a shallower one is more " +
  "likely to be tessellation noise. Edges at or above this angle are extracted and refined.";

/** The stepper's value text for task 5, from the same numbers the backend's row carries. */
export function stepperValue(
  angle: number,
  surfaces: number,
  bytes: number,
): string {
  const staged = bytes > 0 ? ` · ${(bytes / 1048576).toFixed(1)} MiB to send` : "";
  return `${trim(angle)}° edges · ${surfaces} surfaces${staged}`;
}

/** Whether the page shows the surfaces and the angle at all: blockMesh has neither. */
export function appliesTo(mesher: string): boolean {
  return followsShape(mesher);
}

function trim(value: number): string {
  return String(parseFloat(value.toFixed(2)));
}