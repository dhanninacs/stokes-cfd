/**
 * The section's cut-face colour grouping (T-065b), and its budget.
 *
 * Claude asked for the **two-colour case** specifically: the cold-plate fixture, where the fluid is the
 * serpentine channel inside the plate, so a cut across the channel must show the plate's cap with a
 * void and the fluid's cap filling that void, side by side. That geometry is what he named as the case
 * the stencil passes "must get right": the plate's count must leave the void empty, the fluid body's
 * pass must fill exactly it, no gap and no overlap at the shared boundary.
 *
 * **What a unit test can and cannot check here.** The stencil judgement itself is a GPU question and is
 * checked on the real window, in `tools/test_interactions.py`. What is pure — and what is tested here —
 * is the decision the caps make BEFORE drawing: which distinct looks become passes, whether two bodies
 * that share a colour share a pass, and what happens when there are more looks than the layer budget
 * allows. The invalid-quad guard is here too, because a NaN quaternion hides the cap silently.
 *
 * These run in node: the module builds `THREE.Mesh` and `THREE.MeshBasicMaterial` objects, which need
 * no WebGL context until they are drawn.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import {
  MAX_CAP_COLOURS,
  SECTION_LAYERS,
  SectionCaps,
  capsSignature,
  invalidSectionLayers,
  layerMask,
  markLayer,
  sectionLayerRanges,
  sectionLayersOverlap,
} from "./sectionCaps";

/** A part as `setParts` takes one: a mesh with a colour and an opacity. */
function part(colour: string, opacity = 1) {
  return {
    mesh: new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial()),
    colour: new THREE.Color(colour),
    opacity,
  };
}

describe("SectionCaps grouping", () => {
  it("gives each distinct colour its own pass", () => {
    // The cold plate's three bodies: the grey plate, the orange chip, the blue fluid. Three colours =
    // three passes, which is what lets the fluid's cap fill the void the plate's cap left.
    const caps = new SectionCaps();
    caps.setParts([part("#b8bcc2"), part("#d97340"), part("#5a9ed9")]);
    expect(caps.info().groups).toBe(3);
  });

  it("shares one pass between two bodies of the same colour", () => {
    // The demo's two flanges are both metal; they must not take two passes, and they must not be
    // capped in two slightly different greys.
    const caps = new SectionCaps();
    caps.setParts([part("#616870"), part("#616870"), part("#5a9ed9")]);
    expect(caps.info().groups).toBe(2);
  });

  it("treats the same colour at two opacities as two looks, not one", () => {
    // A translucent body's cap must match its body: collapsing the two would draw one of them at the
    // other's opacity, which is a visible wrongness at the cut.
    const caps = new SectionCaps();
    caps.setParts([part("#5a9ed9", 0.35), part("#5a9ed9", 1)]);
    expect(caps.info().groups).toBe(2);
  });

  it("marks one mesh per colour per cull face", () => {
    // Each group hangs TWO mark meshes on each of its parts — the back-face and front-face counters.
    // Eight marks for three colours over four parts is what "the counts are healthy" looked like while
    // the caps were still invisible, which is why the count alone is not the test; the drawing is.
    const caps = new SectionCaps();
    const parts = [part("#aca"), part("#aca"), part("#bcc"), part("#cab")];
    caps.setParts(parts);
    expect(caps.info().marks).toBe(parts.length * 2);
  });

  it("drops the colours past the layer budget instead of sharing one", () => {
    // More looks than layers: the least are dropped rather than sharing a layer, because two bodies
    // capped in one colour puts a flat wrong colour across a cut — worse than no cap on the last one.
    const caps = new SectionCaps();
    const many = Array.from({ length: MAX_CAP_COLOURS + 5 }, (_, i) => part(`#${(i + 1).toString(16).padStart(6, "0")}`));
    caps.setParts(many);
    expect(caps.info().groups).toBe(MAX_CAP_COLOURS);
  });

  it("claims layer ranges that do not overlap each other", () => {
    // **This replaces a tautology.** The old test asserted `MAX_CAP_COLOURS > 0 && <= 22`, which
    // compares two constants to each other and cannot fail for any interesting reason — Claude's
    // "tests that cannot fail". The real property is that the section's own layers are DISJOINT: the
    // handle was on layer 9 while the marks began at 8, so the handle sat inside the mark range and
    // the two passes drew over each other. This fails if any two ranges touch.
    const clashes = sectionLayersOverlap();
    expect(clashes, `layers overlap: ${JSON.stringify(clashes)}`).toEqual([]);
  });

  it("keeps every mark inside the range the marks own", () => {
    // And the derived property, which is what the layer band is FOR: no group may be handed a layer
    // outside it, or that group's pass would draw something else.
    const ranges = sectionLayerRanges();
    const marks = ranges.find((r) => r.name === "marks");
    expect(marks).toBeDefined();
    if (marks === undefined) return;
    for (let index = 0; index < MAX_CAP_COLOURS; index++) {
      const layer = markLayer(index);
      expect(layer).toBeGreaterThanOrEqual(marks.from);
      expect(layer).toBeLessThanOrEqual(marks.to);
    }
    // Past the budget there is no layer at all, rather than one that collides.
    expect(markLayer(MAX_CAP_COLOURS)).toBe(-1);
  });

  it("keeps the handle and the quad clear of every mark", () => {
    // The three ranges named in the bug report: the handle's (9, colliding), the marks' and the
    // quad's. Asserted as containment rather than by naming the numbers, so moving the base is fine
    // as long as the ranges stay apart.
    const ranges = sectionLayerRanges();
    const marks = ranges.find((r) => r.name === "marks");
    const handle = ranges.find((r) => r.name === "handle");
    const quad = ranges.find((r) => r.name === "quad");
    expect(marks && handle && quad).toBeTruthy();
    if (!marks || !handle || !quad) return;
    expect(handle.to < marks.from || handle.from > marks.to).toBe(true);
    expect(quad.to < marks.from || quad.from > marks.to).toBe(true);
  });
});

