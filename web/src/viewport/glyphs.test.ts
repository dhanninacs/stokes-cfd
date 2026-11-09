/**
 * Boundary glyph placement (ADR-0022 §8.1, T-046).
 *
 * The rules worth pinning are the ones that are wrong invisibly: the **inward/outward** flip (a
 * missed flip makes every inlet read as an outlet), the **constant on-screen size** (which must
 * depend on the camera distance and the viewport, and be DPI-honest), the shape a kind resolves to,
 * and the two anchors that must not be drawn at all (no faces, or behind the camera).
 */

import { describe, expect, it } from "vitest";

import {
  GLYPH_MIN_FRACTION,
  GLYPH_PIXELS,
  glyphFlow,
  glyphScale,
  glyphShape,
  glyphShrink,
  glyphsFor,
  type TagAnchor,
} from "./glyphs";
import type { ConditionKind } from "../api/types";

function kind(over: Partial<ConditionKind> & { id: string; family: string }): ConditionKind {
  return {
    label: over.id,
    fields: [],
    needs: {},
    color: "#d64f4f",
    glyph: "",
    arrives: null,
    available: true,
    note: "",
    params: [],
    ...over,
  };
}

const INLET = kind({ id: "velocity_inlet", family: "inlet", glyph: "arrow_in" });
const OUTLET = kind({ id: "pressure_outlet", family: "outlet", glyph: "arrow_out", color: "#3b82c4" });
const WALL = kind({ id: "wall", family: "wall", glyph: "plane", color: "#7f8c99" });

function anchor(over: Partial<TagAnchor> = {}): TagAnchor {
  return {
    tag: "/regions/fluid/boundaries/inlet",
    centroid: [1, 2, 3],
    normal: [0, 0, 1],
    area: 1,
    extent: 1,
    faces: 4,
    parts: 1,
    partial: false,
    ...over,
  };
}

describe("glyphFlow", () => {
  it("reads the family: inlets flow in, outlets out", () => {
    expect(glyphFlow({ id: "velocity_inlet", family: "inlet" })).toBe("in");
    expect(glyphFlow({ id: "pressure_outlet", family: "outlet" })).toBe("out");
  });

  it("has no direction for a wall or a symmetry", () => {
    expect(glyphFlow({ id: "wall", family: "wall" })).toBe("none");
    expect(glyphFlow({ id: "symmetry", family: "constraint" })).toBe("none");
  });

  it("falls back to the id when the family is unfamiliar", () => {
    expect(glyphFlow({ id: "suction_inlet", family: "custom" })).toBe("in");
    expect(glyphFlow({ id: "vent_outlet", family: "custom" })).toBe("out");
  });
});

describe("glyphShape", () => {
  it("uses the catalogue's own glyph when it names a known shape", () => {
    expect(glyphShape(INLET)).toBe("arrow_in");
    expect(glyphShape(kind({ id: "x", family: "wall", glyph: "pair" }))).toBe("pair");
  });

  it("derives the arrow from the flow when the catalogue names nothing", () => {
    expect(glyphShape(kind({ id: "velocity_inlet", family: "inlet", glyph: "" }))).toBe("arrow_in");
    expect(glyphShape(kind({ id: "pressure_outlet", family: "outlet", glyph: "" }))).toBe("arrow_out");
    expect(glyphShape(kind({ id: "wall", family: "wall", glyph: "" }))).toBe("plane");
  });

  it("falls back to a visible shape for a glyph this build does not know", () => {
    expect(glyphShape(kind({ id: "x", family: "interface", glyph: "some_future_shape" }))).toBe("diamond");
  });

  it("honours the catalogue's literal 'none', which the wall kind uses", () => {
    // Caught against the real catalogue: `wall` carries glyph "none". Treating that as an unknown
    // shape would draw a diamond on every wall.
    expect(glyphShape(kind({ id: "wall", family: "wall", glyph: "none" }))).toBe("none");
  });
});

