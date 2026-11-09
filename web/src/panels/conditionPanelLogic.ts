/**
 * The value panel's pure logic (ADR-0022 §7.1, T-044): the kinds grouped by family, the name a kind
 * defaults to, and the command a commit issues.
 *
 * This is the part of the panel a reviewer can be wrong about without seeing it — the family order,
 * which kinds are choosable, what the name field starts as, and whether commit is `boundary.assign`
 * (a pick) or `boundary.set` (an existing boundary). Keeping it here means those rules are tested
 * directly, the way `assignTarget.ts` is, and `ConditionPanel.tsx` only renders.
 *
 * The ADR is specific about the presentation, and it is honoured here rather than in the DOM: the
 * kinds are a **vertical list grouped by family**, "each with a 12 px dot in the kind's colour", and
 * the last (an unavailable kind) is "disabled with its `arrives`" — the milestone that brings it.
 */

import type { ConditionKind } from "../api/types";

/** One family of the left column: its kinds, in catalogue order, and whether it can be chosen. */
export interface KindFamily {
  /** The catalogue's family id, used as the group's key. */
  id: string;
  /** The group's heading, title-cased from the family id (the catalogue has no label for it). */
  title: string;
  kinds: ConditionKind[];
}

/** The order the families appear in, matching §7.1's list: inlets, outlets, walls, constraints,
 * interfaces. A family the catalogue adds later sorts after these rather than disappearing. */
const FAMILY_ORDER = ["inlet", "outlet", "wall", "constraint", "interface"];

/** "inlet" → "Inlet", "fixed_value" → "Fixed value": a family id as a heading. */
export function familyTitle(id: string): string {
  const words = id.split(/[_-]+/).filter(Boolean);
  if (words.length === 0) return id;
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

/**
 * The catalogue as the left column: one group per family, in §7.1's order, the kinds in catalogue
 * order within each. An empty family is dropped, and an unknown family id is kept (sorted after the
 * known ones) so a new server-side family shows up instead of silently vanishing.
 */
export function groupKinds(kinds: readonly ConditionKind[]): KindFamily[] {
  const byFamily = new Map<string, ConditionKind[]>();
  for (const kind of kinds) {
    const family = kind.family || "other";
    const list = byFamily.get(family);
    if (list === undefined) byFamily.set(family, [kind]);
    else list.push(kind);
  }
  const rank = (id: string): number => {
    const at = FAMILY_ORDER.indexOf(id);
    return at < 0 ? FAMILY_ORDER.length : at;
  };
  return [...byFamily.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([id, list]) => ({ id, title: familyTitle(id), kinds: list }));
}

/**
 * The name the panel starts a kind's boundary with. §7.1: "the name (defaulting from the kind,
 * unique)". The default is the kind's id with its underscores read as spaces, which is what the
 * catalogue's own `label` gives minus the capitalisation; `taken` (the project's existing boundary
 * names) makes it unique by appending `_2`, `_3`, … — the same rule the tree uses for a repeated
 * node name, so the panel and the tree cannot disagree.
 */
export function defaultName(kind: ConditionKind, taken: readonly string[]): string {
  const base = kind.label || kind.id;
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let index = 2; ; index++) {
    const candidate = `${base}_${index}`;
    if (!used.has(candidate)) return candidate;
  }
}

/**
 * The name an assign sends, or null to leave it to the server. A node name is letters, digits and
 * underscores with no leading digit (the server refuses anything else), and the label-based default
 * ("Velocity inlet") is none of those, so a name left at its default is not sent: the server names the
 * boundary from its kind ("inlet", "outlet", "walls"), uniquely. A name the person typed is kept, with
 * spaces and dashes read as underscores and any other character dropped.
 */
export function nameToSend(typed: string, fallback: string): string | null {
  const text = typed.trim();
  if (text === "" || text === fallback) return null;
  const clean = text.replace(/[\s-]+/g, "_").replace(/[^A-Za-z0-9_]/g, "");
  if (clean === "") return null;
  return /^[0-9]/.test(clean) ? `b_${clean}` : clean;
}

/** The one command a commit issues, and what it is for. */
export interface ConditionCommit {
  method: string;
  params: Record<string, unknown>;
}

/**
 * The command that commits the panel (ADR-0022 §7.1): `boundary.assign` for a pick, `boundary.set`
 * for a boundary that already exists (chosen from the tree or the conditions list).
 *
 * The two differ in what they address: an assign is given the *pick's members* and makes whatever is
 * missing; a set is given the boundary's **path** and changes only its values (and, when the kind
 * changes, its kind). That is why this takes the target rather than a boolean: the caller knows which
 * one it is holding, and passing the wrong one would assign to the wrong thing.
 */
export function conditionCommit(target: {
  /** The boundary's node path: `boundary.set` addresses it by `boundary`, not by `target`. */
  boundary: string;
  kind: ConditionKind;
  name: string;
  values: Record<string, unknown>;
}): ConditionCommit {
  const params: Record<string, unknown> = { boundary: target.boundary, kind: target.kind.id };
  if (target.name !== "") params.name = target.name;
  if (Object.keys(target.values).length > 0) params.values = target.values;
  return { method: "boundary.set", params };
}

/**
 * The target line §7.1 asks for: what the panel applies to, and the gesture that put it there —
 * "12 faces of 3 bodies · Replace". `how` is the pick gesture, so a Shift-add reads differently from
 * a replace; a boundary chosen from the tree has no gesture and gives just its name.
 */
export function targetLine(label: string, how: "replace" | "add" | "toggle" | null): string {
  if (label === "") return how === null ? "nothing picked" : "";
  const gesture = how === "add" ? "Add" : how === "toggle" ? "Toggle" : how === "replace" ? "Replace" : "";
  return gesture === "" ? label : `${label} · ${gesture}`;
}

/**
 * Whether an Enter that reached the card may commit it. A field that refused its text itself (not
 * the server's refusal, which a retry may meet again) is marked `data-refused`, and the card would send
 * the value from before the edit, so it waits until the field is corrected. (The field also stops such an Enter itself; this is the card's own guard.)
 */
export function enterMayCommit(target: { getAttribute?: (name: string) => string | null } | null): boolean {
  return target?.getAttribute?.("data-refused") !== "true";
}
