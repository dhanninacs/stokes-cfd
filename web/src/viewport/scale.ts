/**
 * The scale bar: how long the model is at the size it is drawn.
 *
 * A viewport with no scale is a viewport you cannot trust: a CFD model that is 20 mm long and one
 * that is 20 m long look the same until something is tagged, the ground grid is the only other cue,
 * and a grid's own spacing says nothing about size once you stop counting lines. The bar is the
 * answer to "is this part the size I think it is".
 *
 * It is a 2D overlay, not a 3D object, so it never rotates, never clips and costs one DOM node. The
 * length it reports is picked from the 1-2-5 series (`niceSpacing` in `grid.ts`) so the number is
 * always one a person reads at a glance: 1, 2, 5, 10, 20, 50 …
 *
 * **Honesty about perspective.** Under a perspective camera there is no single scale for the whole
 * image: a metre at the far edge of the floor is fewer pixels than a metre at the target. The bar is
 * therefore measured AT THE ORBIT TARGET, which is what the user is looking at, and it says so
 * (`perspective: true`) rather than implying it holds everywhere. Under an orthographic camera the
 * scale is uniform and the figure is exact.
 */

/** A bar ready to draw: the length it stands for, and how wide that is on screen. */
export interface ScaleBar {
  /** The length in SI metres that the bar represents. */
  metres: number;
  /** How wide to draw the bar, in CSS pixels. */
  pixels: number;
  /** The bar's length, already rounded and formatted for a label ("10 mm", "2 m"). */
  label: string;
  /** True when taken at the orbit target because the camera is perspective. */
  perspective: boolean;
}

/** The longest bar that still fits the corner it lives in, before rounding down. */
export const TARGET_PIXELS = 96;

/**
 * The unit ladder, longest first. No centimetre: a CAD viewport counts in millimetres and metres,
 * and a bar that reads "1 cm" where the user's own model is in millimetres is a unit change they did
 * not ask for. Every step is 1000, so the chosen number is always between 1 and 1000.
 */
const UNITS: { metres: number; suffix: string }[] = [
  { metres: 1000, suffix: "km" },
  { metres: 1, suffix: "m" },
  { metres: 0.001, suffix: "mm" },
  { metres: 1e-6, suffix: "µm" },
];

/**
 * `metres` in the unit that keeps the number between 1 and 1000, to at most three significant
 * figures, with the unit spelled out. Trailing zeros go: "10 mm", not "10.0 mm".
 */
export function formatLength(metres: number): string {
  if (!(metres > 0) || !Number.isFinite(metres)) return "—";
  const unit = UNITS.find((u) => metres >= u.metres) ?? UNITS[UNITS.length - 1];
  const value = metres / unit.metres;
  // Three significant figures, then trim only the zeros BEHIND a decimal point: a value of 500 is
  // 500, and a blanket trim of trailing zeros would turn it into 5, a hundredfold lie on the label.
  //
  // `toPrecision` switches to EXPONENTIAL notation below 1e-6, and the ladder's smallest unit is the
  // micrometre, so a length under a micrometre reached the user as "1.92e-1 µm" — a number nobody
  // reads on a dimension, and one that hid the real defect behind it (it looked like a formatting
  // wart rather than the wrong measurement it was). `toFixed` has no such switch: the digits are
  // expanded, and the trailing-zero trim below leaves "0.192 µm".
  const fixed = expandPrecision(value, 3);
  const text = fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
  return `${text} ${unit.suffix}`;
}

/**
 * Three significant figures as a **plain decimal** string, whatever the magnitude.
 *
 * `value.toPrecision(3)` is the obvious call and the wrong one here: for `|value| < 1e-6` it returns
 * exponential notation (`1.92e-7`), which no dimension label should carry. This scales the value into
 * `[1, 1000)` and rounds it there, so the digits are always expanded.
 */
