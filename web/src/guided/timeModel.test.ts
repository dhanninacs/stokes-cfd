import { describe, expect, it } from "vitest";
import { historyAxis, looksSteady, nextFrame, stepTime, timeIndex, timeText, writtenTimes } from "./timeModel";

describe("the written times of a run", () => {
  it("sorts, drops repeats and the initial condition", () => {
    expect(writtenTimes([0.2, 0, 0.1, 0.2])).toEqual([0.1, 0.2]);
    expect(writtenTimes([0])).toEqual([0]);
    expect(writtenTimes(undefined)).toEqual([]);
  });

  it("finds the one on screen and steps between them, never past an end", () => {
    const times = [0.1, 0.2, 0.3];
    expect(timeIndex(times, 0.2000001)).toBe(1);
    expect(stepTime(times, 0.2, 1)).toBe(0.3);
    expect(stepTime(times, 0.2, -1)).toBe(0.1);
    expect(stepTime(times, 0.3, 1)).toBeNull();
    expect(stepTime([], 0, 1)).toBeNull();
  });

  it("plays to the end, or loops back to the first", () => {
    expect(nextFrame([1, 2], 1, false)).toBe(2);
    expect(nextFrame([1, 2], 2, false)).toBeNull();
    expect(nextFrame([1, 2], 2, true)).toBe(1);
    expect(nextFrame([1], 1, true)).toBeNull();
  });
});

describe("words for a time", () => {
  it("writes seconds or an iteration", () => {
    expect(timeText(0.0125, false)).toBe("0.0125 s");
    expect(timeText(2e-5, false)).toBe("2.00e-5 s");
    expect(timeText(450, true)).toBe("iteration 450");
    expect(looksSteady([450, 500])).toBe(true);
    expect(looksSteady([0.1, 0.2])).toBe(false);
  });

  it("labels a history by iteration or by time", () => {
    expect(historyAxis([1, 2, 3])).toBe("iteration");
    expect(historyAxis([0.001, 0.002])).toBe("time (s)");
  });
});
