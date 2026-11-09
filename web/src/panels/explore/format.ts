/**
 * Showing SI numbers in the project's display units, and the one-line texts of the Explore panels.
 *
 * A design table carries SI numbers; each column names its SI unit and the project's display unit
 * (`display_unit`). The conversion is `display = si * factor + offset`, and the factors come from the
 * `units.convert` command, which owns the unit registry (`stokes.units`, pint); the temperatures carry
 * the offset. `loadConversions` fetches each pair once and `rememberConversion` stores it. A pair that
 * has not been fetched yet is never guessed: the number is shown in its SI unit, with the SI unit's
 * name, so a label can be wrong about nothing.
 */
import type { UnitsSchema } from "../../api/types";
import { formatNumber, prettyUnit } from "../../util/units";
import type { Column, StudyCounts } from "./types";

/** `"<si unit>|<display unit>"` to `[factor, offset]`, filled from `units.convert`. */
const CONVERSIONS = new Map<string, readonly [number, number]>();

/** The cache key of one conversion. */
export function conversionKey(siUnit: string, displayUnit: string): string {
  return `${siUnit}|${displayUnit}`;
}

/** Stores a conversion the server answered, so `converterFor` can use it without asking again. */
export function rememberConversion(siUnit: string, displayUnit: string, factor: number, offset: number): void {
  CONVERSIONS.set(conversionKey(siUnit, displayUnit), [factor, offset]);
}

/** True when `converterFor` can convert this pair now; an identity or empty pair always can. */
export function hasConversion(siUnit: string, displayUnit: string): boolean {
  if (!displayUnit || displayUnit === siUnit) return true;
  return CONVERSIONS.has(conversionKey(siUnit, displayUnit));
}

/** Converts a column's SI numbers to what the table shows. */
export interface Converter {
  /** The unit the converted numbers are in, as text for people ("m³/s"). */
  unit: string;
  /** False when the display unit is not known here and the numbers stay in SI. */
  converted: boolean;
  toDisplay(si: number): number;
}

const identity = (si: number): number => si;

/** The converter between an SI unit and a display unit (both as the server writes them). */
export function converterFor(siUnit: string, displayUnit: string): Converter {
  if (!displayUnit || displayUnit === siUnit) return { unit: prettyUnit(siUnit), converted: true, toDisplay: identity };
  const entry = CONVERSIONS.get(conversionKey(siUnit, displayUnit));
  if (!entry) return { unit: prettyUnit(siUnit), converted: false, toDisplay: identity };
  const [factor, offset] = entry;
  return { unit: prettyUnit(displayUnit), converted: true, toDisplay: (si) => si * factor + offset };
}

export function columnConverter(column: Column): Converter {
  return converterFor(column.unit, column.displayUnit);
}

/** The display unit of a dimension in a unit system, as the server spells it ("L/min"); the SI unit when the schema is not loaded. */
export function displayUnitOf(units: UnitsSchema | null, system: string, dimension: string): string {
  const entry = units?.dimensions[dimension];
  return entry ? (entry.display[system] ?? entry.si_unit) : "";
}

/** What a column is called in headers, axes and menus: the response's label, else its name. */
export function columnTitle(column: Column): string {
  return column.label || column.name;
}

/** A number as a table cell shows it: five significant digits, no negative zero. */
export function formatCell(value: number): string {
  if (!Number.isFinite(value)) return "";
  const text = formatNumber(value, 5);
  return text === "-0" ? "0" : text;
}

/** A tick on a plot axis: four significant digits. */
export function formatTick(value: number): string {
  if (!Number.isFinite(value)) return "";
  const text = formatNumber(value, 4);
  return text === "-0" ? "0" : text;
}

/** `text` with its unit: "1996 Pa", or "7" alone when the quantity has none. */
export function withUnit(text: string, unit: string): string {
  return unit ? `${text} ${unit}` : text;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/**
 * The status line of the design table: "12 points · 9 done · 1 failed". A state with no points is left
 * out, so a finished table reads "3 points · 3 done" and a fresh one "12 points · 12 pending".
 */
export function statusText(counts: StudyCounts, total: number): string {
  const parts = [plural(total, "point")];
  if (counts.done) parts.push(`${counts.done.toLocaleString()} done`);
  if (counts.failed) parts.push(`${counts.failed.toLocaleString()} failed`);
  if (counts.running) parts.push(`${counts.running.toLocaleString()} running`);
  if (counts.pending) parts.push(`${counts.pending.toLocaleString()} pending`);
  return parts.join(" · ");
}
