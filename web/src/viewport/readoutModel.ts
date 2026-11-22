/**
 * The hover readout of the results: the value of the shown field at the point under the pointer, read from
 * the values the surface is already coloured with, and the line the tag prints.
 *
 * **Linear on the triangle**, the same rule the colours and `run.probe` use: the field is known at the
 * vertices, and the value at a point of a triangle is the weighted sum of its three. A vertex the server
 * gave no number (NaN) makes the answer "no value", never a guess.
 *
 * Import rule: no React, no store, no DOM, no three. This loads in Node.
 */
import { formatTickWith, type TickFormat } from "../panels/legendModel";

/** The value at barycentric `weights` of the triangle `vertices`, or null when a vertex has no value. */
export function valueAt(values: ArrayLike<number>, vertices: readonly [number, number, number], weights: readonly [number, number, number]): number | null {
  let sum = 0;
  for (let k = 0; k < 3; k++) {
    const i = vertices[k]!;
    if (i < 0 || i >= values.length) return null;
    const v = values[i]!;
    if (!Number.isFinite(v)) return null;
    sum += v * weights[k]!;
  }
  return Number.isFinite(sum) ? sum : null;
}

export interface Readout {
  /** "|U| 0.312 m/s": the symbol, the value in the legend's format and the units. */
  text: string;
  x: number;
  y: number;
}

/** The tag's text: the field's symbol, the value written as the legend writes its ticks, the units. */
export function readoutText(symbol: string, value: number, units: string, format: TickFormat = "auto", digits = 3): string {
  const number = formatTickWith(value, format, digits);
  return units === "" ? `${symbol} ${number}` : `${symbol} ${number} ${units}`;
}
