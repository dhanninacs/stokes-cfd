/**
 * The colour maps of the results (T-218, R1): a registry of sequential and diverging maps, each a table of
 * sRGB stops. They are DATA palettes, like the selection palette: the one place in the interface where
 * colours are not theme tokens, because a field's colours mean values and must not change with the theme.
 * No dependency, no texture: the tint is an RGBA8 value per vertex and the legend is a CSS gradient built
 * from the same table, so the legend, the walls, the slice and the iso-surface can never be different maps.
 *
 * `viridis` is the default and the one that has always shipped: 32 samples of the polynomial fit of viridis by
 * M. Zucker (public domain), rounded to bytes; the ends land within 4 of 255 of viridis's own (68, 1, 84) and
 * (253, 231, 37). It is also exported under its old name `COLORMAP`, with `colorAt`, `gradientCss` and
 * `steppedGradientCss` meaning "the viridis one", so every reader written before the registry keeps working.
 * `magma`, `turbo` and `coolwarm` are short samples of the published maps (matplotlib / Google / Moreland), a
 * few more stops than the eye can tell from the originals when interpolated linearly; `greys` is ColorBrewer's.
 * `aurora`, `ember`, `ocean` and `iceburn` are the bespoke maps of the old Stokes viewer (stops only).
 *
 * **A DIVERGING map is centred on zero when the range spans it** (`fractionIn`): a pressure of -20..+80 Pa
 * must not paint 0 Pa in the cool half just because the map's middle is at 30. The three-way split
 * min..0..max onto 0..0.5..1 keeps the neutral colour on the value that means "no difference", at the price
 * of a different colour-per-unit on each side - which the legend bar shows, because it is built the same way.
 *
 * Import rule: no React, no store, no DOM. This loads in Node.
 */

export type Rgb = readonly [number, number, number];

export type ColormapId = "viridis" | "magma" | "coolwarm" | "turbo" | "greys" | "aurora" | "ember" | "ocean" | "iceburn";

export interface Colormap {
  id: ColormapId;
  /** One or two words, for a menu. */
  label: string;
  /** sRGB bytes, low to high, evenly spaced along the map. At least 5. */
  stops: readonly Rgb[];
  /** Has a neutral middle; centred on zero when a range spans it. */
  diverging: boolean;
}

/** 32 sRGB stops, low to high, viridis-like. */
export const COLORMAP: readonly Rgb[] = [
  [71, 1, 85],
  [72, 13, 96],
  [72, 24, 107],
  [72, 36, 116],
  [71, 46, 124],
  [69, 56, 130],
  [67, 66, 135],
  [64, 76, 138],
  [60, 84, 140],
  [56, 93, 141],
  [51, 101, 142],
  [46, 109, 142],
  [42, 117, 142],
  [37, 125, 141],
  [34, 133, 141],
  [32, 140, 140],
  [31, 148, 138],
  [32, 156, 136],
  [34, 163, 134],
  [39, 171, 130],
  [47, 178, 125],
  [57, 185, 118],
  [70, 192, 110],
  [86, 199, 100],
  [104, 205, 89],
  [124, 210, 76],
  [146, 215, 62],
  [169, 219, 49],
  [192, 223, 38],
  [215, 226, 30],
  [235, 229, 27],
  [252, 231, 33],
];

/** `#rrggbb` stops as bytes: the tables below are written the way their sources publish them. */
function hexStops(hex: readonly string[]): Rgb[] {
  return hex.map((h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)] as const);
}

