import { describe, expect, it } from "vitest";
import { ago, folderOf, monogram, recentCards } from "./startScreenModel";

describe("ago", () => {
  const now = Date.parse("2026-11-03T12:00:00Z");
  it("says how long ago in a few words", () => {
    expect(ago("2026-11-03T11:59:30Z", now)).toBe("just now");
    expect(ago("2026-11-03T11:55:00Z", now)).toBe("5 min ago");
    expect(ago("2026-11-03T09:00:00Z", now)).toBe("3 h ago");
    expect(ago("2026-11-02T10:00:00Z", now)).toBe("yesterday");
    expect(ago("2026-10-30T12:00:00Z", now)).toBe("4 d ago");
  });
  it("gives the date after a week, with the year only when it differs", () => {
    expect(ago("2026-10-12T12:00:00Z", now)).toBe("12 Oct");
    expect(ago("2025-12-24T12:00:00Z", now)).toBe("24 Dec 2025");
  });
  it("says nothing for a missing or broken stamp, and never a time in the future", () => {
    expect(ago(undefined, now)).toBe("");
    expect(ago("not a date", now)).toBe("");
    expect(ago("2026-11-04T12:00:00Z", now)).toBe("just now");
  });
});

describe("folderOf", () => {
  it("is the folder that holds the project, for both slash styles", () => {
    expect(folderOf("D:\\work\\cases\\proj\\pipe3.stokes")).toBe("proj");
    expect(folderOf("/srv/work/runs/pipe3.stokes")).toBe("runs");
    expect(folderOf("pipe3.stokes")).toBe("");
  });
});

describe("recentCards", () => {
  it("puts existing projects first, keeps the order, and caps the count", () => {
    const rows = [{ n: 1, exists: false }, { n: 2, exists: true }, { n: 3, exists: true }];
    expect(recentCards(rows).map((r) => r.n)).toEqual([2, 3, 1]);
    expect(recentCards(rows, 2).map((r) => r.n)).toEqual([2, 3]);
  });
});

describe("monogram", () => {
  it("tells the recent projects apart in two characters", () => {
    expect(["pipe3", "pipe4", "cavity", "angledDuctLTS", "pitzDaily", "hot_room", "gui1"].map(monogram)).toEqual(["P3", "P4", "CA", "AD", "PD", "HR", "G1"]);
  });
  it("copes with odd names", () => {
    expect(monogram("")).toBe("");
    expect(monogram("x")).toBe("X");
    expect(monogram("42")).toBe("42");
  });
});