describe("glyphScale", () => {
  it("keeps the glyph GLYPH_PIXELS tall on screen", () => {
    // A 60-degree fov, 600 px tall viewport, 10 m away: world height is 2*10*tan(30) = 11.547 m,
    // so one pixel is 11.547/600 m and the glyph is 28 of those.
    const scale = glyphScale(10, 60, 600);
    expect(scale).toBeCloseTo((2 * 10 * Math.tan(Math.PI / 6) * GLYPH_PIXELS) / 600, 9);
  });

  it("grows with distance, so the on-screen size does not", () => {
    expect(glyphScale(20, 60, 600)).toBeCloseTo(2 * glyphScale(10, 60, 600), 9);
  });

  it("is DPI-honest: twice the pixels is half the world size per pixel", () => {
    expect(glyphScale(10, 60, 1200)).toBeCloseTo(glyphScale(10, 60, 600) / 2, 9);
  });

  it("is zero for a degenerate camera or viewport rather than infinite", () => {
    expect(glyphScale(0, 60, 600)).toBe(0);
    expect(glyphScale(10, 0, 600)).toBe(0);
    expect(glyphScale(10, 60, 0)).toBe(0);
    expect(glyphScale(-5, 60, 600)).toBe(0);
  });
});

describe("glyphsFor", () => {
  const kindOf = (tag: string) => (tag.includes("inlet") ? INLET : tag.includes("outlet") ? OUTLET : WALL);
  // Facing the camera square-on, 10 m away: the shape every pre-fix-4 test assumed. `cosAngle: 1`
  // means the surface's normal points straight at the camera, so `glyphShrink` returns 1 and the
  // size is the plain distance scale.
  const at10 = () => ({ distance: 10, cosAngle: 1 });

  it("places a glyph at the anchor's centroid with the tag's colour", () => {
    const [glyph] = glyphsFor([anchor()], kindOf, at10, 60, 600);
    expect(glyph.position).toEqual([1, 2, 3]);
    expect(glyph.color).toBe("#d64f4f");
  });

  it("draws at the anchor's on-face position when the server gives one", () => {
    // An L-shaped plane's centroid lies in the empty corner; the server sends a point on the face.
    const [glyph] = glyphsFor([anchor({ position: [4, 5, 6] })], kindOf, at10, 60, 600);
    expect(glyph.position).toEqual([4, 5, 6]);
  });

  it("points an inlet INWARD, against the surface normal", () => {
    // The anchor normal is [0,0,1]; an inlet's arrow must be [0,0,-1] or it reads as an outlet.
    const [inlet] = glyphsFor([anchor()], kindOf, at10, 60, 600);
    expect(inlet.direction).toEqual([-0, -0, -1]);
  });

  it("points an outlet outward, along the surface normal", () => {
    const [outlet] = glyphsFor([anchor({ tag: "/b/outlet" })], kindOf, at10, 60, 600);
    expect(outlet.direction).toEqual([0, 0, 1]);
  });

  it("has no direction to flip on a wall", () => {
    const [wall] = glyphsFor([anchor({ tag: "/b/walls" })], kindOf, at10, 60, 600);
    expect(wall.direction).toEqual([0, 0, 1]);
    expect(wall.shape).toBe("plane");
  });

  it("normalises a non-unit normal before flipping", () => {
    const [glyph] = glyphsFor([anchor({ normal: [0, 0, 5] })], kindOf, at10, 60, 600);
    expect(glyph.direction[2]).toBeCloseTo(-1, 9);
  });

  it("keeps the scale constant on screen via the camera distance", () => {
    // Square-on (`cosAngle: 1`) so this measures the DISTANCE rule alone; the angle's floor is
    // `glyphShrink`'s business and is tested there.
    const near = glyphsFor([anchor()], kindOf, () => ({ distance: 5, cosAngle: 1 }), 60, 600)[0];
    const far = glyphsFor([anchor()], kindOf, () => ({ distance: 50, cosAngle: 1 }), 60, 600)[0];
    expect(far.scale).toBeCloseTo(near.scale * 10, 9);
  });

  it("draws nothing on a canvas with no height (a hidden window), not a screen-filling glyph", () => {
    expect(glyphsFor([anchor()], kindOf, at10, 60, 0)).toEqual([]);
  });

  it("drops an anchor with no faces rather than floating a glyph at the origin", () => {
    expect(glyphsFor([anchor({ faces: 0 })], kindOf, at10, 60, 600)).toEqual([]);
  });

  it("drops an anchor behind the camera", () => {
    expect(glyphsFor([anchor()], kindOf, () => ({ distance: -1, cosAngle: 1 }), 60, 600)).toEqual([]);
  });

  it("keeps a partial anchor: the known faces still say where the boundary is", () => {
    const glyphs = glyphsFor([anchor({ partial: true })], kindOf, at10, 60, 600);
    expect(glyphs).toHaveLength(1);
  });

  it("draws a directionless plane for a tag no boundary uses", () => {
    const [glyph] = glyphsFor([anchor()], () => null, at10, 60, 600);
    expect(glyph.shape).toBe("plane");
    expect(glyph.direction).toEqual([0, 0, 1]);
  });

  it("draws no glyph for a kind the catalogue gives none (a wall)", () => {
    const wall = (tag: string) => kind({ id: "wall", family: "wall", glyph: "none" });
    expect(glyphsFor([anchor({ tag: "/b/walls" })], wall, at10, 60, 600)).toEqual([]);
  });

  it("draws one glyph per anchor, whatever the face count", () => {
    const many = [anchor({ tag: "/b/a" }), anchor({ tag: "/b/b" }), anchor({ tag: "/b/c", faces: 9000 })];
    expect(glyphsFor(many, kindOf, at10, 60, 600)).toHaveLength(3);
  });
});


