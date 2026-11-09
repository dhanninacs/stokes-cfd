import { describe, expect, it } from "vitest";
import { COLORMAPS } from "../state/colormap";
import {
  NO_RESULTS,
  autoRange,
  effectiveRange,
  formatTick,
  hasPercentiles,
  isLocked,
  loaded,
  patchTint,
  rangeForField,
  type ResultsView,
} from "./fieldsModel";
import {
  chooseRange,
  clampDigits,
  dropReadout,
  formatTickWith,
  legendLabels,
  legendOf,
  rangeModeOf,
  setFormat,
  setFullRange,
  setManualEnd,
  setMap,
  setPlacement,
  setPrintSize,
  setRangeMode,
  setReverse,
  viewColormap,
} from "./legendModel";
import { readRunFields, type RunFields } from "./resultsApi";

function data(extra: Record<string, number> = {}): RunFields {
  return {
    run: "r1",
    time: 100,
    slice: false,
    surfaces: "ok",
    fields: [
      { name: "U_mag", units: "m/s", min: 0, max: 10, ...extra },
      { name: "p", units: "m2/s2", min: -20, max: 80 },
    ],
  };
}

function view(over: Partial<ResultsView> = {}): ResultsView {
  return { field: "U_mag", lock: null, slice: false, bands: 0, ...over };
}

describe("legendOf", () => {
  it("fills every default for a view that has none of the settings", () => {
    expect(legendOf(view())).toEqual({
      rangeMode: "auto",
      fullRange: false,
      map: "viridis",
      reverse: false,
      format: "auto",
      digits: 3,
      placement: "right",
      printSize: false,
      bands: 0,
    });
    expect(legendOf(null).map).toBe("viridis");
    expect(legendOf(undefined).placement).toBe("right");
  });

  it("keeps what is set and replaces what is nonsense", () => {
    const set = legendOf(view({ map: "magma", reverse: true, bands: 8, format: "scientific", digits: 2, placement: "bottom", printSize: true }));
    expect(set).toMatchObject({ map: "magma", reverse: true, bands: 8, format: "scientific", digits: 2, placement: "bottom", printSize: true });
    const bad = legendOf({ map: "rainbow", format: "hex", placement: "left", digits: Number.NaN, bands: -4 } as unknown as Partial<ResultsView>);
    expect(bad).toMatchObject({ map: "viridis", format: "auto", placement: "right", digits: 3, bands: 0 });
  });

  it("limits bands to what can be drawn", () => {
    expect(legendOf(view({ bands: 99 })).bands).toBe(32);
    expect(legendOf(view({ bands: 8.7 })).bands).toBe(8);
  });

  it("answers the colour map table for a view", () => {
    expect(viewColormap(view())).toBe(COLORMAPS.viridis);
    expect(viewColormap(view({ map: "coolwarm" }))).toBe(COLORMAPS.coolwarm);
  });
});

describe("the view reducers", () => {
  it("change only the field they own and return the same object for no change", () => {
    const v = view();
    expect(setMap(v, "viridis")).toBe(v);
    expect(setMap(v, "turbo")).toEqual({ ...v, map: "turbo" });
    expect(setMap(v, "rainbow" as never)).toBe(v);
    expect(setReverse(v, false)).toBe(v);
    expect(setReverse(v, true).reverse).toBe(true);
    expect(setPlacement(v, "right")).toBe(v);
    expect(setPlacement(v, "floating").placement).toBe("floating");
    expect(setPrintSize(v, false)).toBe(v);
    expect(setPrintSize(v, true).printSize).toBe(true);
    expect(setFullRange(v, false)).toBe(v);
    expect(setFullRange(v, true).fullRange).toBe(true);
  });

  it("sets the format and clamps the digits", () => {
    const v = view();
    expect(setFormat(v, "auto")).toEqual({ ...v, format: "auto", digits: undefined });
    expect(setFormat(v, "fixed", 2)).toMatchObject({ format: "fixed", digits: 2 });
    expect(setFormat(v, "fixed", 99).digits).toBe(8);
    expect(setFormat(view({ format: "fixed", digits: 2 }), "fixed", 2)).toEqual(view({ format: "fixed", digits: 2 }));
  });

  it("survive a new run's arrival: the map and the legend are the user's, not the data's", () => {
    const user = view({ map: "ember", reverse: true, format: "fixed", digits: 1, placement: "bottom", bands: 8 });
    const next = loaded({ ...NO_RESULTS, view: user, seq: 1 }, data());
    expect(next.view).toMatchObject({ map: "ember", reverse: true, format: "fixed", digits: 1, placement: "bottom", bands: 8, field: "U_mag" });
  });
});

