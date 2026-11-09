/**
 * The scale bar's arithmetic and its labels.
 *
 * The bar exists because a viewport with no scale cannot be trusted, so a wrong label is worse than
 * no bar at all: it would quietly tell the user their 20 mm part is 20 m. These check the 1-2-5
 * series, the unit each length lands in, and that the drawn width always stands for the printed
 * length.
 */
import { describe, expect, it } from "vitest";

import { formatLength, scaleBar, TARGET_PIXELS } from "./scale";

describe("formatLength", () => {
  it("uses the unit that keeps the number readable", () => {
    // The rule: the largest unit whose value is at least 1. No centimetre on the ladder, so a
    // millimetre-scale model never gets its unit changed under it.
    expect(formatLength(0.01)).toBe("10 mm");
    expect(formatLength(0.0025)).toBe("2.5 mm");
    expect(formatLength(1)).toBe("1 m");
    expect(formatLength(2)).toBe("2 m");
    expect(formatLength(1500)).toBe("1.5 km");
    expect(formatLength(0.0005)).toBe("500 µm");
    expect(formatLength(0.0000005)).toBe("0.5 µm");
  });

  it("keeps at most three significant figures and drops trailing zeros", () => {
    expect(formatLength(0.02)).toBe("20 mm");
    expect(formatLength(0.1)).toBe("100 mm");
    expect(formatLength(0.125)).toBe("125 mm");
    expect(formatLength(0.3)).toBe("300 mm");
  });

  it("refuses a length that is not a length rather than printing nonsense", () => {
    expect(formatLength(0)).toBe("—");
    expect(formatLength(-1)).toBe("—");
    expect(formatLength(Number.NaN)).toBe("—");
    expect(formatLength(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("the scale bar", () => {
  it("is a round length from the 1-2-5 series", () => {
    // A metre per pixel: the bar may be up to 96 px, so 50 m is the longest 1-2-5 value that fits.
    expect(scaleBar(1, false)?.metres).toBe(50);
    expect(scaleBar(0.1, false)?.metres).toBe(5);
    expect(scaleBar(0.001, false)?.metres).toBe(0.05);
  });

  it("never overruns its corner", () => {
    for (const perPixel of [1e-5, 0.0004, 0.01, 0.3, 1, 7, 250]) {
      const bar = scaleBar(perPixel, false);
      expect(bar).not.toBeNull();
      expect(bar!.pixels).toBeLessThanOrEqual(TARGET_PIXELS + 1e-9);
      expect(bar!.pixels).toBeGreaterThan(0);
    }
  });

  it("says what it drew: the width always stands for the printed length", () => {
    for (const perPixel of [1e-5, 0.0007, 0.05, 0.3, 1, 7, 250]) {
      const bar = scaleBar(perPixel, false)!;
      // The one property that matters: pixels * metresPerPixel === the length on the label.
      expect(bar.pixels * perPixel).toBeCloseTo(bar.metres, 9);
      expect(bar.label).toBe(formatLength(bar.metres));
    }
  });

  it("is exact under an orthographic camera and says when it is not", () => {
    expect(scaleBar(0.01, false)?.perspective).toBe(false);
    expect(scaleBar(0.01, true)?.perspective).toBe(true);
  });

  it("draws nothing rather than something wrong when it has no scale", () => {
    expect(scaleBar(0, false)).toBeNull();
    expect(scaleBar(-1, false)).toBeNull();
    expect(scaleBar(Number.NaN, false)).toBeNull();
  });
});
