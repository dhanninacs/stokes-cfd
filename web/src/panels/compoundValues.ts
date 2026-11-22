/**
 * The pure helpers of the compound quantity fields (`CompoundFields.tsx`) (ADR-0037): a `vector` parameter's three
 * components and a `quantity_map`'s named entries. The model stores each component as a quantity
 * of its own, under `<name>.<component>` (`position.x`, `dimensions.W`); a new operation's card
 * holds the vector as one list (`position: [0.01, "R", "2 [mm]"]`) until it is created. No React,
 * no store: the tests run in Node.
 */
import { isQuantity, type ParamValue } from "../api/types";

export const AXES = ["x", "y", "z"] as const;
export type Axis = (typeof AXES)[number];

/** A component's text as it is first shown: its expression, else its displayed number. */
export interface ComponentSeed {
  /** The expression (or quantity text) the user wrote; null for a plain number. */
  expr: string | null;
  /** The number in SI, when there is one. */
  si: number | null;
  /** The server's display string ("5 mm"), when there is one. */
  display: string | null;
}

/**
 * One component of a vector, from the node's stored components (`position.x`) or, on a card, from
 * the list the card holds (`position[0]`).
 */
export function vectorComponent(
  values: Record<string, unknown>,
  displays: Record<string, string> | undefined,
  name: string,
  axis: Axis,
): ComponentSeed {
  const key = `${name}.${axis}`;
  const stored = values[key] as ParamValue | undefined;
  if (stored !== undefined && isQuantity(stored)) {
    return { expr: stored.expr ?? null, si: stored.value, display: displays?.[key] ?? null };
  }
  const list = values[name];
  const item = Array.isArray(list) ? list[AXES.indexOf(axis)] : undefined;
  if (typeof item === "number" && Number.isFinite(item)) return { expr: null, si: item, display: null };
  if (typeof item === "string" && item.trim() !== "") return { expr: item.trim(), si: null, display: null };
  if (isQuantity(item as ParamValue)) {
    const q = item as { value: number; expr?: string | null };
    return { expr: q.expr ?? null, si: q.value, display: null };
  }
  return { expr: null, si: null, display: null };
}

/**
 * A card's list after one component changed: the other two kept as the card shows them (a
 * stored component as its expression or SI number).
 */
export function withComponent(values: Record<string, unknown>, name: string, axis: Axis, value: unknown): unknown[] {
  return AXES.map((a) => {
    if (a === axis) return value;
    const seed = vectorComponent(values, undefined, name, a);
    return seed.expr ?? seed.si ?? 0;
  });
}

/** One entry of a `quantity_map`, as a row of the dimensions table. */
export interface MapEntry {
  name: string;
  /** What the user wrote: quantity text or an expression. */
  text: string;
  /** The server's display of its value ("20 mm"), when it is stored. */
  display: string | null;
  si: number | null;
  /** The SI unit of the stored value ("m", "rad"), when it is stored. */
  unit?: string;
}

/**
 * A stored length or angle as the geometry forms write it ("40 [mm]", "30 [deg]") when the form
 * works in millimetres: the rest of the form shows mm, so a dimension typed as 40 mm must not come
 * back as "0.04 m". Null for any other unit or form.
 */
export function geometryText(si: number, unit: string, unitSystem: string | undefined): string | null {
  if (unitSystem !== "mm" || !Number.isFinite(si)) return null;
  const tidy = (v: number) => String(Number(v.toPrecision(12)));
  if (unit === "m") return `${tidy(si * 1000)} [mm]`;
  if (unit === "rad") return `${tidy((si * 180) / Math.PI)} [deg]`;
  return null;
}

/** The entries of map `name`, from the node's stored entries or the card's object, by name. */
export function mapEntries(values: Record<string, unknown>, displays: Record<string, string> | undefined, name: string, unitSystem?: string): MapEntry[] {
  const prefix = `${name}.`;
  const out: MapEntry[] = [];
  for (const [key, value] of Object.entries(values)) {
    if (!key.startsWith(prefix) || !isQuantity(value as ParamValue)) continue;
    const q = value as { value: number; unit: string; expr?: string | null };
    const display = displays?.[key] ?? null;
    out.push({ name: key.slice(prefix.length), text: q.expr ?? geometryText(q.value, q.unit, unitSystem) ?? display ?? `${q.value} [${q.unit || "1"}]`, display, si: q.value, unit: q.unit });
  }
  const held = values[name];
  if (out.length === 0 && held !== null && typeof held === "object" && !Array.isArray(held)) {
    for (const [entry, value] of Object.entries(held as Record<string, unknown>)) {
      out.push({ name: entry, text: typeof value === "string" ? value : String(value), display: null, si: typeof value === "number" ? value : null });
    }
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

/** The JSON a map is set with: each entry's text by name. */
export function mapValue(entries: readonly MapEntry[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of entries) out[e.name] = e.text;
  return out;
}

const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Why a new entry's name cannot be used, or null. */
export function entryNameRefusal(name: string, taken: readonly string[]): string | null {
  if (!NAME.test(name)) return "A name is letters, digits and underscores, not starting with a digit";
  if (taken.includes(name)) return `${name} is already a dimension`;
  return null;
}
