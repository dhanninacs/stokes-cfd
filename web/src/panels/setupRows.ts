/**
 * Tree rows for `region` and `boundary` (ADR-0022 D2.10, §9.1; T-047): what a row shows, and the
 * *derived* status it draws.
 *
 * D2.10 is specific about where the status comes from, and it is the rule most likely to be got
 * wrong: **status is derived, not written**. The tree and the conditions list show a boundary or
 * region `stale` when its tag is stale, `failed` when the tag is missing or the kind is invalid,
 * `warning` when `setup.check` has a warning for it — computed by the front end from the tag link and
 * the check result. `boundary.list` and `region.list` compute the same for the CLI and MCP, and
 * nothing writes a status back into the project.
 *
 * That is why this module is pure: given one `boundary.list`/`region.list` entry it returns the row's
 * label, value, swatch, eye and dot. `TreeRow.tsx` renders it and does nothing else, so the rule
 * cannot drift between the tree, the conditions list and the CLI.
 *
 * The value shown is the *primary* one, already in display units: the server sends
 * `primary: {name, value, display}` ("2 m/s"), and the row prints `display` rather than formatting a
 * number itself — the units a user chose are the server's answer, not the front end's guess.
 */

import type { BoundaryEntry, RegionEntry } from "../api/types";

/** The state a row's dot shows; the colours are `SETUP_DOT`'s, so the tree and the readout agree. */
export type RowStatus = "ok" | "warning" | "stale" | "failed";

/** Everything a row renders, decided once so the tree, the list and the CLI cannot disagree. */
export interface SetUpRow {
  /** The node's stable id, for selection and DOM ids. */
  id: string;
  path: string;
  name: string;
  /** The row's kind label ("Velocity inlet"), or "Region" for a region. */
  label: string;
  /** The swatch colour (`#rrggbb`) of the tag this row proxies, or null when it has none. */
  color: string | null;
  /** The tag's eye: whether the tag is drawn. Null for a row without a tag. */
  visible: boolean | null;
  /** The primary value in display units ("2 m/s"), or "" when the entry has none. */
  value: string;
  /** The derived status (D2.10): never stored, always recomputed here. */
  status: RowStatus;
  /** A sentence for the row's tooltip: the kind, the value and what is wrong. */
  detail: string;
  /** True for a plain group from before typed boundaries, which the UI marks rather than hides. */
  legacy: boolean;
}

/**
 * The status of an entry, from the fields the server derived plus the check's warnings (D2.10).
 *
 * Precedence is **failed > stale > warning > ok**: a missing tag is worse than a stale one, and a
 * stale tag is worse than a warning about a value, because a stale tag names ids that no longer
 * exist. An entry the tree shows can therefore be trusted to be at least as bad as its worst field.
 */
export interface StatusInput {
  /** `boundary.list` marks a missing tag on the tag; `region.list` has no `missing`. */
  tag?: { stale?: boolean; missing?: boolean } | null;
  /** `region.list` carries staleness on the entry itself. */
  stale?: boolean;
  status?: string;
}

export function rowStatus(entry: StatusInput): RowStatus {
  const tag = entry.tag;
  if (tag != null && tag.missing === true) return "failed";
  if (entry.status === "error" || entry.status === "failed") return "failed";
  if (entry.stale === true) return "stale";
  if (tag != null && tag.stale === true) return "stale";
  if (entry.status === "stale") return "stale";
  if (entry.status === "warning") return "warning";
  return "ok";
}

/** A boundary row from one `boundary.list` entry. */
export function boundaryRow(entry: BoundaryEntry): SetUpRow {
  const legacy = entry.legacy === true;
  const kind = entry.label ?? entry.kind ?? "";
  const value = entry.primary?.display ?? "";
  const faces = entry.tag?.faces ?? 0;
  const parts = entry.tag?.parts ?? 0;
  const status = rowStatus({ tag: entry.tag ?? null, status: entry.status });
  const label = legacy ? "Plain group" : kind === "" ? "Boundary" : kind;
  const what = legacy
    ? "A plain group from before typed boundaries; assigning a condition makes it one."
    : `${parts} part${parts === 1 ? "" : "s"}, ${faces} face${faces === 1 ? "" : "s"}`;
  const problem =
    entry.tag?.missing === true
      ? " Its tag is missing."
      : entry.tag?.stale === true
        ? " Its tag is stale."
        : (entry.warnings?.length ?? 0) > 0
          ? ` ${entry.warnings!.length} warning(s).`
          : "";
  return {
    id: entry.id,
    path: entry.path,
    name: entry.name,
    label,
    color: entry.tag?.color ?? null,
    visible: entry.tag?.visible ?? null,
    value,
    status,
    detail: `${label}${value === "" ? "" : ` · ${value}`}. ${what}.${problem}`.trim(),
    legacy,
  };
}

/** A region row from one `region.list` entry. */
export function regionRow(entry: RegionEntry): SetUpRow {
  const legacy = entry.legacy === true;
  const material = entry.material?.name ?? "";
  // `bodies` is the list of part ids the region holds, not a count; a legacy group has none.
  const bodies = entry.bodies?.length ?? 0;
  const status = rowStatus({ status: entry.status, stale: entry.stale });
  const label = legacy ? "Plain group" : "Region";
  const what = material === "" ? "no material" : material;
  const bodiesText = `${bodies} bod${bodies === 1 ? "y" : "ies"}`;
  return {
    id: entry.id,
    path: entry.path,
    name: entry.name,
    label,
    color: entry.tag?.color ?? null,
    visible: entry.tag?.visible ?? null,
    value: material === "" ? "" : material,
    status,
    detail: `${label} · ${bodiesText} · ${what}`,
    legacy,
  };
}

/**
 * Whether a row's ghost eye should show a body/tag as hidden: a proxy swatch and eye are offered
 * only for a row that actually has a tag to toggle (§9.1), so an untagged legacy group gets neither
 * rather than a control that would do nothing.
 */
export function hasTagControls(row: SetUpRow): boolean {
  return row.color !== null && row.visible !== null;
}
