/**
 * Which entries of an imported case's dictionaries the Guided Fluid, Boundaries and Run cards show
 * (`CaseSettingsCard`). Pure, so the tests run without the store.
 */
import type { SettingsRow } from "../../panels/caseCardModel";

/** Which entries a card shows: a file and path patterns (`*` matches one path element). */
export interface CasePick {
  file: string;
  /** Path patterns, e.g. `["RAS", "model"]` or `["boundaryField", "*", "value"]`. */
  paths: string[][];
}

/** The picks of each card. Files a case does not have are skipped, so one list serves every tutorial. */
export const CASE_PICKS: Record<"fluid" | "boundaries" | "run", CasePick[]> = {
  fluid: [
    { file: "constant/momentumTransport", paths: [["simulationType"], ["RAS", "model"], ["LES", "model"], ["LES", "delta"]] },
    { file: "constant/physicalProperties", paths: [["nu"], ["viscosityModel"], ["rho"]] },
    { file: "constant/thermophysicalTransport", paths: [["RAS", "Prt"], ["laminar", "Prt"]] },
  ],
  boundaries: [
    { file: "0/U", paths: [["boundaryField", "*", "value"], ["boundaryField", "*", "type"]] },
    { file: "0/p", paths: [["boundaryField", "*", "value"]] },
    { file: "0/T", paths: [["boundaryField", "*", "value"]] },
  ],
  // No `decomposeParDict` here: an imported case runs every step on one rank for now
  // (`stokes/run/external.py`), so its subdomain count would change nothing it runs.
  run: [{ file: "system/controlDict", paths: [["endTime"], ["deltaT"], ["writeInterval"], ["maxCo"], ["adjustTimeStep"]] }],
};

/** Whether `path` matches `pattern` exactly, `*` standing for any one element. */
export function matchesPath(path: readonly string[], pattern: readonly string[]): boolean {
  return path.length === pattern.length && pattern.every((p, i) => p === "*" || p === path[i]);
}

/** The editable rows of a file that a pick asks for, in the file's own order. */
export function pickRows(rows: readonly SettingsRow[], pick: CasePick): SettingsRow[] {
  return rows.filter((r) => r.kind === "entry" && !r.truncated && pick.paths.some((p) => matchesPath(r.path, p)));
}

/** A row's label: its path without the dictionary wrappers a user does not think in. */
export function rowLabel(row: SettingsRow): string {
  return row.path.filter((p) => p !== "boundaryField").join(" · ");
}
