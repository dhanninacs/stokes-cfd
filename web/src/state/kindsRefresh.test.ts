import { describe, expect, it } from "vitest";
import { kindsRefreshWanted } from "./kindsRefresh";

describe("kindsRefreshWanted", () => {
  it("asks again when a project opens", () => {
    expect(kindsRefreshWanted("project.opened", {})).toBe(true);
  });

  it("asks again when the engine setting changes", () => {
    expect(kindsRefreshWanted("node.changed", { path: "/solver/settings", param: "engine" })).toBe(true);
  });

  it("leaves other settings and other nodes alone", () => {
    expect(kindsRefreshWanted("node.changed", { path: "/solver/settings", param: "cores" })).toBe(false);
    expect(kindsRefreshWanted("node.changed", { path: "/regions/fluid", param: "engine" })).toBe(false);
    expect(kindsRefreshWanted("node.added", { path: "/solver/settings" })).toBe(false);
  });

  it("survives data that is not an object", () => {
    expect(kindsRefreshWanted("node.changed", null)).toBe(false);
    expect(kindsRefreshWanted("node.changed", "x")).toBe(false);
  });
});
