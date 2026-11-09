/**
 * The slice list: adding up to eight, removing, the drawing settings and their limits, moving a slice to
 * another plane, the status the cut reports, and the words and numbers the panel prints.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { axisPlane } from "../state/slicePlane";
import { CAP_STATUS_KEY, FOLLOW_FIELD, MAX_LEVELS, MAX_SLICES, MIN_LEVELS, NO_SLICES, addAxisSlice, wallsFaded, addSlice, clearSliceStatus, contourLevels, createDebounce, formatMm, isFull, moveSlice, removeSlice, setFadeWalls, setSliceStatus, sliceFieldOf, sliceLabel, sliceName, sliceSourceOf, sliderRange, statusText, updateSlice, visibleSlices, type SliceList, sliceRequestedFieldOf } from "./sliceModel";

function listOf(n: number): SliceList {
  let list = NO_SLICES;
  for (let i = 0; i < n; i++) list = addAxisSlice(list, "x", i * 0.01);
  return list;
}

describe("addSlice", () => {
  it("adds a visible, filled, smooth slice that follows the surfaces' field, with a new id each time", () => {
    const one = addAxisSlice(NO_SLICES, "z", 0.01);
    expect(one.list).toHaveLength(1);
    expect(one.list[0]).toMatchObject({ id: "s1", origin: "axis", visible: true, field: FOLLOW_FIELD, fill: true, edges: false, lines: false, bands: false, levels: 10 });
    expect(one.list[0].plane).toEqual(axisPlane("z", 0.01));
    const two = addAxisSlice(one, "x", 0.2);
    expect(two.list.map((s) => s.id)).toEqual(["s1", "s2"]);
  });

  it("never reuses an id, even after a slice is removed", () => {
    const list = removeSlice(listOf(2), "s2");
    expect(addAxisSlice(list, "y", 0).list.map((s) => s.id)).toEqual(["s1", "s3"]);
  });

  it("stops at eight and says it is full", () => {
    const full = listOf(MAX_SLICES);
    expect(isFull(full)).toBe(true);
    expect(addAxisSlice(full, "x", 1)).toBe(full);
    expect(full.list).toHaveLength(8);
    expect(isFull(removeSlice(full, "s3"))).toBe(false);
  });

  it("takes a position that is not finite as the origin", () => {
    expect(addAxisSlice(NO_SLICES, "x", Number.NaN).list[0].plane).toEqual(axisPlane("x", 0));
  });

  it("keeps where a slice came from", () => {
    expect(addSlice(NO_SLICES, axisPlane("z", 0.01), "section").list[0].origin).toBe("section");
  });
});

describe("cell edges", () => {
  it("are off for a new slice and turn on and off through updateSlice, like every other setting", () => {
    const one = addAxisSlice(NO_SLICES, "x", 0.2);
    expect(one.list[0].edges).toBe(false);
    const on = updateSlice(one, "s1", { edges: true });
    expect(on.list[0].edges).toBe(true);
    expect(updateSlice(on, "s1", { edges: true })).toBe(on);
    expect(updateSlice(on, "s1", { edges: false }).list[0].edges).toBe(false);
  });
});

describe("removeSlice", () => {
  it("removes the slice and its status; an unknown id changes nothing", () => {
    let list = setSliceStatus(listOf(2), "s1", { status: "ready", message: "" });
    const removed = removeSlice(list, "s1");
    expect(removed.list.map((s) => s.id)).toEqual(["s2"]);
    expect(removed.status.s1).toBeUndefined();
    list = removed;
    expect(removeSlice(list, "nope")).toBe(list);
  });
});

describe("updateSlice", () => {
  it("changes the settings, and returns the same list when nothing changes", () => {
    const list = listOf(1);
    const lines = updateSlice(list, "s1", { lines: true, field: "p" });
    expect(lines.list[0]).toMatchObject({ lines: true, field: "p" });
    expect(updateSlice(lines, "s1", { lines: true })).toBe(lines);
    expect(updateSlice(lines, "s9", { lines: false })).toBe(lines);
  });

  it("holds the levels to 5..20 whole numbers", () => {
    const list = listOf(1);
    expect(updateSlice(list, "s1", { levels: 2 }).list[0].levels).toBe(MIN_LEVELS);
    expect(updateSlice(list, "s1", { levels: 99 }).list[0].levels).toBe(MAX_LEVELS);
    expect(updateSlice(list, "s1", { levels: 12.4 }).list[0].levels).toBe(12);
    expect(updateSlice(list, "s1", { levels: Number.NaN }).list[0].levels).toBe(10);
  });

  it("hiding a slice keeps it in the list", () => {
    const hidden = updateSlice(listOf(2), "s1", { visible: false });
    expect(hidden.list).toHaveLength(2);
    expect(visibleSlices(hidden).map((s) => s.id)).toEqual(["s2"]);
  });
});

describe("moveSlice", () => {
  it("moves the plane; the same plane (to six digits) or an unknown id changes nothing", () => {
    const list = listOf(1);
    const moved = moveSlice(list, "s1", axisPlane("x", 0.3));
    expect(moved.list[0].plane).toEqual(axisPlane("x", 0.3));
    expect(moveSlice(moved, "s1", axisPlane("x", 0.3000000001))).toBe(moved);
    expect(moveSlice(moved, "s7", axisPlane("x", 0.1))).toBe(moved);
  });

  it("keeps the drawing settings", () => {
    const list = updateSlice(listOf(1), "s1", { lines: true, levels: 14 });
    expect(moveSlice(list, "s1", axisPlane("x", 0.4)).list[0]).toMatchObject({ lines: true, levels: 14 });
  });
});

describe("status", () => {
  it("is kept per slice and the list is the same when nothing changes", () => {
    const list = listOf(1);
    const loading = setSliceStatus(list, "s1", { status: "loading", message: "" });
    expect(loading.status.s1.status).toBe("loading");
    expect(setSliceStatus(loading, "s1", { status: "loading", message: "" })).toBe(loading);
    expect(loading.list).toBe(list.list);
  });

  it("drops a status for a slice that is gone, but keeps the cap's", () => {
    const list = listOf(1);
    expect(setSliceStatus(list, "s9", { status: "empty", message: "" })).toBe(list);
    expect(setSliceStatus(list, CAP_STATUS_KEY, { status: "loading", message: "" }).status[CAP_STATUS_KEY].status).toBe("loading");
    const cleared = clearSliceStatus(setSliceStatus(list, CAP_STATUS_KEY, { status: "loading", message: "" }), CAP_STATUS_KEY);
    expect(cleared.status[CAP_STATUS_KEY]).toBeUndefined();
    expect(clearSliceStatus(cleared, CAP_STATUS_KEY)).toBe(cleared);
  });

  it("has words for each state", () => {
    expect(statusText(undefined)).toBe("");
    expect(statusText({ status: "ready", message: "" })).toBe("");
    expect(statusText({ status: "loading", message: "" })).toBe("Cutting...");
    expect(statusText({ status: "empty", message: "" })).toBe("The plane misses the model");
    expect(statusText({ status: "host", message: "" })).toMatch(/host is not available/);
    expect(statusText({ status: "error", message: "boom" })).toBe("boom");
    expect(statusText({ status: "error", message: "" })).toBe("The plane could not be cut");
  });
});

describe("labels and fields", () => {
  it("names an axis slice by axis and millimetres, an oblique one as such", () => {
    expect(sliceLabel({ plane: axisPlane("x", 0.25), origin: "axis" })).toBe("X = 250 mm");
    expect(sliceLabel({ plane: axisPlane("z", 0.0105), origin: "axis" })).toBe("Z = 10.5 mm");
    expect(sliceLabel({ plane: axisPlane("y", 0), origin: "axis" })).toBe("Y = 0 mm");
    expect(sliceLabel({ plane: { origin: [0, 0, 0], normal: [1, 1, 0] }, origin: "axis" })).toBe("Oblique plane");
    expect(sliceLabel({ plane: axisPlane("y", -0.01), origin: "section" })).toBe("Section Y = -10 mm");
    expect(sliceName({ plane: axisPlane("x", 0.25), origin: "axis" })).toBe("Slice X = 250 mm (mesh)");
  });

  it("formatMm drops -0", () => {
    expect(formatMm(-0)).toBe("0 mm");
    expect(formatMm(0.0004)).toBe("0.4 mm");
  });

  it("a slice follows the surfaces' field unless it has its own", () => {
    expect(sliceFieldOf({ field: FOLLOW_FIELD }, "U_mag")).toBe("U_mag");
    expect(sliceFieldOf({ field: "p" }, "U_mag")).toBe("p");
  });

  it("the scene source of a slice is mesh:slice:<id>, not the mid-plane's mesh:slice", () => {
    expect(sliceSourceOf("s3")).toBe("mesh:slice:s3");
  });
});

describe("the fade of the walls", () => {
  it("is on until turned off, and the same list comes back when it does not change", () => {
    expect(NO_SLICES.fadeWalls).toBe(true);
    const off = setFadeWalls(NO_SLICES, false);
    expect(off.fadeWalls).toBe(false);
    expect(setFadeWalls(off, false)).toBe(off);
  });
});

describe("sliderRange", () => {
  it("spans the model in whole half-millimetre steps", () => {
    expect(sliderRange([0, 0.5])).toEqual({ min: 0, max: 500 });
    expect(sliderRange([0.00012, 0.01999])).toEqual({ min: 0, max: 20 });
    expect(sliderRange([-0.0103, 0.0103])).toEqual({ min: -10.5, max: 10.5 });
  });

  it("has a range for a model that is not there yet", () => {
    expect(sliderRange(null)).toEqual({ min: -500, max: 500 });
    expect(sliderRange([1, 0])).toEqual({ min: -500, max: 500 });
  });
});

describe("contourLevels", () => {
  it("N bands have N - 1 lines between them, evenly spaced inside the range", () => {
    expect(contourLevels(0, 10, 5)).toEqual([2, 4, 6, 8]);
    expect(contourLevels(0, 1, 20)).toHaveLength(19);
    expect(contourLevels(-1, 1, 10).every((v) => v > -1 && v < 1)).toBe(true);
  });

  it("holds the count to 5..20 and gives none for a range with no width", () => {
    expect(contourLevels(0, 1, 2)).toHaveLength(MIN_LEVELS - 1);
    expect(contourLevels(0, 1, 99)).toHaveLength(MAX_LEVELS - 1);
    expect(contourLevels(1, 1, 10)).toEqual([]);
    expect(contourLevels(2, 1, 10)).toEqual([]);
  });
});

describe("createDebounce", () => {
  afterEach(() => vi.useRealTimers());

  it("runs once, with the last value, when the calls have rested for the delay", () => {
    vi.useFakeTimers();
    const seen: number[] = [];
    const slider = createDebounce((mm: number) => seen.push(mm), 300);
    slider.call(1);
    vi.advanceTimersByTime(200);
    slider.call(2);
    vi.advanceTimersByTime(200);
    slider.call(3);
    expect(seen).toEqual([]);
    expect(slider.pending).toBe(true);
    vi.advanceTimersByTime(299);
    expect(seen).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(seen).toEqual([3]);
    expect(slider.pending).toBe(false);
  });

  it("flush runs a waiting call now, cancel drops it, and neither does anything without one", () => {
    vi.useFakeTimers();
    const seen: number[] = [];
    const slider = createDebounce((mm: number) => seen.push(mm), 300);
    slider.flush();
    slider.call(5);
    slider.flush();
    expect(seen).toEqual([5]);
    slider.call(6);
    slider.cancel();
    vi.advanceTimersByTime(1000);
    expect(seen).toEqual([5]);
  });
});

describe("sliceRequestedFieldOf", () => {
  it("fetches a speed as its VELOCITY, so one request serves the colouring and the arrows", () => {
    // **Measured on the duct's slice**: 2761 points, `U` 33 132 bytes against `U_mag`'s 11 044 -
    // 22 088 more, before gzip, and the header ranges `U` over its magnitude, so the legend stays
    // true. This is the substitution claude-win2's decision (c) asks for.
    expect(sliceRequestedFieldOf("U_mag")).toBe("U");
  });

  it("leaves EVERY other field alone: do not fetch what nobody asked for", () => {
    expect(sliceRequestedFieldOf("p")).toBe("p");
    expect(sliceRequestedFieldOf("T")).toBe("T");
    expect(sliceRequestedFieldOf("U")).toBe("U");
  });
});

describe("wallsFaded", () => {
  const none = { setting: true, slices: 0, iso: false, midBuried: false };
  it("fades for a slice of the list, the iso, or the mid-plane slice whose cut was removed", () => {
    expect(wallsFaded(none)).toBe(false);
    expect(wallsFaded({ ...none, slices: 1 })).toBe(true);
    expect(wallsFaded({ ...none, iso: true })).toBe(true);
    // Measured on pipe3: Remove section with the Slice look on brought the near half of the pipe back over the
    // slice, and the end face filled the view. The walls fade so the slice stays on screen.
    expect(wallsFaded({ ...none, midBuried: true })).toBe(true);
  });
  it("fades for streamlines and volume arrows, which fill the fluid and no section can expose (2026-11-04)", () => {
    expect(wallsFaded({ ...none, inside: true })).toBe(true);
    expect(wallsFaded({ ...none, inside: false })).toBe(false);
  });
  it("never fades with the setting off", () => {
    expect(wallsFaded({ setting: false, slices: 2, iso: true, midBuried: true, inside: true })).toBe(false);
  });
});
