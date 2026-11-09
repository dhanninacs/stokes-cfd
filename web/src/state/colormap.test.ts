import { describe, expect, it } from "vitest";
import {
  COLORMAP,
  COLORMAPS,
  COLORMAP_IDS,
  bandColor,
  colorAt,
  colorAtIn,
  colormapOf,
  fraction,
  fractionIn,
  gradientCss,
  gradientCssFor,
  levelColor,
  steppedGradientCss,
  steppedGradientCssFor,
  tintFor,
  type ColormapId,
} from "./colormap";

describe("colormap", () => {
  it("has 32 stops, all bytes, and lightens from dark to bright", () => {
    expect(COLORMAP).toHaveLength(32);
    for (const stop of COLORMAP) for (const channel of stop) expect(channel >= 0 && channel <= 255 && Number.isInteger(channel)).toBe(true);
    const luma = (c: readonly number[]): number => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    for (let i = 1; i < COLORMAP.length; i++) expect(luma(COLORMAP[i])).toBeGreaterThan(luma(COLORMAP[i - 1]));
  });

  it("colorAt(0) and colorAt(1) are the first and last stops, and the ends clamp", () => {
    expect(colorAt(0)).toEqual([...COLORMAP[0]]);
    expect(colorAt(1)).toEqual([...COLORMAP[31]]);
    expect(colorAt(-5)).toEqual([...COLORMAP[0]]);
    expect(colorAt(7)).toEqual([...COLORMAP[31]]);
    expect(colorAt(Number.NaN)).toEqual([...COLORMAP[0]]);
  });

  it("interpolates linearly between two stops", () => {
    const mid = colorAt(0.5 / 31);
    for (let c = 0; c < 3; c++) expect(mid[c]).toBeCloseTo((COLORMAP[0][c] + COLORMAP[1][c]) / 2, 9);
  });

  it("tintFor gives the end stops at min and max, with alpha 255", () => {
    const rgba = tintFor([0, 10], 0, 10);
    expect(Array.from(rgba.subarray(0, 4))).toEqual([...COLORMAP[0], 255]);
    expect(Array.from(rgba.subarray(4, 8))).toEqual([...COLORMAP[31], 255]);
  });

  it("tintFor leaves a NaN transparent so the patch's own colour shows", () => {
    const rgba = tintFor([Number.NaN, 5], 0, 10);
    expect(Array.from(rgba.subarray(0, 4))).toEqual([0, 0, 0, 0]);
    expect(rgba[7]).toBe(255);
  });

  it("clamps values outside the range to its ends", () => {
    const rgba = tintFor([-3, 99], 0, 10);
    expect(Array.from(rgba.subarray(0, 3))).toEqual([...COLORMAP[0]]);
    expect(Array.from(rgba.subarray(4, 7))).toEqual([...COLORMAP[31]]);
  });

  it("does not divide by zero when min equals max (or is above it)", () => {
    expect(fraction(3, 3, 3)).toBe(0.5);
    expect(fraction(3, 5, 1)).toBe(0.5);
    const rgba = tintFor([3, 3], 3, 3);
    expect(Array.from(rgba.subarray(0, 4))).toEqual(Array.from(rgba.subarray(4, 8)));
    expect(rgba[3]).toBe(255);
    expect(Array.from(rgba).every((b) => Number.isInteger(b))).toBe(true);
  });

  it("builds a gradient of every stop for the legend", () => {
    const css = gradientCss("to top");
    expect(css.startsWith("linear-gradient(to top, rgb(71 1 85) 0.00%")).toBe(true);
    expect(css.endsWith("rgb(252 231 33) 100.00%)")).toBe(true);
    expect(css.match(/rgb\(/g)).toHaveLength(32);
  });
});

describe("steppedGradientCss", () => {
  it("is the smooth gradient when there are no bands", () => {
    // Banding is an addition to the approved palette, so 0 must reproduce exactly what ships.
    expect(steppedGradientCss("to top", 0)).toBe(gradientCss("to top"));
  });

  it("gives each band a HARD stop, so the bar shows what the model shows", () => {
    // A gradient beside a banded surface would be a legend that lies about its own colours.
    const css = steppedGradientCss("to top", 4);
    // Four bands, two stops each, every pair sharing one colour.
    expect(css.match(/rgb\(/g)?.length).toBe(8);
    const pairs = css.split(",").map((s) => s.trim());
    // The first two stops are the same colour: the low band has no ramp inside it.
    const firstColour = pairs.find((p) => p.startsWith("rgb("))!;
    const secondColour = pairs[pairs.indexOf(firstColour) + 1];
    expect(secondColour).toMatch(/^rgb\(/);
    expect(secondColour.split(" ").slice(0, 3)).toEqual(firstColour.split(" ").slice(0, 3));
  });
});

describe("the registry", () => {
  const ids = Object.keys(COLORMAPS) as ColormapId[];

  it("lists the nine maps, the default first, and the menu order names every one", () => {
    expect([...ids].sort()).toEqual(["aurora", "coolwarm", "ember", "greys", "iceburn", "magma", "ocean", "turbo", "viridis"]);
    expect([...COLORMAP_IDS].sort()).toEqual([...ids].sort());
    expect(COLORMAP_IDS[0]).toBe("viridis");
  });

  it("gives every map at least 5 stops, all valid sRGB bytes, and its own id", () => {
    for (const id of ids) {
      const map = COLORMAPS[id];
      expect(map.id).toBe(id);
      expect(map.label.length).toBeGreaterThan(0);
      expect(map.stops.length).toBeGreaterThanOrEqual(5);
      for (const stop of map.stops) {
        expect(stop).toHaveLength(3);
        for (const channel of stop) expect(Number.isInteger(channel) && channel >= 0 && channel <= 255).toBe(true);
      }
    }
  });

  it("keeps viridis exactly as it shipped: COLORMAP is its table, colorAt is its lookup", () => {
    expect(COLORMAPS.viridis.stops).toBe(COLORMAP);
    for (const t of [0, 0.25, 0.5, 0.77, 1]) expect(colorAtIn("viridis", t)).toEqual(colorAt(t));
    expect(colorAtIn("viridis", 0)).toEqual([71, 1, 85]);
    expect(colorAtIn("viridis", 1)).toEqual([252, 231, 33]);
    // The middle falls between stops 15 and 16 (31 steps), so it is their average.
    const mid = colorAtIn("viridis", 0.5);
    for (let c = 0; c < 3; c++) expect(mid[c]).toBeCloseTo((COLORMAP[15][c] + COLORMAP[16][c]) / 2, 9);
  });

  it("marks the diverging maps, and only those", () => {
    expect(ids.filter((id) => COLORMAPS[id].diverging).sort()).toEqual(["coolwarm", "iceburn"]);
  });

  it("copies the old viewer's stop colours for the four bespoke maps", () => {
    expect(COLORMAPS.aurora.stops[0]).toEqual([0x0b, 0x0d, 0x3a]);
    expect(COLORMAPS.aurora.stops).toHaveLength(5);
    expect(COLORMAPS.ember.stops).toHaveLength(6);
    expect(COLORMAPS.ember.stops[5]).toEqual([0xfc, 0xe9, 0x6b]);
    expect(COLORMAPS.ocean.stops[4]).toEqual([0xea, 0xfc, 0xff]);
    expect(COLORMAPS.iceburn.stops[2]).toEqual([0xf1, 0xfa, 0xee]);
  });

  it("falls back to viridis for an unknown or missing id instead of failing", () => {
    expect(colormapOf(undefined)).toBe(COLORMAPS.viridis);
    expect(colormapOf("nonsense")).toBe(COLORMAPS.viridis);
    expect(colormapOf("magma")).toBe(COLORMAPS.magma);
  });

  it("reads each map from its first stop to its last", () => {
    for (const id of ids) {
      expect(colorAtIn(id, 0)).toEqual([...COLORMAPS[id].stops[0]]);
      expect(colorAtIn(id, 1)).toEqual([...COLORMAPS[id].stops[COLORMAPS[id].stops.length - 1]]);
    }
  });

  it("makes the sequential maps lighten or darken one way, not both (a monotone lightness)", () => {
    const luma = (c: readonly number[]): number => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    for (const id of ["viridis", "magma", "greys", "aurora", "ember", "ocean"] as const) {
      const l = COLORMAPS[id].stops.map(luma);
      const up = l.every((v, i) => i === 0 || v >= l[i - 1] - 1);
      const down = l.every((v, i) => i === 0 || v <= l[i - 1] + 1);
      expect(up || down).toBe(true);
    }
  });
});

describe("reverse", () => {
  it("flips the map end for end", () => {
    for (const id of COLORMAP_IDS) {
      for (const t of [0, 0.3, 0.5, 0.8, 1]) {
        const a = colorAtIn(id, t, true);
        const b = colorAtIn(id, 1 - t);
        for (let c = 0; c < 3; c++) expect(a[c]).toBeCloseTo(b[c], 9);
      }
    }
  });

  it("flips a tint and a gradient, and leaves the default call alone", () => {
    const fwd = tintFor([0, 10], 0, 10, 0, "magma");
    const rev = tintFor([0, 10], 0, 10, 0, "magma", true);
    expect(Array.from(rev.subarray(0, 4))).toEqual(Array.from(fwd.subarray(4, 8)));
    expect(Array.from(rev.subarray(4, 8))).toEqual(Array.from(fwd.subarray(0, 4)));
    expect(gradientCssFor("magma", true).startsWith("linear-gradient(to top, rgb(252 253 191) 0.00%")).toBe(true);
    expect(gradientCssFor("viridis", false)).toBe(gradientCss("to top"));
  });

  it("keeps a diverging map's neutral colour on zero when reversed", () => {
    const rgba = tintFor([0], -1, 3, 0, "coolwarm", true);
    const neutral = colorAtIn("coolwarm", 0.5, true).map(Math.round);
    expect(Array.from(rgba.subarray(0, 3))).toEqual(neutral);
  });
});

describe("tintFor with a map", () => {
  it("is the viridis tint when no map is given", () => {
    const values = [0, 1, 2.5, 7, 10];
    expect(Array.from(tintFor(values, 0, 10, 0, "viridis"))).toEqual(Array.from(tintFor(values, 0, 10)));
    expect(Array.from(tintFor(values, 0, 10, 8, "viridis"))).toEqual(Array.from(tintFor(values, 0, 10, 8)));
  });

  it("paints another map's end stops at min and max", () => {
    const rgba = tintFor([0, 10], 0, 10, 0, "turbo");
    expect(Array.from(rgba.subarray(0, 3))).toEqual([...COLORMAPS.turbo.stops[0]]);
    expect(Array.from(rgba.subarray(4, 7))).toEqual([...COLORMAPS.turbo.stops[COLORMAPS.turbo.stops.length - 1]]);
  });

  it("quantises into bands: every value of a band gets one colour, the band's middle one", () => {
    const values = [0.1, 1.2, 2.4, 2.6, 4.9, 5.0, 7.4, 9.9];
    const rgba = tintFor(values, 0, 10, 4, "magma");
    const at = (i: number) => Array.from(rgba.subarray(i * 4, i * 4 + 3));
    // [0,2.5) holds 0.1, 1.2 and 2.4; [2.5,5) holds 2.6 and 4.9.
    expect(at(0)).toEqual(at(1));
    expect(at(1)).toEqual(at(2));
    expect(at(3)).toEqual(at(4));
    expect(at(0)).not.toEqual(at(3));
    expect(at(0)).toEqual(bandColor(0, 4, "magma"));
    expect(at(7)).toEqual(bandColor(3, 4, "magma"));
    const distinct = new Set(Array.from({ length: values.length }, (_, i) => at(i).join(",")));
    expect(distinct.size).toBe(4);
  });

  it("agrees with the slice fill and the legend on a band's colour", () => {
    for (const id of COLORMAP_IDS) {
      const rgba = tintFor([3.1], 0, 8, 8, id);
      expect(Array.from(rgba.subarray(0, 3))).toEqual(bandColor(3, 8, id));
    }
    const css = steppedGradientCssFor("ember", 4);
    const [r, g, b] = bandColor(0, 4, "ember");
    expect(css).toContain(`rgb(${r} ${g} ${b}) 0.000%`);
  });
});

describe("a diverging map centred on zero", () => {
  it("maps min..0..max onto 0..0.5..1", () => {
    const m = COLORMAPS.coolwarm;
    expect(fractionIn(m, -2, -2, 6)).toBe(0);
    expect(fractionIn(m, 0, -2, 6)).toBe(0.5);
    expect(fractionIn(m, 6, -2, 6)).toBe(1);
    expect(fractionIn(m, -1, -2, 6)).toBeCloseTo(0.25, 12);
    expect(fractionIn(m, 3, -2, 6)).toBeCloseTo(0.75, 12);
    expect(fractionIn(m, -50, -2, 6)).toBe(0);
    expect(fractionIn(m, 50, -2, 6)).toBe(1);
  });

  it("puts zero at the middle colour even when the range is lopsided", () => {
    const rgba = tintFor([0, -1, 9], -1, 9, 0, "coolwarm");
    const middle = colorAtIn("coolwarm", 0.5).map(Math.round);
    expect(Array.from(rgba.subarray(0, 3))).toEqual(middle);
    expect(Array.from(rgba.subarray(4, 7))).toEqual([...COLORMAPS.coolwarm.stops[0]]);
    expect(Array.from(rgba.subarray(8, 11))).toEqual([...COLORMAPS.coolwarm.stops[10]]);
  });

  it("is plain linear scaling when the range does not span zero, or the map is sequential", () => {
    expect(fractionIn(COLORMAPS.coolwarm, 5, 2, 10)).toBe(fraction(5, 2, 10));
    expect(fractionIn(COLORMAPS.coolwarm, -5, -10, -2)).toBe(fraction(-5, -10, -2));
    expect(fractionIn(COLORMAPS.coolwarm, 0, -3, 0)).toBe(1);
    expect(fractionIn(COLORMAPS.viridis, 0, -2, 6)).toBe(fraction(0, -2, 6));
    expect(Array.from(tintFor([0], -2, 6, 0, "viridis"))).toEqual(Array.from(tintFor([0], -2, 6)));
  });

  it("moves the legend's stops so the neutral colour sits on the value zero", () => {
    // Range -1..3: zero is a quarter of the way up the bar.
    const css = gradientCssFor("coolwarm", false, "to top", { min: -1, max: 3 });
    const neutral = colorAtIn("coolwarm", 0.5).map(Math.round);
    expect(css).toContain(`rgb(${neutral.join(" ")}) 25.00%`);
    // A symmetric range puts it in the middle.
    expect(gradientCssFor("coolwarm", false, "to top", { min: -2, max: 2 })).toContain(`rgb(${neutral.join(" ")}) 50.00%`);
    // A range that does not span zero is the plain gradient.
    expect(gradientCssFor("coolwarm", false, "to top", { min: 1, max: 3 })).toBe(gradientCssFor("coolwarm"));
  });

  it("centres banded colours on zero too: cool below the middle, warm above, neutral on the line at zero", () => {
    // Range -2..2 in 4 bands: edges -2,-1,0,1,2, so the middles are -1.5,-0.5,0.5,1.5.
    const range = { min: -2, max: 2 };
    const lowerMiddle = bandColor(1, 4, "coolwarm", false, range);
    const upperMiddle = bandColor(2, 4, "coolwarm", false, range);
    expect(lowerMiddle).not.toEqual(upperMiddle);
    // Cool below zero, warm above: red grows, blue shrinks.
    expect(upperMiddle[0]).toBeGreaterThan(lowerMiddle[0]);
    expect(upperMiddle[2]).toBeLessThan(lowerMiddle[2]);
    // The line between the second and third band is at zero, the neutral colour.
    expect(levelColor(1, 4, "coolwarm", false, range)).toEqual(colorAtIn("coolwarm", 0.5).map(Math.round));
  });

  it("builds a stepped bar from the same colours the tint uses", () => {
    const range = { min: -1, max: 3 };
    const rgba = tintFor([-0.8], range.min, range.max, 4, "coolwarm");
    expect(Array.from(rgba.subarray(0, 3))).toEqual(bandColor(0, 4, "coolwarm", false, range));
    expect(steppedGradientCssFor("coolwarm", 4, false, "to top", range)).toContain(`rgb(${bandColor(0, 4, "coolwarm", false, range).join(" ")}) 0.000%`);
    expect(steppedGradientCssFor("coolwarm", 0, false, "to top", range)).toBe(gradientCssFor("coolwarm", false, "to top", range));
  });
});

describe("the viridis wrappers that existed before the registry", () => {
  it("are the registry's functions with the default map", () => {
    expect(gradientCss("to right")).toBe(gradientCssFor("viridis", false, "to right"));
    expect(steppedGradientCss("to top", 6)).toBe(steppedGradientCssFor("viridis", 6));
    expect(bandColor(2, 8)).toEqual(bandColor(2, 8, "viridis"));
  });
});