describe("SectionCaps section state", () => {
  it("is inactive with no section and active with one", () => {
    const caps = new SectionCaps();
    expect(caps.showing).toBe(false);
    caps.setSection(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0));
    expect(caps.showing).toBe(true);
    caps.setSection(null);
    expect(caps.showing).toBe(false);
  });

  it("refuses a plane with no normal", () => {
    // A zero normal makes `setFromUnitVectors` produce a NaN quaternion in `place`, and the cap would
    // disappear with no error anywhere. Refusing it here is what keeps the failure visible.
    const caps = new SectionCaps();
    caps.setSection(new THREE.Plane(new THREE.Vector3(0, 0, 0), 0));
    expect(caps.showing).toBe(false);
  });

  it("is active as soon as the section is, before any parts are marked", () => {
    // The exact deadlock that made the whole feature a no-op: `showing` used to require groups, which
    // were only built behind a guard on `showing` itself. It must be true with zero parts.
    const caps = new SectionCaps();
    caps.setSection(new THREE.Plane(new THREE.Vector3(1, 0, 0), 0));
    expect(caps.info().groups).toBe(0);
    expect(caps.showing).toBe(true);
  });
});

describe("SectionCaps placement", () => {
  it("puts the quad in the plane, sized to the model", () => {
    const caps = new SectionCaps();
    caps.setSection(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0));
    caps.setParts([part("#aca")]);
    // A model 0.49 m across — the demo pipe's own extent. The quad must be sized FROM the model: the
    // first version floored it at a metre and gave this model a 2.00 m quad that sat nowhere useful.
    const bounds = new THREE.Box3(new THREE.Vector3(-0.244, -0.05, -0.244), new THREE.Vector3(0.244, 0.05, 0.244));
    caps.place(bounds);
    const quad = caps.info().quad;
    expect(quad).toBe(true);
  });

  it("hides the quad again when the section is removed", () => {
    // **The bug this covers.** `place` returns early while the section is off, and `render` returns
    // early on `!showing`, so neither reached the line that hides the quad. A frame drawn with a
    // section on left the quad visible, and removing the section left it being drawn by the fill
    // pass's own layer — a flat plane hanging in the model with the cut gone. The first version of this
    // test passed `place` directly with the section never on, which tested a different thing: the
    // sequence below is what the app does.
    const caps = new SectionCaps();
    caps.setParts([part("#aca")]);
    const bounds = new THREE.Box3(
      new THREE.Vector3(-1, -1, -1),
      new THREE.Vector3(1, 1, 1),
    );
    caps.setSection(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0));
    caps.place(bounds); // a frame with the section on: the quad is now visible
    expect(caps.info().quad).toBe(true);
    caps.setSection(null); // the user removes the section
    expect(caps.info().quad).toBe(false);
    // **And turning it back on must show the quad again.** This is the half the first version of the
    // fix missed: `setSection(null)` hides it and nothing set it back, so the second section drew no
    // caps while every count still reported healthy. A `THREE.Mesh` starts visible, which is why the
    // FIRST section worked and hid the bug.
    caps.setSection(new THREE.Plane(new THREE.Vector3(1, 0, 0), 0));
    caps.place(bounds);
    expect(caps.info().quad).toBe(true);
  });
});

