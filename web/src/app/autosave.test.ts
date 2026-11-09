import { describe, expect, it } from "vitest";
import type { ProjectInfo } from "../api/types";
import { autosaveWanted, recoveryWhen } from "./autosaveModel";

const project = (over: Partial<ProjectInfo> = {}): ProjectInfo => ({
  name: "elbow",
  path: "C:/p/elbow.stokes",
  display_units: "si",
  nodes: 10,
  can_undo: true,
  can_redo: false,
  dirty: true,
  ...over,
});

describe("autosaveWanted", () => {
  it("copies unsaved work of a project on disk while connected", () => {
    expect(autosaveWanted(project(), true)).toBe(true);
  });

  it("has nothing to keep for a clean project, no project, no folder or no server", () => {
    expect(autosaveWanted(project({ dirty: false }), true)).toBe(false);
    expect(autosaveWanted(null, true)).toBe(false);
    expect(autosaveWanted(project({ path: null }), true)).toBe(false);
    expect(autosaveWanted(project(), false)).toBe(false);
  });

  it("treats an older server's missing `dirty` as unsaved, as the Close prompt does", () => {
    expect(autosaveWanted(project({ dirty: undefined }), true)).toBe(true);
  });
});

describe("recoveryWhen", () => {
  it("names the time and the day", () => {
    const text = recoveryWhen("2026-11-08T14:05:00+00:00");
    expect(text).toMatch(/ on /);
  });

  it("gives back text that is not a date", () => {
    expect(recoveryWhen("earlier")).toBe("earlier");
  });
});
