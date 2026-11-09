/**
 * The Surface page's pure model (brief M-515): the staged surfaces against the staging limit, the
 * over-limit tone, and the feature-angle words.
 */
import { describe, expect, it } from "vitest";
import {
  ANGLE_EXPLANATION,
  BLOCKMESH_NOTE,
  NO_SURFACE_MESH_NOTE,
  angleLabel,
  appliesTo,
  stepperValue,
  surfaceViews,
} from "./surfaceModel";

const LIMIT = 500 * 1024 * 1024;

describe("surfaceViews", () => {
  it("bars a surface against the limit, clamped so over-limit still renders", () => {
    const views = surfaceViews([
      { name: "walls", triangles: 200_000, bytes: LIMIT / 4 },
      { name: "huge", triangles: 9_000_000, bytes: LIMIT + 1 },
    ]);
    expect(views[0].fraction).toBeCloseTo(0.25);
    expect(views[0].over).toBe(false);
    expect(views[1].fraction).toBe(1); // clamped
    expect(views[1].over).toBe(true);
  });

  it("an empty row list renders nothing", () => {
    expect(surfaceViews([])).toEqual([]);
  });
});

describe("the words", () => {
  it("the angle label carries its unit, and the explanation says what it decides", () => {
    expect(angleLabel(30)).toBe("Keep edges sharper than 30°");
    expect(angleLabel(45.5)).toBe("Keep edges sharper than 45.5°");
    expect(ANGLE_EXPLANATION).toContain("tessellation noise");
  });

  it("the two notes name what snappy does and does not have", () => {
    expect(NO_SURFACE_MESH_NOTE).toContain("no separate surface mesh");
    expect(BLOCKMESH_NOTE).toBe("Surfaces and sharp edges apply to snappyHexMesh and the cfMesh types only");
  });

  it("appliesTo is every shape-following mesher, not blockMesh", () => {
    expect(appliesTo("snappy")).toBe(true);
    expect(appliesTo("polyhex")).toBe(true);
    expect(appliesTo("cfmesh_tet")).toBe(true);
    expect(appliesTo("blockmesh")).toBe(false);
  });
});

describe("stepperValue", () => {
  it("the task-5 value: angle, surfaces, and the staged size", () => {
    expect(stepperValue(30, 2, 1.8 * 1048576)).toBe(
      "30° edges · 2 surfaces · 1.8 MiB to send",
    );
    expect(stepperValue(45, 3, 0)).toBe("45° edges · 3 surfaces");
  });
});