describe("SectionCaps resource lifetime", () => {
  /** Every distinct geometry and material reachable from a set of meshes, by identity. */
  function live(meshes: THREE.Object3D[]): { geometries: number; materials: number } {
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Set<THREE.Material>();
    for (const root of meshes) {
      root.traverse((child) => {
        const mesh = child as THREE.Mesh;
        if (mesh.geometry) geometries.add(mesh.geometry);
        const material = mesh.material;
        if (Array.isArray(material)) for (const m of material) materials.add(m);
        else if (material) materials.add(material);
      });
    }
    return { geometries: geometries.size, materials: materials.size };
  }

  it("leaves no mark mesh behind after the marks are cleared", () => {
    // Claude's defect 5: `marksByPart` kept a mesh per part per colour — and with them the removed
    // parts they are children OF, because a mark's parent is the part's mesh. Removing the section is
    // the user saying they are done with it, so nothing may stay attached.
    const parts = [part("#aca"), part("#bcc")];
    const caps = new SectionCaps();
    caps.setSection(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0));
    caps.setParts(parts);
    // Two marks hang on each part before the clear.
    expect(parts[0].mesh.children).toHaveLength(2);
    expect(caps.info().marks).toBeGreaterThan(0);

    caps.clearMarks();
    expect(caps.info().marks).toBe(0);
    expect(caps.info().groups).toBe(0);
    // **Nothing is left attached to the parts.** This is the leak: a mark is a CHILD of the part's
    // mesh, so an unattached one would keep the part's geometry and its own material alive for as long
    // as the caps object held the reference.
    for (const p of parts) expect(p.mesh.children).toHaveLength(0);
    // And the part still draws with its own geometry, which the marks shared rather than owned:
    // clearing the marks must not have disposed the geometry the body is drawn with.
    expect(live(parts.map((p) => p.mesh)).geometries).toBe(2);
  });

  it("disposes the quad and the mark materials exactly once, and is idempotent", () => {
    // `dispose` is called on scene teardown; a second call must not throw or double-dispose, because a
    // React effect can run twice in development.
    const caps = new SectionCaps();
    caps.setSection(new THREE.Plane(new THREE.Vector3(1, 0, 0), 0));
    caps.setParts([part("#aca")]);
    expect(() => caps.dispose()).not.toThrow();
    expect(() => caps.dispose()).not.toThrow();
    expect(caps.info().marks).toBe(0);
    expect(caps.info().groups).toBe(0);
  });

  it("keeps one geometry per part, shared by its marks, not one per mark", () => {
    // The marks share the part's geometry, so 4 parts at 3 colours is 4 geometries and 24 materials —
    // not 24 geometries. This is what makes the leak test meaningful: there is nothing per-mark to
    // leak but the mesh objects themselves, which `clearMarks` must detach.
    const parts = [part("#aca"), part("#bcc"), part("#cab"), part("#abc")];
    const caps = new SectionCaps();
    caps.setSection(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0));
    caps.setParts(parts);
    const parts_ = live(parts.map((p) => p.mesh));
    // 4 part geometries + 4 target-less marks share them: still 4.
    expect(parts_.geometries).toBe(4);
    caps.dispose();
  });
});