describe("range modes", () => {
  const d = data();

  it("reads auto when nothing is fixed, locked when it was frozen, manual when it was typed", () => {
    expect(rangeModeOf(view())).toBe("auto");
    const locked = setRangeMode(view(), d, "locked");
    expect(rangeModeOf(locked)).toBe("locked");
    expect(effectiveRange(locked, d)).toEqual({ min: 0, max: 10 });
    const manual = setRangeMode(view(), d, "manual");
    expect(rangeModeOf(manual)).toBe("manual");
    expect(isLocked(manual)).toBe(true);
  });

  it("goes back to auto by letting go of the lock", () => {
    const back = setRangeMode(setRangeMode(view(), d, "locked"), d, "auto");
    expect(isLocked(back)).toBe(false);
    expect(back.lock).toBeNull();
    expect(rangeModeOf(back)).toBe("auto");
    // Already auto: nothing to do, the same object.
    const auto = view({ rangeMode: "auto" });
    expect(setRangeMode(auto, d, "auto")).toBe(auto);
  });

  it("keeps a range that is already fixed when the mode changes between locked and manual", () => {
    const typed = setManualEnd(setRangeMode(view(), d, "locked"), d, "max", 4);
    expect(typed.lock).toEqual({ field: "U_mag", min: 0, max: 4 });
    expect(rangeModeOf(typed)).toBe("manual");
    const relabelled = setRangeMode(typed, d, "locked");
    expect(relabelled.lock).toEqual({ field: "U_mag", min: 0, max: 4 });
    expect(rangeModeOf(relabelled)).toBe("locked");
  });

  it("typing an end fixes the range and marks it manual; a value that is not a number changes nothing", () => {
    const typed = setManualEnd(view(), d, "min", 2);
    expect(rangeModeOf(typed)).toBe("manual");
    expect(typed.lock).toEqual({ field: "U_mag", min: 2, max: 10 });
    const v = view();
    expect(setManualEnd(v, d, "min", null)).toBe(v);
    expect(setManualEnd(v, d, "min", Number.NaN)).toBe(v);
  });

  it("has nothing to fix without a field", () => {
    const none = view({ field: "none" });
    expect(setRangeMode(none, d, "locked")).toBe(none);
  });
});

describe("the range menu", () => {
  const d = data({ p01: 0.5, p99: 8 });

  it("locks the range that is on screen: Full range, then Locked or Manual, keeps min..max", () => {
    const full = chooseRange(view(), d, "full");
    expect(effectiveRange(full, d)).toEqual({ min: 0, max: 10 });
    for (const mode of ["locked", "manual"] as const) {
      const fixed = chooseRange(full, d, mode);
      expect(isLocked(fixed)).toBe(true);
      expect(rangeModeOf(fixed)).toBe(mode);
      expect(effectiveRange(fixed, d)).toEqual({ min: 0, max: 10 });
      expect(fixed.fullRange).toBe(false);
    }
  });

  it("locks the percentile range when that is what is on screen", () => {
    expect(effectiveRange(chooseRange(view(), d, "locked"), d)).toEqual({ min: 0.5, max: 8 });
  });

  it("puts auto back to the percentile range, and leaves Full range on until another pick", () => {
    const locked = chooseRange(chooseRange(view(), d, "full"), d, "locked");
    const auto = chooseRange(locked, d, "auto");
    expect(isLocked(auto)).toBe(false);
    expect(effectiveRange(auto, d)).toEqual({ min: 0.5, max: 8 });
    expect(chooseRange(auto, d, "full").fullRange).toBe(true);
  });
});