/** The registry, in the order a menu lists it: the default first, then the other sequential maps, then the diverging ones. */
export const COLORMAPS: Record<ColormapId, Colormap> = {
  viridis: { id: "viridis", label: "Viridis", stops: COLORMAP, diverging: false },
  magma: {
    id: "magma",
    label: "Magma",
    stops: hexStops(["#000004", "#180f3d", "#440f76", "#721f81", "#9e2f7f", "#cd4071", "#f1605d", "#fd9567", "#fec98d", "#fcfdbf"]),
    diverging: false,
  },
  turbo: {
    id: "turbo",
    label: "Turbo",
    stops: hexStops([
      "#30123b", "#4145ab", "#4675ed", "#39a2fc", "#1bcfd4", "#24eca6", "#61fc6c", "#a4fc3b",
      "#d1e834", "#f3c63a", "#fe9b2d", "#f36315", "#d93806", "#b11901", "#7a0403",
    ]),
    diverging: false,
  },
  greys: {
    id: "greys",
    label: "Greys",
    // ColorBrewer Greys, light to dark, without the pure white and pure black ends: a white low value vanishes
    // on a white figure and a black high value on the dark viewport.
    stops: hexStops(["#f0f0f0", "#d9d9d9", "#bdbdbd", "#969696", "#737373", "#525252", "#252525"]),
    diverging: false,
  },
  aurora: { id: "aurora", label: "Aurora", stops: hexStops(["#0b0d3a", "#16357f", "#0f8aa6", "#3ddc97", "#e8ffb0"]), diverging: false },
  ember: { id: "ember", label: "Ember", stops: hexStops(["#0b0b15", "#3b0f6b", "#9b1d64", "#e8473f", "#f99e1c", "#fce96b"]), diverging: false },
  ocean: { id: "ocean", label: "Ocean", stops: hexStops(["#04203f", "#0a5a8a", "#16a0c0", "#7fdbe6", "#eafcff"]), diverging: false },
  coolwarm: {
    id: "coolwarm",
    label: "Cool-warm",
    // Moreland's cool-warm: blue, a light-grey middle, red.
    stops: hexStops(["#3b4cc0", "#5977e3", "#7b9ff9", "#9ebeff", "#c0d4f5", "#dddcdc", "#f2cbb7", "#f7ac8e", "#ee8468", "#d65244", "#b40426"]),
    diverging: true,
  },
  iceburn: { id: "iceburn", label: "Ice-burn", stops: hexStops(["#1b4965", "#62b6cb", "#f1faee", "#ffb703", "#e63946"]), diverging: true },
};

/** Every id, in menu order. */
export const COLORMAP_IDS: readonly ColormapId[] = ["viridis", "magma", "turbo", "greys", "aurora", "ember", "ocean", "coolwarm", "iceburn"];

export const DEFAULT_COLORMAP: ColormapId = "viridis";

const VIRIDIS = COLORMAPS.viridis;

export function isColormapId(value: unknown): value is ColormapId {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(COLORMAPS, value);
}

/**
 * The map for an id, a map itself, or nothing. **Anything unknown is viridis**, not an error: the id comes from
 * view state that a saved view or a crafted document can fill with anything, and a figure in the default map is
 * a better answer than none.
 */
export function colormapOf(map?: Colormap | ColormapId | string | null): Colormap {
  if (map === undefined || map === null) return VIRIDIS;
  if (typeof map === "object") return map;
  return isColormapId(map) ? COLORMAPS[map] : VIRIDIS;
}