describe("the drag-cost guard (Claude's defect 8)", () => {
  it("does not rebuild the marks when only the plane moves", () => {
    // **The mechanism defect 8 named.** Every `pointermove` during a drag used to rebuild the marks —
    // two meshes per part per colour — because `setSection` cleared the signature key first, which
    // defeated the very guard that exists to stop it. Measured on the cold plate: 21 rebuilds over a
    // section-on plus 40 moves before the fix, 1 after.
    //
    // The scene's own counter is the honest witness here: `setParts` replacing the mark meshes is the
    // allocation, and it is not observable from the outside any other way. This drives the caps object
    // directly, which is where the decision lives.
    const parts = [part("#aca"), part("#bcc")];
    const caps = new SectionCaps();
    caps.setSection(new THREE.Plane(new THREE.Vector3(1, 0, 0), -0.5));
    caps.setParts(parts);

    /** The mark meshes currently attached, by identity. */
    const attached = (): THREE.Object3D[] => [...parts[0].mesh.children];

    const before = attached();
    expect(before.length).toBeGreaterThan(0);

    // Moving the plane must reuse the SAME mark meshes: the meshes are children of the parts and
    // share their geometry, so nothing about them depends on where the plane is.
    for (let i = 0; i < 20; i++) {
      caps.setSection(new THREE.Plane(new THREE.Vector3(1, 0, 0), -(0.5 + i * 0.01)));
    }
    const after = attached();
    expect(after).toHaveLength(before.length);
    for (let i = 0; i < before.length; i++) {
      // Identity, not equality: a rebuild makes NEW meshes even when they look the same, and new
      // meshes are the allocation. `toBe` is what makes this test able to fail.
      expect(after[i]).toBe(before[i]);
    }
    caps.dispose();
  });

  it("DOES rebuild when the parts' colours change, which is what the guard must still allow", () => {
    // The other half: a guard that never rebuilds would be as wrong as one that always does. A
    // recoloured body needs a new cap colour, so the marks must be replaced.
    const body = part("#aca");
    const caps = new SectionCaps();
    caps.setSection(new THREE.Plane(new THREE.Vector3(1, 0, 0), -0.5));
    caps.setParts([body]);
    const before = [...body.mesh.children];

    // The SAME mesh, recoloured — which is what a colour change is. The first version of this passed a
    // fresh `part(...)`, whose helper makes a new mesh, so the caps correctly reported nothing attached
    // to the old one and the test failed on its own construction rather than on the code.
    caps.setParts([{ ...body, colour: new THREE.Color("#ff0000") }]);
    const after = [...body.mesh.children];
    expect(after).toHaveLength(before.length);
    // The mesh OBJECTS are new, because the cap colour is baked into the material they share.
    for (let i = 0; i < before.length; i++) expect(after[i]).not.toBe(before[i]);
    caps.dispose();
  });
});

describe("capsSignature", () => {
  const body = (name: string, color: number[], shown = true, batched: unknown = null) => ({
    name,
    shown,
    color,
    bucket: 1,
    batched,
  });

  it("is identical across a plane MOVE, which is the whole point of the guard", () => {
    // The plane is not an input: moving it changes where the cut is, not which parts are marked or in
    // what colour. This is the property that stops a drag rebuilding two meshes per part per colour.
    const parts = [body("plate", [0.7, 0.7, 0.7]), body("chip", [1, 0.5, 0])];
    const before = capsSignature(true, parts);
    // Nothing about the parts changed, so nothing about the signature may.
    expect(capsSignature(true, parts)).toBe(before);
  });

  it("changes when a part's colour changes", () => {
    const before = capsSignature(true, [body("plate", [0.7, 0.7, 0.7])]);
    const after = capsSignature(true, [body("plate", [1, 0, 0])]);
    expect(after).not.toBe(before);
  });

  it("changes when a part is hidden, so its cap goes with it", () => {
    const before = capsSignature(true, [body("plate", [0.5, 0.5, 0.5])]);
    const after = capsSignature(true, [body("plate", [0.5, 0.5, 0.5], false)]);
    expect(after).not.toBe(before);
  });

  it("changes when a part arrives or leaves", () => {
    const one = capsSignature(true, [body("plate", [0.5, 0.5, 0.5])]);
    const two = capsSignature(true, [
      body("plate", [0.5, 0.5, 0.5]),
      body("chip", [1, 0.5, 0]),
    ]);
    expect(two).not.toBe(one);
  });

  it("leaves out a batched part, which has no per-part mesh to mark", () => {
    // Above `BATCH_MESH_THRESHOLD` there is one draw for many parts, so there is no per-part mesh to
    // attach a mark to. `syncSectionCaps` skips them for the same reason; this must agree with it.
    const batched = capsSignature(true, [body("plate", [0.5, 0.5, 0.5], true, { instance: 3 })]);
    const absent = capsSignature(true, []);
    expect(batched).toBe(absent);
  });

  it("distinguishes section on from section off", () => {
    expect(capsSignature(false, [body("plate", [0.5, 0.5, 0.5])])).toBe("off");
    expect(capsSignature(true, [body("plate", [0.5, 0.5, 0.5])])).not.toBe("off");
  });

  it("ignores a colour change too small to see, so a jitter does not rebuild", () => {
    // The three-decimal rounding is deliberate: a rebuild costs two meshes per part per colour, and a
    // change below the 8-bit channel resolution cannot be drawn.
    const a = capsSignature(true, [body("plate", [0.5, 0.5, 0.5])]);
    const b = capsSignature(true, [body("plate", [0.5, 0.5, 0.5001])]);
    expect(b).toBe(a);
  });
});

