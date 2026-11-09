/**
 * The colour legend as a model (R1 of the results overhaul): the settings it has, the rules that turn them into
 * what is drawn, and the number formats a journal figure wants. Pure functions, so every rule is tested in
 * Node; `FieldLegend.tsx` (the bar), the Map and Range menus of the Results panel, and the consumers of the
 * colour map (`fields.ts`, `sliceLayer.ts`, `isoLayer.ts`) are thin readers of it.
 *
 * **The settings live in `results.view`** as optional fields (`ResultsView extends Partial<LegendSettings>`),
 * so one object says what the walls, the slices, the iso-surface and the legend are coloured with and they
 * cannot disagree. `legendOf` fills the defaults; the reducers here write only the fields they own.
 *
 * View state only: nothing here is a project command, nothing is journaled. Saved views (a later brief) copy
 * these fields into the project.
 *
 * Import rule: no React, no store, no DOM. Types only from other modules' state, so this loads in Node.
 */

import { DEFAULT_COLORMAP, colormapOf, isColormapId, type Colormap, type ColormapId } from "../state/colormap";
import { formatTick, isLocked, legendTicks, lockRange, pressureLabel, setLockEnd, unlockRange, type Range, type ResultsView } from "./fieldsModel";
import { formatMonitor } from "./monitorsModel";
import type { RunFields } from "./resultsApi";

/**
 * Where the range comes from. **Auto** follows the run (its 1-99 percentile range when the run gives one, else
 * min..max); **locked** is the range frozen at what it was when the user pressed lock; **manual** is typed
 * values. Locked and manual both live in `view.lock`; the mode says which one the user is in, so the menu
 * can say so and the typed values are not overwritten by a re-lock.
 */
export type RangeMode = "auto" | "locked" | "manual";

/** How a tick is written: today's three-significant-digit rule, a fixed number of decimals, scientific, or significant digits. */
export type TickFormat = "auto" | "fixed" | "scientific" | "significant";

/** Where the legend sits: the right edge of the figure, under it, or free in the viewport. */
export type LegendPlacement = "right" | "bottom" | "floating";

/** The legend's own settings, beside `field`, `lock` and `bands` which `ResultsView` already has. */
export interface LegendSettings {
  rangeMode: RangeMode;
  /** Only means something with percentiles: draw min..max although the run reported a 1-99 range. */
  fullRange: boolean;
  map: ColormapId;
  reverse: boolean;
  format: TickFormat;
  /** Decimals for `fixed` and `scientific`, significant digits for `significant`. Ignored by `auto`. */
  digits: number;
  placement: LegendPlacement;
  /** Fonts 1.5x and a thicker bar, which the image export uses. */
  printSize: boolean;
}

/** The settings with every default filled in, plus the band count. */
export interface LegendView extends LegendSettings {
  /** 0 is smooth. */
  bands: number;
}

export const DEFAULT_DIGITS = 3;
export const MIN_DIGITS = 0;
export const MAX_DIGITS = 8;

export const RANGE_MODES: readonly RangeMode[] = ["auto", "locked", "manual"];
export const TICK_FORMATS: readonly TickFormat[] = ["auto", "fixed", "scientific", "significant"];
export const PLACEMENTS: readonly LegendPlacement[] = ["right", "bottom", "floating"];

/** The words a menu shows: one or two, the sentence is the tooltip. */
export const RANGE_MODE_WORDS: Record<RangeMode, string> = { auto: "Auto", locked: "Locked", manual: "Manual" };
export const FORMAT_WORDS: Record<TickFormat, string> = { auto: "Auto", fixed: "Fixed", scientific: "Scientific", significant: "Significant" };
export const PLACEMENT_WORDS: Record<LegendPlacement, string> = { right: "Right", bottom: "Bottom", floating: "Floating" };

