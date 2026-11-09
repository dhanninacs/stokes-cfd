/**
 * The Boundary layers page's pure model (brief M-505): the every-wall fields' words, the per-wall
 * table's placeholders and coverage tone, and the y+ helper's result line.
 *
 * **"layers added" never appears before coverage exists** (ASK Q4, and the brief's rule): a
 * request that has not been meshed is a request — `requestedWords` says so, and no function in
 * this module emits the word "added" about layers that have not been measured.
 */

/** The every-wall settings, as the mesh_settings node holds them. */
export interface LayerSettings {
  layers: number;
  layer_mode: "final_ratio" | "first_height";
  expansion_ratio: number;
  final_ratio: number;
  first_layer_height: number;
  min_thickness_ratio: number;
}

/** One wall boundary, from the tree. */
export interface WallRow {
  name: string;
  /** The boundary's own stored count, or null (empty = inherit). */
  override: number | null;
}

/** The coverage table from the meshed result (`meta.layers`), by patch. */
export interface Coverage {
  patch: string;
  coverage_percent: number;
}

/** Where an inherited count comes from, as the placeholder says it. */
export function inheritedFrom(
  override: number | null,
  settingsLayers: number,
  bodyHasLayers: boolean,
): string {
  if (override !== null) return "";
  if (bodyHasLayers) return "from the body";
  return `from the mesh settings: ${settingsLayers}`;
}

/** The per-wall row: the effective count, the placeholder words, and the override's meaning. */
export interface WallView {
  name: string;
  override: number | null;
  /** The count this wall gets: the override, else the settings'. */
  effective: number;
  placeholder: string;
  /** The override's own meaning: "none" choice maps to 0, empty to inherit. */
  overrideWord: "none" | "inherit" | "";
}

/** The walls' table. Non-walls never appear here — the page lists them in its muted line. */
export function wallViews(
  rows: WallRow[],
  settings: LayerSettings,
  bodyHasLayers: boolean,
): WallView[] {
  return rows.map((row) => ({
    name: row.name,
    override: row.override,
    effective: row.override ?? settings.layers,
    placeholder:
      row.override !== null ? "" : inheritedFrom(null, settings.layers, bodyHasLayers),
    overrideWord: row.override === null ? "inherit" : row.override === 0 ? "none" : "",
  }));
}

/** The muted line for the boundaries layers cannot grow on (brief M-505 §UI). */
export function nonWallLine(names: string[]): string {
  if (names.length === 0) return "";
  const joined = names.slice(0, -1).join(", ");
  const last = names[names.length - 1];
  return `${joined}${names.length > 1 ? " and" : ""}${names.length > 1 ? " " : ""}${last}: no layers (not walls)`;
}

/** The coverage column's words and tone: warning under 80 %, with the wall named. */
export function coverageView(
  wallName: string,
  coverage: Coverage[] | null,
): { text: string; warning: boolean } | null {
  if (coverage === null) return null;
  const row = coverage.find((c) => c.patch === wallName);
  if (row === undefined) return null;
  const rounded = Math.round(row.coverage_percent);
  return {
    text: `layers on ${rounded} % of this wall`,
    warning: row.coverage_percent < 80,
  };
}

/** The line under the "Add 3 layers" button: a REQUEST until a mesh measures it (ASK Q4). */
export function requestedWords(settings: LayerSettings, wallCount: number): string {
  if (settings.layers <= 0) return "";
  return `${settings.layers} layers requested on ${wallCount} wall${wallCount === 1 ? "" : "s"}`;
}

/** The "Add 3 layers on every wall" button's payload: ONE mesh.configure, walls only (ASK Q4). */
export function addThreeLayersPayload(): Record<string, unknown> {
  return {
    layers: 3,
    layer_mode: "final_ratio",
    expansion_ratio: 1.2,
    final_ratio: 0.3,
  };
}

/** Whether the button shows at all: only when the count is 0 (the off state, ASK Q4). */
export function addLayersVisible(settings: LayerSettings): boolean {
  return settings.layers === 0;
}

/** The y+ helper's result line, above the assumptions. */
export function helperLine(numbers: {
  first_height: number;
  reynolds: number;
  method: string;
}): string {
  return `First layer height ${formatMm(numbers.first_height)} — Re ${formatRe(
    numbers.reynolds,
  )} · ${numbers.method}`;
}

/** The one plain line of assumptions, ALWAYS under the helper's result (brief M-505 §UI). */
export const HELPER_ASSUMPTIONS =
  "Estimate for a flat plate with turbulent flow from the leading edge; check it against your " +
  "geometry.";

function formatMm(metres: number): string {
  if (metres >= 0.001) {
    return `${parseFloat((metres * 1000).toFixed(2))} mm`;
  }
  return `${parseFloat((metres * 1_000_000).toFixed(2))} µm`;
}

function formatRe(reynolds: number): string {
  if (reynolds >= 1000) {
    return `${parseFloat((reynolds / 1000).toFixed(0))}k`;
  }
  return String(Math.round(reynolds));
}