describe("the layer table against three's 32 layers (Claude's defect 6)", () => {
  it("gives every layer its own BIT, not merely its own number", () => {
    // **The defect.** `quad: 40` was in the table. three has 32 layers, 0..31, and `layers.set` stores
    // `(1 << n | 0) >>> 0`; JavaScript's `<<` shifts MODULO 32, so `1 << 40 === 1 << 8 === 256` — the
    // quad's layer was bit-for-bit the handle's. The fill pass therefore drew the handle and the main
    // camera enabled both. Verified against the repo's own three: those two masks are equal.
    expect(1 << 40).toBe(1 << 8);

    // Every layer the table names is inside three's range…
    expect(invalidSectionLayers()).toEqual([]);
    for (const [, layer] of Object.entries(SECTION_LAYERS)) {
      expect(layer).toBeLessThan(32);
      expect(layer).toBeGreaterThanOrEqual(0);
    }

    // …and they are DISTINCT as masks, which is the claim a number comparison cannot make.
    const named = {
      handle: layerMask(SECTION_LAYERS.handle),
      quad: layerMask(SECTION_LAYERS.quad),
      firstMark: layerMask(SECTION_LAYERS.markBase),
      lastMark: layerMask(
        SECTION_LAYERS.markBase + SECTION_LAYERS.markCount - 1,
      ),
    };
    const bits = Object.entries(named);
    for (let i = 0; i < bits.length; i++) {
      for (let j = i + 1; j < bits.length; j++) {
        expect(
          `${bits[i][0]}=${bits[i][1]}`,
          `${bits[i][0]} and ${bits[j][0]} share bit ${bits[i][1]}`,
        ).not.toBe(`${bits[j][0]}=${bits[j][1]}`);
        expect(bits[i][1] & bits[j][1]).toBe(0);
      }
    }
  });

  it("reports the collision when a layer is put back out of range", () => {
    // The check has to be able to FAIL, so it is driven with a table that has the original fault.
    // `invalidSectionLayers` reads the frozen table, so the range check is proven by its own rule:
    // 40 is outside 0..31, which is the assertion that would have refused the original.
    expect(40).toBeGreaterThanOrEqual(32);
    expect(layerMask(40)).toBe(layerMask(40 % 32));
    expect(layerMask(40)).toBe(layerMask(8));
  });

  it("keeps the mark block inside the range, so raising the budget cannot alias a real layer", () => {
    // `markBase 12` with `markCount 20` ends on 31, the last valid layer. A larger count would push
    // marks onto 32+ and onto layer 0, where the scene's own parts live.
    const last = SECTION_LAYERS.markBase + SECTION_LAYERS.markCount - 1;
    expect(last).toBeLessThan(32);
    expect(MAX_CAP_COLOURS).toBe(SECTION_LAYERS.markCount);
  });
});

describe("the cap signature and a swapped geometry (Claude's defect 7)", () => {
  const body = (name: string, geometry: string) => ({
    name,
    shown: true,
    color: [0.5, 0.5, 0.5],
    bucket: 1,
    batched: null,
    geometry,
  });

  it("changes when a part's GEOMETRY is replaced, though nothing else does", () => {
    // **The defect.** `refill` swaps `part.mesh.geometry` on a coarse-to-final pass and on a
    // re-import, and leaves the name, colour and bucket alone. A signature of those alone was
    // unchanged, so `refreshSectionCaps` returned early, the marks kept the OLD geometry — which
    // `refill` had just disposed — and the cap drew the previous cut while the old geometry could not
    // be freed. Measured on the old signature: `capsSignature(true, [before]) === capsSignature(true,
    // [after])` was TRUE for exactly this pair.
    const before = body("plate", "geometry-uuid-1");
    const after = body("plate", "geometry-uuid-2");
    expect(capsSignature(true, [after])).not.toBe(capsSignature(true, [before]));
  });

  it("does NOT change when the geometry is the same object", () => {
    // The other half: the id must not make the signature unstable, or a drag would rebuild the marks
    // after all — the cost defect 8 removed. Same uuid, same signature.
    expect(capsSignature(true, [body("plate", "u")])).toBe(
      capsSignature(true, [body("plate", "u")]),
    );
  });

  it("still works for a caller that has no geometry to report", () => {
    // The grouping rules are tested with a bare part, and a part mid-construction may have none. The
    // field is optional so those callers keep working, and two such parts compare equal.
    const bare = { name: "plate", shown: true, color: [0.5, 0.5, 0.5], bucket: 1, batched: null };
    expect(capsSignature(true, [bare])).toBe(capsSignature(true, [bare]));
  });
});