/** The map at `t` in 0..1 (clamped; NaN reads as 0), linear between the stops, as 0..255 floats; `reverse` reads it from the other end. */
export function colorAtIn(map: Colormap | ColormapId, t: number, reverse = false): [number, number, number] {
  const stops = colormapOf(map).stops;
  const clamped = Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0;
  const x = reverse ? 1 - clamped : clamped;
  const scaled = x * (stops.length - 1);
  const low = Math.min(stops.length - 2, Math.floor(scaled));
  const f = scaled - low;
  const a = stops[low];
  const b = stops[low + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

/** The viridis map at `t` in 0..1: `colorAtIn` with the default map. */
export function colorAt(t: number): [number, number, number] {
  return colorAtIn(VIRIDIS, t);
}

/**
 * Where a value falls in a range, 0..1. A range with no width (every value the same) puts every value in the
 * middle instead of dividing by zero; a value outside the range is clamped to its end.
 */
export function fraction(value: number, min: number, max: number): number {
  if (!(max > min)) return 0.5;
  return Math.min(1, Math.max(0, (value - min) / (max - min)));
}

/** Whether a range is drawn centred on zero: a diverging map and a range with negative and positive values. */
export function spansZero(map: Colormap | ColormapId, min: number, max: number): boolean {
  return colormapOf(map).diverging && min < 0 && max > 0;
}

/**
 * Where a value falls ON THE MAP, 0..1. The same as `fraction` for a sequential map; for a diverging map
 * over a range that spans zero, `min..0..max` lands on `0..0.5..1`, so zero is the neutral colour whatever the
 * asymmetry of the range.
 */
export function fractionIn(map: Colormap | ColormapId, value: number, min: number, max: number): number {
  if (!spansZero(map, min, max)) return fraction(value, min, max);
  if (Number.isNaN(value)) return 0.5;
  if (value <= 0) return Math.max(0, 0.5 * (1 - value / min));
  return Math.min(1, 0.5 + (0.5 * value) / max);
}

/** The range the bands and levels are cut in, when the caller has one: it decides whether a diverging map is centred. */
export interface BandRange {
  min: number;
  max: number;
}

/**
 * Where on the map the value at `u` (0..1 of the way through the RANGE, linearly) is coloured: `u` itself, or
 * - for a diverging map over a range that spans zero - `u` carried through the zero-centring.
 */
function mapPosition(map: Colormap, u: number, range?: BandRange): number {
  if (range === undefined || !spansZero(map, range.min, range.max)) return u;
  return fractionIn(map, range.min + u * (range.max - range.min), range.min, range.max);
}

function rounded(c: readonly number[]): [number, number, number] {
  return [Math.round(c[0]), Math.round(c[1]), Math.round(c[2])];
}

/**
 * The colour of band `band` of `bands` equal bands of the RANGE (its middle), as sRGB bytes: the fill of a
 * banded contour. **Bands are equal in value, not in map position**, to agree with `bandEdges` and the legend's
 * ticks; for a sequential map the two are the same thing.
 */
export function bandColor(band: number, bands: number, map?: Colormap | ColormapId, reverse = false, range?: BandRange): [number, number, number] {
  const m = colormapOf(map);
  return rounded(colorAtIn(m, mapPosition(m, (band + 0.5) / Math.max(1, bands), range), reverse));
}

/** The colour of contour line `level` (0-based) of the `bands - 1` lines between `bands` bands: the map at the boundary it draws, as sRGB bytes. */
export function levelColor(level: number, bands: number, map?: Colormap | ColormapId, reverse = false, range?: BandRange): [number, number, number] {
  const m = colormapOf(map);
  return rounded(colorAtIn(m, mapPosition(m, (level + 1) / Math.max(1, bands), range), reverse));
}

/** The band count a caller asked for, as one drawable: 0 (smooth) or 1..32. */
function bandsOf(bands: number): number {
  return Number.isFinite(bands) && bands > 0 ? Math.min(Math.trunc(bands), 32) : 0;
}

/**
 * The per-vertex tint for a field: RGBA8, one word per value, alpha 255. A `NaN` value gets alpha 0, so the
 * patch's own colour shows there and nothing is invented. The scene reads it as sRGB colour and strength
 * (`SceneManager.setSourceVertexTint`).
 *
 * `map` is the colour map (viridis when omitted) and `reverse` reads it from the other end. **A diverging map
 * over a range that spans zero is centred on zero** (`fractionIn`).
 */
export function tintFor(values: ArrayLike<number>, min: number, max: number, bands = 0, map?: Colormap | ColormapId, reverse = false): Uint8Array {
  const out = new Uint8Array(values.length * 4);
  const m = colormapOf(map);
  /*
    **`bands` above zero draws FLAT steps instead of the smooth map** (claude-win2's results item 3),
    with each value coloured by the MIDDLE of the band it falls in - the same convention `bandColor`
    uses for a banded slice fill, so the walls and a cut agree. The band is found in VALUE space (equal
    value steps, like the legend's edges) and its middle is then put on the map, centred for a diverging one.
  */
  const n = bandsOf(bands);
  // Whether the map is centred on zero is a property of the map and the range, not of a value: decided once here,
  // not by `fractionIn`/`mapPosition` for each of a few hundred thousand vertices (the review of R1). `place` is
  // `fractionIn` with that decision made, so the result of every value is the one `fractionIn` gives.
  const centred = spansZero(m, min, max);
  const place = centred
    ? (v: number): number => (v <= 0 ? Math.max(0, 0.5 * (1 - v / min)) : Math.min(1, 0.5 + (0.5 * v) / max))
    : (v: number): number => fraction(v, min, max);
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (Number.isNaN(v)) continue; // the array is zeros: alpha 0
    let at: number;
    if (n === 0) at = place(v);
    else {
      const u = (Math.min(n - 1, Math.floor(fraction(v, min, max) * n)) + 0.5) / n;
      at = centred ? place(min + u * (max - min)) : u;
    }
    const [r, g, b] = colorAtIn(m, at, reverse);
    out[i * 4] = Math.round(r);
    out[i * 4 + 1] = Math.round(g);
    out[i * 4 + 2] = Math.round(b);
    out[i * 4 + 3] = 255;
  }
  return out;
}

/**
 * A CSS `linear-gradient` of the whole map from `from` (`"to top"`, `"to right"`), for the legend's bar.
 *
 * With a `range` that spans zero and a diverging map the stops are moved so the neutral colour sits where the
 * VALUE zero is on the bar (the bar is linear in value): the same centring the tint uses, or the legend would
 * disagree with the surface about which colour is zero.
 */
export function gradientCssFor(map?: Colormap | ColormapId, reverse = false, from = "to top", range?: BandRange): string {
  const m = colormapOf(map);
  const n = m.stops.length;
  const entries: string[] = [];
  if (range === undefined || !spansZero(m, range.min, range.max)) {
    for (let i = 0; i < n; i++) {
      const c = m.stops[reverse ? n - 1 - i : i];
      entries.push(`rgb(${c[0]} ${c[1]} ${c[2]}) ${((i / (n - 1)) * 100).toFixed(2)}%`);
    }
  } else {
    // The bar position of the value zero, 0..1 from the low end.
    const z = -range.min / (range.max - range.min);
    const ts = new Set<number>(m.stops.map((_, i) => i / (n - 1)));
    ts.add(0.5);
    for (const t of [...ts].sort((a, b) => a - b)) {
      const position = t <= 0.5 ? z * (t / 0.5) : z + (1 - z) * ((t - 0.5) / 0.5);
      const [r, g, b] = rounded(colorAtIn(m, t, reverse));
      entries.push(`rgb(${r} ${g} ${b}) ${(position * 100).toFixed(2)}%`);
    }
  }
  return `linear-gradient(${from}, ${entries.join(", ")})`;
}

/**
 * A CSS `linear-gradient` of `bands` FLAT steps - the legend's bar when the map is banded.
 *
 * **Hard stops, not a smooth ramp**: the bar must show what the model shows, and a gradient beside a
 * banded surface would be a legend that lies about its own colours. Each step is the band's middle colour,
 * the same one `bandColor` gives a banded slice fill.
 */
export function steppedGradientCssFor(map?: Colormap | ColormapId, bands = 0, reverse = false, from = "to top", range?: BandRange): string {
  const n = bandsOf(bands);
  if (n === 0) return gradientCssFor(map, reverse, from, range);
  const stops: string[] = [];
  for (let i = 0; i < n; i++) {
    // **`to top` means step 0 is at the BOTTOM**, which is where the range's minimum is drawn.
    const [r, g, b] = bandColor(i, n, map, reverse, range);
    const colour = `rgb(${r} ${g} ${b})`;
    const low = ((i / n) * 100).toFixed(3);
    const high = (((i + 1) / n) * 100).toFixed(3);
    stops.push(`${colour} ${low}%`, `${colour} ${high}%`);
  }
  return `linear-gradient(${from}, ${stops.join(", ")})`;
}

/** A CSS `linear-gradient` of the whole viridis map: `gradientCssFor` with the default map. */
export function gradientCss(from: string): string {
  return gradientCssFor(VIRIDIS, false, from);
}

/** A CSS `linear-gradient` of `bands` flat steps of the viridis map: `steppedGradientCssFor` with the default map. */
export function steppedGradientCss(from: string, bands: number): string {
  return steppedGradientCssFor(VIRIDIS, bands, false, from);
}