describe("the auto range", () => {
  it("is min..max when the run gives no percentiles", () => {
    expect(effectiveRange(view(), data())).toEqual({ min: 0, max: 10 });
    expect(hasPercentiles(data(), "U_mag")).toBe(false);
  });

  it("is the 1-99 percentile range when the run gives one, and full range puts min..max back", () => {
    const d = data({ p01: 0.5, p99: 8 });
    expect(hasPercentiles(d, "U_mag")).toBe(true);
    expect(effectiveRange(view(), d)).toEqual({ min: 0.5, max: 8 });
    expect(rangeForField(view(), d, "U_mag")).toEqual({ min: 0.5, max: 8 });
    expect(effectiveRange(view({ fullRange: true }), d)).toEqual({ min: 0, max: 10 });
  });

  it("lets the user's lock win over the percentiles", () => {
    const d = data({ p01: 0.5, p99: 8 });
    expect(effectiveRange(view({ lock: { field: "U_mag", min: 1, max: 2 } }), d)).toEqual({ min: 1, max: 2 });
  });

  it("refuses percentiles that make no range: equal, inverted, or outside the data", () => {
    expect(autoRange({}, data({ p01: 5, p99: 5 }), "U_mag")).toEqual({ min: 0, max: 10 });
    expect(autoRange({}, data({ p01: 9, p99: 2 }), "U_mag")).toEqual({ min: 0, max: 10 });
    expect(autoRange({}, data({ p01: 20, p99: 30 }), "U_mag")).toEqual({ min: 0, max: 10 });
    expect(autoRange({}, data({ p01: -5, p99: 12 }), "U_mag")).toEqual({ min: 0, max: 10 });
    expect(autoRange({}, data(), "nope")).toBeNull();
  });

  it("is read from run.fields when the server sends p01 and p99, and ignored when it sends one", () => {
    const both = readRunFields({ run: "r", fields: [{ name: "p", units: "Pa", min: 0, max: 9, p01: 1, p99: 8 }] });
    expect(both?.fields[0]).toEqual({ name: "p", units: "Pa", min: 0, max: 9, p01: 1, p99: 8 });
    const one = readRunFields({ run: "r", fields: [{ name: "p", units: "Pa", min: 0, max: 9, p01: 1 }] });
    expect(one?.fields[0]).toEqual({ name: "p", units: "Pa", min: 0, max: 9 });
    expect(one?.fields[0]).not.toHaveProperty("p01");
  });
});

