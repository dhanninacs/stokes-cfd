/**
 * How to show a material property's value, when the method may compute it.
 *
 * **The owner, via Claude (verdict leftover 3)**: *"a zero that means 'not evaluated' must not be
 * shown as a value"*. Measured on the demo's air: `density` uses `ideal_gas` and stores
 * `value: 0.0`, so the page read **"density 0 kg/m³"** — a real density for a gas is around 1.2, and
 * zero is not a placeholder a reader can tell from a measurement.
 *
 * **`constant` is the only method whose value IS the answer.** Every other method computes it from
 * other parameters — pressure and temperature for a gas, a table for a real fluid — so the stored
 * number is what the model holds for a method it is not currently using, and showing it would be
 * showing a stale field as a fact.
 */
export const COMPUTED = "computed";

/** The methods that compute their value rather than storing it. Derived above from the schema. */
export function isComputed(method: unknown): boolean {
  return typeof method === "string" && method !== "" && method !== "constant";
}

/**
 * What to print for a property: the value when the method stores one, otherwise a word.
 *
 * **Called with the value's text already formatted**, so this does not know about units — the caller
 * has the quantity and its dimension, and this only decides whether a number is the answer.
 */
export function propertyText(method: unknown, formatted: string): string {
  return isComputed(method) ? "—" : formatted;
}

/**
 * A one-line explanation of the dash, for a tooltip: *why* there is no number.
 *
 * **A bare dash is a puzzle**; "computed from pressure and temperature" is an answer. The method's own
 * name is used rather than a table of sentences, because a name a reader can match to the field above
 * beats a sentence this file would have to keep up to date with the schema.
 */
export function propertyWhy(method: unknown): string {
  if (!isComputed(method)) return "";
  const named = String(method).replaceAll("_", " ");
  return `Computed by the ${named} model, not stored as a value`;
}