function expandPrecision(value: number, digits: number): string {
  if (value === 0) return "0";
  const magnitude = Math.floor(Math.log10(Math.abs(value)));
  const places = Math.max(0, digits - 1 - magnitude);
  // `toFixed` caps at 100 decimal places; a value that small is below any unit the ladder names, and
  // zero in three significant figures is the honest answer rather than a string of leading zeros.
  if (places > 100) return "0";
  return value.toFixed(places);
}

/**
 * The bar for a view where one CSS pixel spans `metresPerPixel` metres, as long as possible
 * without passing `targetPixels`. The length is snapped to the 1-2-5 series and the pixel width
 * follows it, so the bar is always a round number of millimetres rather than a round number of
 * pixels.
 */
export function scaleBar(metresPerPixel: number, perspective: boolean, targetPixels = TARGET_PIXELS): ScaleBar | null {
  if (!(metresPerPixel > 0) || !Number.isFinite(metresPerPixel)) return null;
  const wanted = niceLength(metresPerPixel * targetPixels);
  if (!(wanted > 0)) return null;
  return {
    metres: wanted,
    pixels: wanted / metresPerPixel,
    label: formatLength(wanted),
    perspective,
  };
}

/** The 1-2-5 value at or just below `target`, so the bar never overruns its corner. */
function niceLength(target: number): number {
  const exponent = Math.floor(Math.log10(target));
  const base = Math.pow(10, exponent);
  const fraction = target / base;
  const step = fraction >= 5 ? 5 : fraction >= 2 ? 2 : 1;
  return step * base;
}

/**
 * The area ladder (square metres), longest first, and the volume ladder (cubic metres).
 *
 * Each step is a factor of a thousand in the *quantity*, not in the length: `mm² -> m²` is 1e-6, so
 * reusing the length ladder here would report a square metre as a million square millimetres. The
 * spelling follows the length ladder's (`mm²`, `µm²`), which is what an engineer reads.
 */
const AREA_UNITS: { squareMetres: number; suffix: string }[] = [
  { squareMetres: 1e6, suffix: "km²" },
  { squareMetres: 1, suffix: "m²" },
  { squareMetres: 1e-6, suffix: "mm²" },
  { squareMetres: 1e-12, suffix: "µm²" },
];

const VOLUME_UNITS: { cubicMetres: number; suffix: string }[] = [
  { cubicMetres: 1e9, suffix: "km³" },
  { cubicMetres: 1, suffix: "m³" },
  { cubicMetres: 1e-9, suffix: "mm³" },
  { cubicMetres: 1e-18, suffix: "µm³" },
];

/** `squareMetres` in the unit that keeps the number between 1 and 1000, e.g. `900 mm²`. */
export function formatArea(squareMetres: number): string {
  return formatInLadder(squareMetres, AREA_UNITS, (unit) => unit.squareMetres);
}

/** `cubicMetres` in the unit that keeps the number between 1 and 1000, e.g. `27 mm³`. */
export function formatVolume(cubicMetres: number): string {
  return formatInLadder(cubicMetres, VOLUME_UNITS, (unit) => unit.cubicMetres);
}

/**
 * The shared body of the three ladders: pick the unit that keeps the value in [1, 1000), print three
 * significant figures as a plain decimal, and trim trailing zeros behind the point.
 *
 * One implementation for all three because the rule is the same and the units differ only in their
 * scale — three copies would drift, and the `toPrecision` trap that produced "1.92e-1 µm" is avoided
 * in exactly one place (`expandPrecision`) rather than three.
 */
function formatInLadder<T>(
  value: number,
  units: readonly T[],
  scaleOf: (unit: T) => number,
): string {
  if (!(value > 0) || !Number.isFinite(value)) return "—";
  const found = units.find((unit) => value >= scaleOf(unit));
  const unit = found ?? units[units.length - 1];
  const scaled = value / scaleOf(unit);
  const fixed = expandPrecision(scaled, 3);
  const text = fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
  // The suffix is carried on the unit object; every ladder's entries have one, so this reads it
  // without a second parameter that could disagree with the array.
  return `${text} ${(unit as { suffix: string }).suffix}`;
}
