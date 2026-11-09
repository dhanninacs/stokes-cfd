/**
 * The instanced glyph layer (ADR-0022 §8.1, T-046).
 *
 * The property §8.1 is really about is that the *instance count is the boundary count*, never the
 * face count, and that the glyphs live on their own layer so nothing else in the renderer sees them.
 * Both are checkable without a GPU by reading the instanced meshes back.
 */

import { describe, expect, it } from "vitest";
import * as THREE from "three";

import { GlyphLayer } from "./glyphLayer";
import type { Glyph } from "./glyphs";

function glyph(over: Partial<Glyph> = {}): Glyph {
  return {
    tag: "/regions/fluid/boundaries/inlet",
    position: [1, 2, 3],
    direction: [0, 0, -1],
    scale: 0.05,
    shape: "arrow_in",
    color: "#d64f4f",
    faces: 900,
    ...over,
  };
}

describe("GlyphLayer", () => {
  it("draws one instance per boundary, however many faces it covers", () => {
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph({ faces: 9000 }), glyph({ tag: "/b/two" })]);
    expect(layer.count()).toBe(2);
    layer.dispose();
  });

  it("puts every mesh on its own layer, so the outline and id passes never see them", () => {
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph()]);
    const mesh = host.getObjectByName("glyphs")?.children[0];
    expect(mesh?.layers.isEnabled(3)).toBe(true);
    expect(mesh?.layers.isEnabled(0)).toBe(false);
    layer.dispose();
  });

  it("enables the glyph layer on the camera it is given (T-046-fix-1)", () => {
    // This is the assertion the original suite was missing, and it is why 31 tests passed while
    // **nothing was ever visible**: three.js draws an object only when
    // `camera.layers.test(object.layers)` holds, and the main camera stayed on layer 0. Every
    // earlier test checked the layer *assignment*; none checked what a camera draws.
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    // A fresh camera sees layer 0 only, so it does not see the glyphs. This is the bug's precondition.
    expect(camera.layers.isEnabled(3)).toBe(false);

    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph()]);
    const mesh = host.getObjectByName("glyphs")?.children[0];
    expect(mesh).toBeDefined();

    // Constructing the layer is what makes the glyphs visible: no caller can forget a separate line.
    expect(camera.layers.test(mesh!.layers)).toBe(true);

    // The id, outline and shadow passes narrow the camera to their own layer and restore `mask`
    // afterwards, so they must still exclude the glyphs — and the glyph layer must survive the
    // restore, or the next frame would lose it.
    const mask = camera.layers.mask;
    camera.layers.set(1); // e.g. PART_LAYER
    expect(camera.layers.test(mesh!.layers)).toBe(false);
    camera.layers.mask = mask;
    expect(camera.layers.test(mesh!.layers)).toBe(true);

    layer.dispose();
  });

  it("groups by shape: one instanced mesh per shape, not per glyph", () => {
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([
      glyph({ shape: "arrow_in" }),
      glyph({ tag: "/b/2", shape: "arrow_in" }),
      glyph({ tag: "/b/3", shape: "plane" }),
    ]);
    const group = host.getObjectByName("glyphs")!;
    expect(group.children.map((c) => c.name).sort()).toEqual(["glyphs:arrow_in", "glyphs:plane"]);
    layer.dispose();
  });

  it("hides a shape's mesh when nothing of that shape is drawn", () => {
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph({ shape: "arrow_in" }), glyph({ shape: "plane" })]);
    layer.set([glyph({ shape: "plane" })]);
    expect(host.getObjectByName("glyphs:arrow_in")?.visible).toBe(false);
    expect(host.getObjectByName("glyphs:plane")?.visible).toBe(true);
    expect(layer.count()).toBe(1);
    layer.dispose();
  });

  it("draws nothing for an empty list, without disposing the geometry", () => {
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph()]);
    layer.set([]);
    expect(layer.count()).toBe(0);
    // Setting it again reuses the mesh rather than rebuilding it.
    layer.set([glyph()]);
    expect(layer.count()).toBe(1);
    layer.dispose();
  });

  it("orients a shape along its direction", () => {
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph({ direction: [0, 0, -1] })]);
    const mesh = host.getObjectByName("glyphs:arrow_in") as THREE.InstancedMesh;
    const matrix = new THREE.Matrix4();
    mesh.getMatrixAt(0, matrix);
    // The shape points along +Z in its own space, so a -Z direction is a 180-degree turn: the
    // transformed +Z must come out as -Z.
    const forward = new THREE.Vector3(0, 0, 1).applyMatrix4(new THREE.Matrix4().extractRotation(matrix));
    expect(forward.z).toBeCloseTo(-1, 6);
    layer.dispose();
  });

  it("applies the world scale it is given", () => {
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph({ scale: 0.25 })]);
    const mesh = host.getObjectByName("glyphs:arrow_in") as THREE.InstancedMesh;
    const matrix = new THREE.Matrix4();
    mesh.getMatrixAt(0, matrix);
    expect(new THREE.Vector3().setFromMatrixScale(matrix).x).toBeCloseTo(0.25, 6);
    layer.dispose();
  });

  it("is safe to dispose twice and ignores a set() after disposal", () => {
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph()]);
    layer.dispose();
    expect(() => layer.dispose()).not.toThrow();
    expect(() => layer.set([glyph()])).not.toThrow();
    expect(host.getObjectByName("glyphs")).toBeUndefined();
  });
});

describe("T-046-fix-2: a glyph on a hidden face is hidden", () => {
  it("depth-tests but does not write depth, and offsets off the face", () => {
    // The defect: the material had `depthTest: false`, so a glyph on the far side of an opaque part
    // was drawn through it — the "after" capture in the review showed the outlet arrow sitting on the
    // inlet face. The glyphs render in the same main pass as the parts, after them, so depth testing
    // is all that is needed: no per-frame work, no rebuild-time raycast, and it stays correct while
    // the camera orbits.
    const host = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const layer = new GlyphLayer(host, 3, camera);
    layer.set([glyph()]);
    const mesh = host.getObjectByName("glyphs")?.children[0] as THREE.Mesh | undefined;
    expect(mesh).toBeDefined();
    const material = mesh!.material as THREE.Material;

    expect(material.depthTest).toBe(true);
    // Not writing depth keeps the markers from occluding each other or the parts behind them.
    expect(material.depthWrite).toBe(false);
    // The anchor is ON the surface (T-046-fix-3), so the glyph's base would z-fight with the face it
    // sits on without a polygon offset.
    expect((material as THREE.MeshBasicMaterial).polygonOffset).toBe(true);
    // Translucent and wireframe parts write no depth, so the glyph shows through them with no extra
    // code — verified in the renderer, not here: `batch.ts` draws `look.glass` with no depth writes.
    layer.dispose();
  });
});