/** Digits clamped to what a tick can use: whole numbers 0..8 (a NaN or a missing value is the default). */
export function clampDigits(digits: number | null | undefined): number {
  if (digits === null || digits === undefined || !Number.isFinite(digits)) return DEFAULT_DIGITS;
  return Math.min(MAX_DIGITS, Math.max(MIN_DIGITS, Math.trunc(digits)));
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

/**
 * The legend's settings out of a view, every missing or nonsensical field replaced by its default.
 * **Tolerant on purpose**: the view can come from a saved workspace or a test literal, and a figure in the
 * default look is a better answer than a thrown error the user cannot act on.
 */
export function legendOf(view: Partial<ResultsView> | null | undefined): LegendView {
  const v = view ?? {};
  const bands = typeof v.bands === "number" && Number.isFinite(v.bands) && v.bands > 0 ? Math.min(Math.trunc(v.bands), 32) : 0;
  return {
    rangeMode: oneOf(v.rangeMode, RANGE_MODES, "auto"),
    fullRange: v.fullRange === true,
    map: isColormapId(v.map) ? v.map : DEFAULT_COLORMAP,
    reverse: v.reverse === true,
    format: oneOf(v.format, TICK_FORMATS, "auto"),
    digits: clampDigits(v.digits),
    placement: oneOf(v.placement, PLACEMENTS, "right"),
    printSize: v.printSize === true,
    bands,
  };
}

/** The map the view is coloured with, as the table the consumers read (`tintFor`, `bandColor`, the gradients). */
export function viewColormap(view: Partial<ResultsView> | null | undefined): Colormap {
  return colormapOf(legendOf(view).map);
}

/** The mode the user is in, as the menu shows it: no fixed range is auto; a fixed one is locked unless its ends were typed. */
export function rangeModeOf(view: ResultsView): RangeMode {
  if (!isLocked(view)) return "auto";
  return view.rangeMode === "manual" ? "manual" : "locked";
}

/**
 * The view in another range mode. **Auto** lets go of any fixed range; **locked** freezes the range as it is
 * now (a range already fixed is kept); **manual** fixes it too and marks it as typed, so the ends can be edited.
 * With no field there is nothing to fix, and the view is returned as it was.
 */
export function setRangeMode(view: ResultsView, data: RunFields | null, mode: RangeMode): ResultsView {
  if (mode === "auto") {
    const open = unlockRange(view);
    return open.rangeMode === "auto" && open === view ? view : { ...open, rangeMode: "auto" };
  }
  const fixed = isLocked(view) ? view : lockRange(view, data);
  if (!isLocked(fixed)) return view;
  return fixed.rangeMode === mode ? fixed : { ...fixed, rangeMode: mode };
}

/** What the Results panel's range menu offers: the three modes, and "full" (auto without the percentile trim). */
export type RangeChoice = RangeMode | "full";

/**
 * The view after a pick in the range menu.
 *
 * **Lock first, then clear the flag** (review of R1): Locked and Manual freeze the range ON SCREEN, and with
 * "Full range" on that is min..max. Clearing `fullRange` before locking froze the 1-99 % range instead, so the
 * colours jumped the moment the user pressed Locked. Auto then Full range: the flag is the only thing that
 * differs from plain auto.
 */
export function chooseRange(view: ResultsView, data: RunFields | null, choice: RangeChoice): ResultsView {
  if (choice === "full") return setFullRange(setRangeMode(view, data, "auto"), true);
  return setFullRange(setRangeMode(view, data, choice), false);
}

/** One end of the range typed by the user: fixes the range at that end and marks it manual. A value that is not a number changes nothing. */
export function setManualEnd(view: ResultsView, data: RunFields | null, end: "min" | "max", value: number | null): ResultsView {
  const next = setLockEnd(view, data, end, value);
  return next === view ? view : { ...next, rangeMode: "manual" };
}

/** The view with "full range" on or off (only a run with percentiles can tell the two apart). */
export function setFullRange(view: ResultsView, full: boolean): ResultsView {
  return (view.fullRange === true) === full ? view : { ...view, fullRange: full };
}

export function setMap(view: ResultsView, map: ColormapId): ResultsView {
  return isColormapId(map) && legendOf(view).map !== map ? { ...view, map } : view;
}

export function setReverse(view: ResultsView, reverse: boolean): ResultsView {
  return legendOf(view).reverse === reverse ? view : { ...view, reverse };
}

export function setFormat(view: ResultsView, format: TickFormat, digits?: number): ResultsView {
  const next = { ...view, format: oneOf(format, TICK_FORMATS, "auto"), digits: digits === undefined ? view.digits : clampDigits(digits) };
  return next.format === view.format && next.digits === view.digits ? view : next;
}

export function setPlacement(view: ResultsView, placement: LegendPlacement): ResultsView {
  return legendOf(view).placement === placement ? view : { ...view, placement: oneOf(placement, PLACEMENTS, "right") };
}

export function setPrintSize(view: ResultsView, on: boolean): ResultsView {
  return legendOf(view).printSize === on ? view : { ...view, printSize: on };
}

// ---------------------------------------------------------------------------------------------- number formats
/** `1.2e-4` from a `toExponential` string: mantissa, then `e`, then the exponent as a plain integer (no `+`, no leading zero). */
function exponentForm(text: string): string {
  const [mantissa, exponent] = text.split("e");
  return `${mantissa}e${Number(exponent)}`;
}

/** A fixed-decimals string without a negative zero: `-0.00` is `0.00`, which a reader would otherwise take for a small negative value. */
function noNegativeZero(text: string): string {
  return /^-0(\.0+)?$/.test(text) ? text.slice(1) : text;
}

/**
 * A number for a tick in the chosen format.
 *
 * - **auto**: today's `formatTick` (three significant digits, `e` notation beyond 1e4 and below 1e-3);
 * - **fixed**: `digits` decimals, always, so a column of ticks lines up (`0.50`, `1.00`);
 * - **scientific**: one digit, `digits` decimals, an exponent (`1.25e-4`);
 * - **significant**: `digits` significant digits, trailing zeros kept (`1.20`), in exponent form only when the
 *   magnitude calls for it.
 *
 * A value that is not a number is "-" in every format, as in `formatTick`.
 */
export function formatTickWith(value: number, format: TickFormat = "auto", digits: number = DEFAULT_DIGITS): string {
  if (format === "auto") return formatTick(value);
  if (!Number.isFinite(value)) return "-";
  const d = clampDigits(digits);
  if (format === "fixed") return noNegativeZero(value.toFixed(d));
  if (format === "scientific") return value === 0 ? (0).toFixed(d) + "e0" : exponentForm(value.toExponential(d));
  // significant: toPrecision needs 1..100 digits.
  const sig = Math.max(1, d);
  if (value === 0) return sig > 1 ? (0).toFixed(sig - 1) : "0";
  const precise = value.toPrecision(sig);
  return precise.includes("e") ? exponentForm(precise.replace("e+", "e")) : precise;
}

/** The ticks of a legend, highest first (as the bar is read), already formatted. */
export function legendLabels(range: Range, view: Partial<ResultsView> | null | undefined, n = 5): string[] {
  const { format, digits } = legendOf(view);
  return legendTicks(range.min, range.max, n).reverse().map((v) => formatTickWith(v, format, digits));
}

// ---------------------------------------------------------------------------------------------- the drop readout
/** What the legend prints for the pressure drop. */
export interface DropReadout {
  /** `Δp` for a pressure, `Δp/ρ` for a kinematic one whose density is not known. */
  symbol: "Δp" | "Δp/ρ";
  /** The number, formatted (`monitorsModel.formatMonitor`). */
  text: string;
  /** The unit of `text`: `Pa`, or the kinematic unit when it could not be made a pressure. */
  units: string;
  /** What was converted, for the tooltip: the kinematic value and the density. "" when nothing was. */
  detail: string;
}

/**
 * The drop as the legend shows it.
 *
 * - A solver that reports a pressure (`units` is `Pa`, the energy engine) is read as it is: `Δp`.
 * - An incompressible solver reports `p/ρ` (m2/s2). **With the material's density the drop is shown in Pa**,
 *   `Δp = ρ · Δp/ρ`, and the kinematic value stays in `detail`, so nothing is hidden. Without a density the
 *   kinematic value is kept and says so with its symbol: a pressure is never made up from a guessed density.
 * - A density that is not a positive number is no density.
 */
export function dropReadout(dropValueKinematic: number, units: string, density: number | null): DropReadout {
  const kinematic = pressureLabel(units) === "p/rho";
  const unit = units === "" ? "m2/s2" : units;
  if (!kinematic) return { symbol: "Δp", text: formatMonitor(dropValueKinematic), units: unit, detail: "" };
  if (density === null || !Number.isFinite(density) || !(density > 0)) {
    return { symbol: "Δp/ρ", text: formatMonitor(dropValueKinematic), units: unit, detail: "" };
  }
  return {
    symbol: "Δp",
    text: formatMonitor(dropValueKinematic * density),
    units: "Pa",
    detail: `Δp/ρ = ${formatMonitor(dropValueKinematic)} ${unit}, ρ = ${formatMonitor(density)} kg/m3`,
  };
}
