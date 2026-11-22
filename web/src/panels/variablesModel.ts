/**
 * The variables a results view can show, as the field picker and the legend use them: the server's
 * catalogue (`results.variables`: every stored field, the built-in derived quantities and the user's
 * functions, each with a group, label, symbol, units and meaning), grouped, searched, and written for
 * a person ("Velocity magnitude", "m²/s²").
 *
 * Nothing here computes physics: every number and every meaning is the server's.
 *
 * Import rule: no React, no store, no DOM. This loads in Node.
 */

export interface VariableInfo {
  name: string;
  label: string;
  symbol: string;
  group: string;
  units: string;
  description: string;
  source: "run" | "derived" | "function";
  available: boolean;
  why?: string;
}

export interface VariablesReply {
  run: string;
  kinematic: boolean;
  density: number | null;
  references: { U_ref: number; p_ref: number } | null;
  variables: VariableInfo[];
}

/** One group of the picker: its name and its variables, in the server's order. */
export interface VariableGroup {
  group: string;
  items: VariableInfo[];
}

/** A reply as the picker can trust it: rows without a name are dropped, missing text is empty. */
export function readVariables(payload: unknown): VariablesReply | null {
  if (typeof payload !== "object" || payload === null) return null;
  const p = payload as Record<string, unknown>;
  if (typeof p.run !== "string" || !Array.isArray(p.variables)) return null;
  const text = (v: unknown): string => (typeof v === "string" ? v : "");
  const variables: VariableInfo[] = [];
  for (const raw of p.variables) {
    if (typeof raw !== "object" || raw === null) continue;
    const v = raw as Record<string, unknown>;
    const name = text(v.name);
    if (name === "") continue;
    const source = v.source === "derived" || v.source === "function" ? v.source : "run";
    variables.push({
      name,
      label: text(v.label) || name,
      symbol: text(v.symbol) || name,
      group: text(v.group) || "Other",
      units: text(v.units),
      description: text(v.description),
      source,
      available: v.available !== false,
      ...(typeof v.why === "string" && v.why !== "" ? { why: v.why } : {}),
    });
  }
  const refs = p.references as Record<string, unknown> | null | undefined;
  return {
    run: p.run,
    kinematic: p.kinematic !== false,
    density: typeof p.density === "number" ? p.density : null,
    references: refs && typeof refs.U_ref === "number" && typeof refs.p_ref === "number" ? { U_ref: refs.U_ref, p_ref: refs.p_ref } : null,
    variables,
  };
}

/** The variables in their groups, in the order the server gave; empty groups are left out. */
export function groupVariables(variables: readonly VariableInfo[]): VariableGroup[] {
  const out: VariableGroup[] = [];
  for (const v of variables) {
    const last = out.find((g) => g.group === v.group);
    if (last === undefined) out.push({ group: v.group, items: [v] });
    else last.items.push(v);
  }
  return out;
}

/** Whether a variable matches what was typed: every word somewhere in its name, label, symbol or group. */
export function matches(v: VariableInfo, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const hay = `${v.name} ${v.label} ${v.symbol} ${v.group} ${v.description}`.toLowerCase();
  return words.every((w) => hay.includes(w));
}

/** The groups after a search; a group none of whose variables match is left out. */
export function searchVariables(variables: readonly VariableInfo[], query: string): VariableGroup[] {
  return groupVariables(variables.filter((v) => matches(v, query)));
}

const SUPERSCRIPT: Record<string, string> = { "-": "⁻", "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹" };

/**
 * A unit as it is printed for a person: OpenFOAM's `m2/s2` is `m²/s²`, `kg/m3` is `kg/m³`, `1/s` stays.
 * A power is a digit run straight after a letter; a number on its own (the 1 of `1/s`) is not a power.
 */
export function prettyUnits(units: string): string {
  return units.replace(/([A-Za-z])(-?\d+(?:\.\d+)?)/g, (_m, letter: string, power: string) => (/\./.test(power) ? `${letter}^${power}` : letter + [...power].map((c) => SUPERSCRIPT[c] ?? c).join("")));
}

/** The variable of a name in a catalogue, or null. */
export function variableOf(variables: readonly VariableInfo[] | null, name: string): VariableInfo | null {
  return variables?.find((v) => v.name === name) ?? null;
}

/**
 * What the legend and the picker's button print for a field: its label and symbol, then its units with
 * raised powers - "Velocity magnitude |U| (m/s)". A field the catalogue does not know prints its name.
 */
export function variableTitle(v: VariableInfo | null, name: string, units: string): string {
  const shown = prettyUnits(v?.units || units);
  // The symbol is added where the label does not already say it: "Velocity magnitude |U|", "Pressure coefficient Cp".
  const head = v === null ? name : v.symbol !== "" && !v.label.includes(v.symbol) ? `${v.label} ${v.symbol}` : v.label;
  return shown === "" ? head : `${head} (${shown})`;
}