describe("glyphShrink: T-046-fix-4's minimum on-screen footprint", () => {
  it("draws a glyph on a surface facing the camera at full size", () => {
    expect(glyphShrink(1)).toBeCloseTo(1, 12);
  });

  it("floors a glyph on a surface seen EXACTLY edge-on", () => {
    // The defect: a face whose normal is perpendicular to the view direction projects to a sliver, so
    // the glyph shrank with it and the boundary the user was looking straight at became unfindable.
    expect(glyphShrink(0)).toBeCloseTo(GLYPH_MIN_FRACTION, 12);
  });

  it("gives a surface seen from BEHIND the same floor as one edge-on", () => {
    // `cosAngle` is negative when the outward normal points away from the camera. That is the same
    // foreshortening, not a reason to draw the glyph larger or smaller than edge-on, so the rule takes
    // the magnitude. A sign slip here would make a back-facing boundary the biggest thing on screen.
    expect(glyphShrink(-1)).toBeCloseTo(1, 12);
    expect(glyphShrink(-0.5)).toBeCloseTo(glyphShrink(0.5), 12);
  });

  it("is monotone in the angle, so turning the model never makes a glyph jump", () => {
    // A rule with a discontinuity would flicker as the camera orbits, which reads as a rendering bug.
    let last = -1;
    for (let c = 0; c <= 1.0001; c += 0.05) {
      const s = glyphShrink(c);
      expect(s).toBeGreaterThanOrEqual(last);
      expect(s).toBeLessThanOrEqual(1);
      last = s;
    }
    expect(last).toBeCloseTo(1, 6);
  });

  it("never falls below the floor, whatever the angle", () => {
    for (const c of [-2, -1, -0.3, 0, 0.3, 1, 2]) {
      expect(glyphShrink(c)).toBeGreaterThanOrEqual(GLYPH_MIN_FRACTION);
    }
  });

  it("takes the floor for a NON-FINITE cosine rather than propagating NaN", () => {
    // A degenerate normal, or a camera exactly at the anchor, can produce NaN. A NaN scale reaches an
    // instance matrix and hides the glyph entirely — silently, because nothing throws.
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      expect(Number.isFinite(glyphShrink(bad))).toBe(true);
      expect(glyphShrink(bad)).toBeCloseTo(GLYPH_MIN_FRACTION, 12);
    }
  });

  it("leaves the on-screen PIXEL length independent of the view angle", () => {
    // **The pure form of what fix-4 asks for.** A glyph's world size renders as a pixel size
    // proportional to it, so the pixel length along the view axis is `scale / metresPerPixel`. This
    // asserts that figure across the whole angle range: it may shrink to the floor, never to nothing.
    const fov = 60;
    const height = 600;
    const distance = 10;
    const metresPerPixel =
      (2 * distance * Math.tan((fov * Math.PI) / 360)) / height;
    const plain = glyphScale(distance, fov, height) / metresPerPixel;
    expect(plain).toBeCloseTo(GLYPH_PIXELS, 6);
    for (const cos of [1, 0.75, 0.5, 0.25, 0]) {
      const pixels = (glyphScale(distance, fov, height) * glyphShrink(cos)) / metresPerPixel;
      expect(pixels).toBeGreaterThanOrEqual(GLYPH_PIXELS * GLYPH_MIN_FRACTION - 1e-9);
      expect(pixels).toBeLessThanOrEqual(GLYPH_PIXELS + 1e-9);
    }
  });

  it("keeps the ON-SCREEN size at the floor regardless of the viewport height", () => {
    // **This test used to assert `glyphShrink(0.3) === glyphShrink(0.3)`** — which cannot fail, and
    // was pointed out by a verification audit that read the file. A test that cannot fail is worse
    // than none, because it reads as coverage.
    //
    // What the claim actually is: the floor is a FRACTION of the distance-scaled size, so the same
    // angle gives the same PIXEL size at any viewport height. That needs both the height the size is
    // computed from and the height the pixels are counted in, which is what `metresPerPixel` is.
    const fov = 60;
    const distance = 8;
    const angle = 0.3;
    const pixelsAt = (height: number): number => {
      const metresPerPixel = (2 * distance * Math.tan((fov * Math.PI) / 360)) / height;
      return (glyphScale(distance, fov, height) * glyphShrink(angle)) / metresPerPixel;
    };
    // The world size doubles with the viewport height, and so do the pixels it covers: the ratio is
    // what has to be stable, which a same-call comparison could never show.
    const short = pixelsAt(600);
    const tall = pixelsAt(1200);
    expect(tall).toBeCloseTo(short, 6);
    // And it is the FLOORED size, strictly between the floor and full size, so this is not a test
    // that would pass with `glyphShrink` removed.
    expect(short).toBeGreaterThan(GLYPH_PIXELS * GLYPH_MIN_FRACTION - 1e-9);
    expect(short).toBeLessThan(GLYPH_PIXELS);
  });
});

describe("glyphsFor: the view angle reaches the size", () => {
  const kindOf = (tag: string) => (tag.includes("inlet") ? INLET : tag.includes("outlet") ? OUTLET : WALL);

  it("shrinks a glyph on an edge-on face, but not below the floor", () => {
    const facing = glyphsFor([anchor()], kindOf, () => ({ distance: 10, cosAngle: 1 }), 60, 600);
    const edgeOn = glyphsFor([anchor()], kindOf, () => ({ distance: 10, cosAngle: 0 }), 60, 600);
    expect(edgeOn[0].scale).toBeLessThan(facing[0].scale);
    expect(edgeOn[0].scale).toBeCloseTo(facing[0].scale * GLYPH_MIN_FRACTION, 9);
  });

  it("keeps the position and direction whatever the angle", () => {
    // The floor changes how big a glyph is drawn, never where it is or which way it points.
    const [g] = glyphsFor([anchor()], kindOf, () => ({ distance: 10, cosAngle: 0 }), 60, 600);
    expect(g.position).toEqual([1, 2, 3]);
    expect(g.direction).toEqual([-0, -0, -1]);
  });
});