describe("formatTickWith", () => {
  it("auto is today's formatTick, for every value", () => {
    for (const v of [0, 1, 0.5, 123.456, 1e-5, 12345, -0.0004, 1e6, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(formatTickWith(v, "auto", 5)).toBe(formatTick(v));
      expect(formatTickWith(v)).toBe(formatTick(v));
    }
  });

  it("fixed always writes the same number of decimals, so ticks line up", () => {
    expect(formatTickWith(0.5, "fixed", 2)).toBe("0.50");
    expect(formatTickWith(1, "fixed", 2)).toBe("1.00");
    expect(formatTickWith(12.3456, "fixed", 1)).toBe("12.3");
    expect(formatTickWith(7.6, "fixed", 0)).toBe("8");
    expect(formatTickWith(-0.001, "fixed", 2)).toBe("0.00");
    expect(formatTickWith(-1.5, "fixed", 2)).toBe("-1.50");
  });

  it("scientific writes one digit, the decimals asked for and a plain exponent", () => {
    expect(formatTickWith(0.000125, "scientific", 2)).toBe("1.25e-4");
    expect(formatTickWith(12345, "scientific", 1)).toBe("1.2e4");
    expect(formatTickWith(-3.5, "scientific", 1)).toBe("-3.5e0");
    expect(formatTickWith(0, "scientific", 2)).toBe("0.00e0");
  });

  it("significant keeps the digits asked for, trailing zeros too, and uses an exponent only when it must", () => {
    expect(formatTickWith(1.2, "significant", 3)).toBe("1.20");
    expect(formatTickWith(123.456, "significant", 4)).toBe("123.5");
    expect(formatTickWith(0.012345, "significant", 2)).toBe("0.012");
    expect(formatTickWith(123456, "significant", 2)).toBe("1.2e5");
    expect(formatTickWith(0.00000123, "significant", 2)).toBe("0.0000012");
    expect(formatTickWith(0, "significant", 3)).toBe("0.00");
    expect(formatTickWith(5, "significant", 0)).toBe("5");
  });

  it("writes a dash for a value that is not a number, in every format", () => {
    for (const format of ["auto", "fixed", "scientific", "significant"] as const) {
      expect(formatTickWith(Number.NaN, format, 2)).toBe("-");
      expect(formatTickWith(Number.POSITIVE_INFINITY, format, 2)).toBe("-");
    }
  });

  it("clamps digits", () => {
    expect(clampDigits(undefined)).toBe(3);
    expect(clampDigits(-2)).toBe(0);
    expect(clampDigits(40)).toBe(8);
    expect(clampDigits(2.9)).toBe(2);
    expect(formatTickWith(1, "fixed", 40)).toBe("1.00000000");
  });

  it("labels the legend highest first, in the view's format", () => {
    expect(legendLabels({ min: 0, max: 1 }, view(), 5)).toEqual(["1", "0.75", "0.5", "0.25", "0"]);
    expect(legendLabels({ min: 0, max: 1 }, view({ format: "fixed", digits: 1 }), 3)).toEqual(["1.0", "0.5", "0.0"]);
  });
});

describe("dropReadout", () => {
  it("gives Pa from the kinematic drop and the density, and keeps the kinematic value in detail", () => {
    const out = dropReadout(2.5, "m2/s2", 998);
    expect(out.symbol).toBe("Δp");
    expect(out.units).toBe("Pa");
    expect(out.text).toBe("2495");
    expect(out.detail).toContain("2.5");
    expect(out.detail).toContain("m2/s2");
    expect(out.detail).toContain("998");
  });

  it("keeps the kinematic value, and says so with its symbol, when the density is not known", () => {
    for (const density of [null, 0, -1, Number.NaN]) {
      const out = dropReadout(2.5, "m2/s2", density);
      expect(out).toEqual({ symbol: "Δp/ρ", text: "2.5", units: "m2/s2", detail: "" });
    }
  });

  it("treats an empty unit as the kinematic one, as the rest of the results do", () => {
    expect(dropReadout(1, "", 1000).units).toBe("Pa");
    expect(dropReadout(1, "", null).units).toBe("m2/s2");
  });

  it("reads a solver that reports a pressure as it is, and never multiplies it by a density", () => {
    const out = dropReadout(120, "Pa", 998);
    expect(out).toEqual({ symbol: "Δp", text: "120", units: "Pa", detail: "" });
  });

  it("formats the way the monitors table does", () => {
    expect(dropReadout(0.0000123, "m2/s2", null).text).toBe("1.23e-5");
    expect(dropReadout(0, "m2/s2", 1000).text).toBe("0");
  });
});

describe("patchTint with a map", () => {
  const values = { field: "U_mag", components: 1, count: 2, values: new Float32Array([0, 10]) } as never;

  it("tints with viridis unless another map is given", () => {
    const plain = patchTint(values, { min: 0, max: 10 }, 2);
    expect(Array.from(plain!.subarray(0, 3))).toEqual([71, 1, 85]);
    const magma = patchTint(values, { min: 0, max: 10 }, 2, 0, "magma");
    expect(Array.from(magma!.subarray(0, 3))).toEqual([0, 0, 4]);
    const flipped = patchTint(values, { min: 0, max: 10 }, 2, 0, "magma", true);
    expect(Array.from(flipped!.subarray(0, 3))).toEqual([252, 253, 191]);
  });
});
