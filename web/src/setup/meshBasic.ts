/**
 * What the Mesh page's Basic block shows: Coarse / Medium / Fine with the cell size each one means, and
 * whether the Boundary layers switch is on. The sizes and the matching rule are the Mesh card's own
 * (`guided/sizing.ts`), so the card and the page agree on every box and every base size.
 *
 * Import rule: no React, no store, no DOM.
 */
import { FINENESS, SWITCH_LAYERS, baseSizeFor, finenessOf, lengthText, type Box, type Fineness } from "../guided/sizing";

const WORDS: Record<Fineness, string> = { coarse: "Coarse", medium: "Medium", fine: "Fine" };

export interface FinenessChoice {
  value: Fineness;
  label: string;
  /** The base size in metres this choice sets, or null while the fluid's size is not known. */
  size: number | null;
  /** The size in words ("2.5 mm"), or "". */
  length: string;
  /** This is the choice the current base size matches (to within the Mesh card's 10 %). */
  active: boolean;
}

/** The three choices for a box and the settings' base size. No choice is active for a size typed in Advanced. */
export function finenessChoices(box: Box | null, baseSize: number | null): FinenessChoice[] {
  const level = finenessOf(box, baseSize);
  return FINENESS.map((value) => {
    const size = baseSizeFor(box, value);
    return { value, label: WORDS[value], size, length: size === null ? "" : lengthText(size), active: value === level };
  });
}

/** The value the Boundary layers switch writes: the card's layer count when it is turned on, else none. */
export function layersFor(on: boolean): number {
  return on ? SWITCH_LAYERS : 0;
}
