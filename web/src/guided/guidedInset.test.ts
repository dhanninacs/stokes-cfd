import { describe, expect, it } from "vitest";
import { freeRect } from "../viewport/freeRect";
import { GUIDED_CARD_INSET, GUIDED_STRIP_INSET, guidedInset } from "./guidedInset";

describe("the guided inset", () => {
  it("is the card column and the strip row in Guided mode", () => {
    expect(guidedInset("guided")).toEqual({ left: 312, top: 0 });
    expect(GUIDED_CARD_INSET).toBe(312);
    expect(GUIDED_STRIP_INSET).toBe(0);
  });

  it("is nothing in Full mode", () => {
    expect(guidedInset("full")).toEqual({ left: 0, top: 0 });
  });

  it("moves the free rectangle off the card; the strip is in the top bar", () => {
    const window = { width: 1600, height: 1000 };
    const inset = guidedInset("guided");
    const free = freeRect({ left: 0 + inset.left, right: 0, top: inset.top, bottom: 0, window });
    expect(free).toEqual({ x: 312, y: 0, width: 1288, height: 1000 });
  });

  it("adds to a left dock rather than replacing it", () => {
    const window = { width: 1600, height: 1000 };
    const inset = guidedInset("guided");
    expect(freeRect({ left: 200 + inset.left, right: 320, top: inset.top, bottom: 0, window }).x).toBe(512);
  });

  it("leaves Full mode's free rectangle as the docks give it", () => {
    const window = { width: 1600, height: 1000 };
    const inset = guidedInset("full");
    expect(freeRect({ left: 300 + inset.left, right: 320, top: inset.top, bottom: 0, window })).toEqual(
      freeRect({ left: 300, right: 320, top: 0, bottom: 0, window }),
    );
  });
});
