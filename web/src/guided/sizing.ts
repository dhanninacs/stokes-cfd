/**
 * The Mesh card's size control: Coarse, Medium, Fine as a base cell size taken from the model's box.
 *
 * The card says how fine the mesh is in words and shows the length it means: a twentieth, a fortieth
 * or an eightieth of the smallest side of the model's bounding box. The length is rounded to two
 * significant figures so the number on the card is the one the settings hold.
 *
 * Import rule: no React, no store, no DOM.
 */

export type Fineness = "coarse" | "medium" | "fine";

export const FINENESS: readonly Fineness[] = ["coarse", "medium", "fine"];

/** How many cells span the smallest side of the box. */
export const DIVISORS: Record<Fineness, number> = { coarse: 20, medium: 40, fine: 80 };

export interface Box {
  min: readonly [number, number, number];
  max: readonly [number, number, number];
}

/** The smallest side of a box that has one, in metres, or null for an empty or flat box. */
export function smallestSide(box: Box | null): number | null {
  if (box === null) return null;
  const sides = [0, 1, 2].map((i) => box.max[i] - box.min[i]).filter((s) => Number.isFinite(s) && s > 0);
  return sides.length === 0 ? null : Math.min(...sides);
}

function twoFigures(value: number): number {
  return Number(value.toPrecision(2));
}

/** The base size for a fineness, or null when the box says nothing. */
export function baseSizeFor(box: Box | null, fineness: Fineness): number | null {
  const side = smallestSide(box);
  return side === null ? null : twoFigures(side / DIVISORS[fineness]);
}

/** How far a base size may be from a preset's length and still count as that preset. */
const MATCH = 0.1;

/**
 * The fineness a base size is, to within 10 %, or null when it matches none of the three (a size typed in
 * the settings): the control then shows no choice rather than a wrong one. The nearest preset wins.
 */
export function finenessOf(box: Box | null, baseSize: number | null): Fineness | null {
  if (baseSize === null) return null;
  let best: Fineness | null = null;
  let gap = Infinity;
  for (const level of FINENESS) {
    const size = baseSizeFor(box, level);
    if (size === null) continue;
    const off = Math.abs(size - baseSize);
    if (off <= size * MATCH && off < gap) {
      best = level;
      gap = off;
    }
  }
  return best;
}

/** A length in metres as words: "2.5 mm", "1.2 cm", "0.8 m". */
export function lengthText(metres: number): string {
  if (!(metres > 0)) return "";
  if (metres < 0.01) return `${Number((metres * 1000).toPrecision(2))} mm`;
  if (metres < 1) return `${Number((metres * 100).toPrecision(2))} cm`;
  return `${Number(metres.toPrecision(2))} m`;
}

/** Layers the Boundary layers switch asks for on every wall when it is turned on. */
export const SWITCH_LAYERS = 3;

// ---------------------------------------------------------------------------------------------- the slider

/** The slider's ends, in cells across the smallest side: twice as coarse as Coarse to twice as fine as Fine. */
export const SLIDER_MIN_DIVISOR = 10;
export const SLIDER_MAX_DIVISOR = 160;

const SPAN = Math.log2(SLIDER_MAX_DIVISOR / SLIDER_MIN_DIVISOR);

/** Where a number of cells across the side sits on the slider, 0 to 1, on a log scale so each preset is a quarter apart. */
export function positionOfDivisor(divisor: number): number {
  const clamped = Math.min(SLIDER_MAX_DIVISOR, Math.max(SLIDER_MIN_DIVISOR, divisor));
  return Math.log2(clamped / SLIDER_MIN_DIVISOR) / SPAN;
}

/** The slider position of Coarse, Medium and Fine: where the three marks are drawn. */
export const MARKS: Record<Fineness, number> = {
  coarse: positionOfDivisor(DIVISORS.coarse),
  medium: positionOfDivisor(DIVISORS.medium),
  fine: positionOfDivisor(DIVISORS.fine),
};

/** The slider position a base size is at, or null when the box says nothing or the size is not positive. */
export function positionOf(box: Box | null, baseSize: number | null): number | null {
  const side = smallestSide(box);
  if (side === null || baseSize === null || !(baseSize > 0)) return null;
  return positionOfDivisor(side / baseSize);
}

/** The base size at a slider position (two significant figures), or null when the box says nothing. */
export function baseSizeAt(box: Box | null, position: number): number | null {
  const side = smallestSide(box);
  if (side === null) return null;
  const t = Math.min(1, Math.max(0, position));
  const divisor = SLIDER_MIN_DIVISOR * 2 ** (t * SPAN);
  return twoFigures(side / divisor);
}

/**
 * A cell size typed by the user, in metres, or null when it is not one. A bare number is millimetres (the
 * card shows mm); `mm`, `cm` and `m` are understood. The size must be positive and give between
 * `SLIDER_MIN_DIVISOR / 2` and `SLIDER_MAX_DIVISOR * 2` cells across the side when a box is known, so a typo of
 * a hundred times too small cannot start a mesh of a billion cells.
 */
export function parseCellSize(text: string, box: Box | null): number | null {
  const match = /^\s*([0-9]*\.?[0-9]+(?:e[-+]?[0-9]+)?)\s*(mm|cm|m)?\s*$/i.exec(text);
  if (match === null) return null;
  const unit = (match[2] ?? "mm").toLowerCase();
  const metres = Number(match[1]) * (unit === "m" ? 1 : unit === "cm" ? 0.01 : 0.001);
  if (!(metres > 0) || !Number.isFinite(metres)) return null;
  const side = smallestSide(box);
  if (side !== null) {
    const cells = side / metres;
    if (cells < SLIDER_MIN_DIVISOR / 2 || cells > SLIDER_MAX_DIVISOR * 2) return null;
  }
  return Number(metres.toPrecision(2));
}
