import { describe, expect, it } from "vitest";
import { baseSizeFor, finenessOf, lengthText, smallestSide, type Box } from "./sizing";

const pipe: Box = { min: [0, 0, 0], max: [0.1, 0.02, 0.4] };

describe("the Mesh card's size control", () => {
  it("takes the smallest side of the box", () => {
    expect(smallestSide(pipe)).toBeCloseTo(0.02);
    expect(smallestSide(null)).toBeNull();
    expect(smallestSide({ min: [0, 0, 0], max: [0, 0, 0] })).toBeNull();
  });

  it("ignores a flat side so a sheet still gets a size", () => {
    expect(smallestSide({ min: [0, 0, 0], max: [0.5, 0.2, 0] })).toBeCloseTo(0.2);
  });

  it("maps Coarse, Medium and Fine to a twentieth, fortieth and eightieth of it", () => {
    expect(baseSizeFor(pipe, "coarse")).toBe(0.001);
    expect(baseSizeFor(pipe, "medium")).toBe(0.0005);
    expect(baseSizeFor(pipe, "fine")).toBe(0.00025);
    expect(baseSizeFor(null, "medium")).toBeNull();
  });

  it("recognises a setting that is one of the three, and none for a custom size", () => {
    expect(finenessOf(pipe, 0.0005)).toBe("medium");
    expect(finenessOf(pipe, 0.001)).toBe("coarse");
    expect(finenessOf(pipe, 0.0007)).toBeNull();
    // within 10 % of a preset still reads as that preset
    expect(finenessOf(pipe, 0.00054)).toBe("medium");
    expect(finenessOf(pipe, 0.00046)).toBe("medium");
    expect(finenessOf(pipe, 0.00056)).toBeNull();
    expect(finenessOf(pipe, null)).toBeNull();
  });

  it("says a length in the unit a person reads", () => {
    expect(lengthText(0.00025)).toBe("0.25 mm");
    expect(lengthText(0.0125)).toBe("1.3 cm");
    expect(lengthText(0.8)).toBe("80 cm");
    expect(lengthText(2)).toBe("2 m");
    expect(lengthText(0)).toBe("");
  });

  it("takes the presets from the fluid body's box, not the whole scene's", () => {
    const fluid: Box = { min: [0.02, 0.02, 0], max: [0.04, 0.04, 0.4] };
    expect(baseSizeFor(fluid, "coarse")).toBe(0.001);
    expect(baseSizeFor(fluid, "fine")).toBe(0.00025);
    expect(baseSizeFor({ min: [0, 0, 0], max: [0.1, 0.1, 0.4] }, "coarse")).toBe(0.005);
  });
});

import { MARKS, SLIDER_MAX_DIVISOR, SLIDER_MIN_DIVISOR, baseSizeAt, parseCellSize, positionOf, positionOfDivisor } from "./sizing";

describe("the fineness slider", () => {
  const box = { min: [0, 0, 0], max: [0.1, 0.1, 0.4] } as const; // smallest side 0.1 m

  it("puts the three presets a quarter apart on a log scale", () => {
    expect(MARKS.coarse).toBeCloseTo(0.25);
    expect(MARKS.medium).toBeCloseTo(0.5);
    expect(MARKS.fine).toBeCloseTo(0.75);
  });

  it("maps a position to the preset's own size and back", () => {
    expect(baseSizeAt(box, MARKS.coarse)).toBe(0.005);
    expect(baseSizeAt(box, MARKS.medium)).toBe(0.0025);
    expect(baseSizeAt(box, MARKS.fine)).toBe(0.0013); // 1.25 mm to two figures, as the preset's own
    expect(positionOf(box, 0.0025)).toBeCloseTo(MARKS.medium);
  });

  it("clamps the ends and says nothing without a box", () => {
    expect(baseSizeAt(box, -1)).toBe(baseSizeAt(box, 0));
    expect(baseSizeAt(box, 5)).toBe(baseSizeAt(box, 1));
    expect(baseSizeAt(null, 0.5)).toBeNull();
    expect(positionOf(null, 0.001)).toBeNull();
    expect(positionOfDivisor(SLIDER_MIN_DIVISOR / 4)).toBe(0);
    expect(positionOfDivisor(SLIDER_MAX_DIVISOR * 4)).toBe(1);
  });

  it("reads a typed size: millimetres by default, units understood", () => {
    expect(parseCellSize("2.5", box)).toBe(0.0025);
    expect(parseCellSize("2.5 mm", box)).toBe(0.0025);
    expect(parseCellSize("0.4cm", box)).toBe(0.004);
    expect(parseCellSize("0.005 m", box)).toBe(0.005);
  });

  it("refuses text that is not a size, and sizes that would be absurd", () => {
    expect(parseCellSize("", box)).toBeNull();
    expect(parseCellSize("fine", box)).toBeNull();
    expect(parseCellSize("-2", box)).toBeNull();
    expect(parseCellSize("0", box)).toBeNull();
    expect(parseCellSize("0.001", box)).toBeNull(); // 0.001 mm: 100,000 cells across
    expect(parseCellSize("90", box)).toBeNull(); // 90 mm: barely one cell
  });
});
