/**
 * Tree rows for regions and boundaries (ADR-0022 D2.10, T-047).
 *
 * The rule under test is D2.10's: **status is derived, not written**. These tests pin the precedence
 * (a missing tag beats a stale one, which beats a warning), the value the row shows, the swatch and
 * eye a row has only when it actually proxies a tag, and that a legacy plain group is marked rather
 * than hidden. They use the real `boundary.list`/`region.list` shapes, so a change to the server's
 * output fails here rather than silently rendering an empty row.
 */

import { describe, expect, it } from "vitest";

import { boundaryRow, hasTagControls, regionRow, rowStatus } from "./setupRows";
import type { BoundaryEntry, RegionEntry, SetUpTagRef } from "../api/types";

/** The tag shape `boundary.list` sends for a healthy tag. */
function tag(over: Partial<SetUpTagRef> = {}): SetUpTagRef {
  return {
    id: "n_tag",
    name: "inlet",
    path: "/geometry/tags/inlet",
    color: "#d64f4f",
    visible: true,
    parts: 2,
    faces: 12,
    stale: false,
    missing: false,
    ...over,
  };
}

/** A typed boundary entry, as `boundary.list` gives it. */
function boundary(over: Partial<BoundaryEntry> = {}): BoundaryEntry {
  return {
    id: "n_b1",
    path: "/regions/fluid/boundaries/inlet",
    name: "inlet",
    region: { id: "n_r1", path: "/regions/fluid", name: "fluid" },
    kind: "velocity_inlet",
    label: "Velocity inlet",
    family: "inlet",
    tag: tag(),
    primary: { name: "velocity", value: 2, display: "2 m/s" },
    warnings: [],
    status: "ok",
    ...over,
  };
}

describe("rowStatus (D2.10)", () => {
  it("is ok for a healthy entry", () => {
    expect(rowStatus({ tag: tag(), status: "ok" })).toBe("ok");
  });

  it("is failed when the tag is missing", () => {
    expect(rowStatus({ tag: tag({ missing: true }), status: "failed" })).toBe("failed");
  });

  it("is failed for an invalid kind, which the server reports as a finding", () => {
    expect(rowStatus({ tag: tag(), status: "error" })).toBe("failed");
  });

  it("is stale when the tag's ids refer to an earlier B-rep", () => {
    expect(rowStatus({ tag: tag({ stale: true }), status: "stale" })).toBe("stale");
  });

  it("is stale for a region's own stale flag (region.list carries it on the entry)", () => {
    expect(rowStatus({ status: "stale", stale: true })).toBe("stale");
  });

  it("is warning for a finding", () => {
    expect(rowStatus({ tag: tag(), status: "warning" })).toBe("warning");
  });

  it("orders failed above stale above warning", () => {
    // A missing tag with a warning is still failed: a missing tag is the worse problem.
    expect(rowStatus({ tag: tag({ missing: true }), status: "warning" })).toBe("failed");
    expect(rowStatus({ tag: tag({ stale: true }), status: "warning" })).toBe("stale");
  });

  it("says ok when the server gives no status at all", () => {
    expect(rowStatus({ tag: tag() })).toBe("ok");
    expect(rowStatus({})).toBe("ok");
  });
});

describe("boundaryRow", () => {
  it("shows the kind label, the primary value in display units and the tag's swatch", () => {
    const row = boundaryRow(boundary());
    expect(row.label).toBe("Velocity inlet");
    expect(row.value).toBe("2 m/s");
    expect(row.color).toBe("#d64f4f");
    expect(row.visible).toBe(true);
    expect(row.status).toBe("ok");
  });

  it("counts the parts and faces from the tag, in the detail", () => {
    expect(boundaryRow(boundary()).detail).toContain("2 parts, 12 faces");
    expect(boundaryRow(boundary({ tag: tag({ parts: 1, faces: 1 }) })).detail).toContain("1 part, 1 face");
  });

  it("says its tag is missing rather than showing a blank row", () => {
    const row = boundaryRow(
      boundary({
        tag: { id: null, name: null, path: null, color: null, visible: null, parts: 0, faces: null, stale: false, missing: true },
        primary: null,
        warnings: ["setup.tag_missing"],
        status: "failed",
      }),
    );
    expect(row.status).toBe("failed");
    expect(row.detail).toContain("tag is missing");
    expect(row.value).toBe("");
  });

  it("marks a legacy plain group instead of hiding it", () => {
    const row = boundaryRow({
      id: "n_l1",
      path: "/regions/fluid/boundaries/walls",
      name: "walls",
      region: { id: "n_r1", path: "/regions/fluid", name: "fluid" },
      legacy: true,
    });
    expect(row.legacy).toBe(true);
    expect(row.label).toBe("Plain group");
    expect(row.detail).toContain("plain group");
    expect(row.color).toBeNull();
  });

  it("offers no swatch or eye for a row without a tag", () => {
    const legacy = boundaryRow({
      id: "n_l1",
      path: "/p",
      name: "walls",
      region: { id: "n_r", path: "/regions/fluid", name: "fluid" },
      legacy: true,
    });
    expect(hasTagControls(legacy)).toBe(false);
    expect(hasTagControls(boundaryRow(boundary()))).toBe(true);
  });

  it("falls back to a generic label when a typed entry has no kind label", () => {
    expect(boundaryRow(boundary({ label: undefined, kind: undefined })).label).toBe("Boundary");
  });
});

describe("regionRow", () => {
  it("shows the material, the body count and the tag's swatch", () => {
    const entry: RegionEntry = {
      id: "n_r1",
      path: "/regions/fluid",
      name: "fluid",
      kind: "fluid",
      material: { id: "n_m1", name: "water", missing: false },
      tag: { id: "n_t", name: "fluid_bodies", path: "/geometry/tags/fluid", color: "#5aa452", visible: true },
      bodies: ["n_p1", "n_p2"],
      boundaries: ["n_b1"],
      uncovered_faces: 12,
      stale: false,
      warnings: [],
      status: "ok",
    };
    const row = regionRow(entry);
    expect(row.label).toBe("Region");
    expect(row.value).toBe("water");
    expect(row.color).toBe("#5aa452");
    expect(row.detail).toContain("2 bodies");
    expect(row.detail).toContain("water");
  });

  it("says 'no material' in the detail when the region has none", () => {
    const row = regionRow({ id: "n_r", path: "/r", name: "fluid", bodies: ["n_p1"], status: "warning" });
    expect(row.value).toBe("");
    expect(row.detail).toContain("no material");
    expect(row.detail).toContain("1 body");
    expect(row.status).toBe("warning");
  });

  it("carries a stale region through from its own flag", () => {
    expect(regionRow({ id: "n_r", path: "/r", name: "fluid", stale: true, status: "stale" }).status).toBe("stale");
  });

  it("marks a legacy region as a plain group", () => {
    const row = regionRow({ id: "n_r", path: "/regions/fluid", name: "fluid", legacy: true });
    expect(row.legacy).toBe(true);
    expect(row.label).toBe("Plain group");
    expect(row.detail).toContain("0 bodies");
  });
});
