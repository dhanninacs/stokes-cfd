/**
 * The Conditions panel's row rendering (ADR-0022 §9.1, T-048).
 *
 * The panel itself is React, but the two things worth pinning are testable without a renderer and are
 * what the ADR is specific about: the rows it builds come from `setupRows` (the *same* functions the
 * tree uses, so the two views cannot disagree — §9.2's "one tree stays"), and a legacy row is marked
 * rather than hidden. Rendering is covered by the review capture.
 */

import { describe, expect, it } from "vitest";

import { boundaryRow, regionRow } from "./setupRows";
import type { BoundaryEntry, RegionEntry } from "../api/types";

describe("the Conditions panel's rows", () => {
  it("builds a region row and its boundary rows from the same functions the tree uses", () => {
    const region: RegionEntry = {
      id: "n_r",
      path: "/regions/fluid",
      name: "fluid",
      kind: "fluid",
      material: { id: "n_m", name: "water", missing: false },
      tag: { id: "n_t", name: "bodies", path: "/geometry/tags/bodies", color: "#5aa452", visible: true },
      bodies: ["n_p1", "n_p2"],
      boundaries: ["n_b"],
      uncovered_faces: 0,
      stale: false,
      warnings: [],
      status: "ok",
    };
    const boundary: BoundaryEntry = {
      id: "n_b",
      path: "/regions/fluid/boundaries/inlet",
      name: "inlet",
      region: { id: "n_r", path: "/regions/fluid", name: "fluid" },
      kind: "velocity_inlet",
      label: "Velocity inlet",
      family: "inlet",
      tag: {
        id: "n_t2", name: "inlet", path: "/geometry/tags/inlet", color: "#d64f4f", visible: true,
        parts: 1, faces: 4, stale: false, missing: false,
      },
      primary: { name: "velocity", value: 2, display: "2 m/s" },
      warnings: [],
      status: "ok",
    };
    const regionRowBuilt = regionRow(region);
    const boundaryRowBuilt = boundaryRow(boundary);
    expect(regionRowBuilt.name).toBe("fluid");
    expect(regionRowBuilt.value).toBe("water");
    expect(boundaryRowBuilt.label).toBe("Velocity inlet");
    expect(boundaryRowBuilt.value).toBe("2 m/s");
    expect(boundaryRowBuilt.status).toBe("ok");
  });

  it("marks a legacy boundary so the panel shows it as a plain group, not an empty row", () => {
    const legacy = boundaryRow({
      id: "n_l",
      path: "/regions/fluid/boundaries/walls",
      name: "walls",
      region: { id: "n_r", path: "/regions/fluid", name: "fluid" },
      legacy: true,
    });
    expect(legacy.legacy).toBe(true);
    expect(legacy.label).toBe("Plain group");
    // No tag means no swatch and no eye, so the panel draws neither.
    expect(legacy.color).toBeNull();
    expect(legacy.visible).toBeNull();
  });

  it("surfaces a missing tag as failed, which is what the panel's red dot means", () => {
    const broken = boundaryRow({
      id: "n_b",
      path: "/regions/fluid/boundaries/inlet",
      name: "inlet",
      region: { id: "n_r", path: "/regions/fluid", name: "fluid" },
      kind: "velocity_inlet",
      label: "Velocity inlet",
      tag: {
        id: null, name: null, path: null, color: null, visible: null,
        parts: 0, faces: null, stale: false, missing: true,
      },
      primary: null,
      warnings: ["setup.tag_missing"],
      status: "failed",
    });
    expect(broken.status).toBe("failed");
    expect(broken.detail).toContain("tag is missing");
  });
});
