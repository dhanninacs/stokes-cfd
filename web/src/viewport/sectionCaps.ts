/**
 * The cut faces of a section (T-065b): the flat caps a solid shows where the section plane passes
 * through it, coloured by the body they belong to.
 *
 * ## The problem
 *
 * A clip plane discards fragments. Cut a pipe with it and you see the *inside* of the far wall through
 * the opening, because nothing was ever drawn where the cut is — a hollow shell, not a section. A real
 * cross-section shows the material the plane passed through, which for a closed solid is a flat face
 * at the plane.
 *
 * ## The technique
 *
 * The cap is not a face the model already has: it has none, and the plane moves every frame the slider
 * is dragged. It is *synthesised in the stencil buffer*, which follows any geometry without a B-rep
 * boolean per frame:
 *
 * 1. **Mark.** Draw the **back faces** of a body with the stencil incrementing, then its **front
 *    faces** with it decrementing, writing neither colour nor depth. Where the solid is closed behind
 *    the plane the two cancel to zero; where the plane has cut through material one of the pair was
 *    clipped away, so the counter is left non-zero. That non-zero region IS the shape of the cut,
 *    however complicated the model is.
 * 2. **Fill.** Draw a quad lying in the plane, kept only where the stencil is non-zero. It fills
 *    exactly the marked region, so the cap follows the outline of the cut rather than covering the
 *    whole plane.
 *
 * **A stencil mark cannot say which body made it**, so the colour has to come from the pass rather than
 * from a lookup: the two steps run **once per distinct colour**, marking only the parts of that colour
 * and filling with it. Two grey flanges are one pass; the blue fluid is another. That is what makes the
 * cut read as a cross-section of *this* model — the fluid's cut is the fluid's blue and the metal cuts
 * are metal — and it costs one extra pair of draws per colour, only while a section is shown.
 *
 * ## How the passes are separated
 *
 * Three.js has no "draw these objects only" flag, so each colour's marks go on **their own layer** and
 * each pass renders the scene through a camera limited to that layer. Only the marks on that layer are
 * drawn, so a pass costs one draw per part of that colour rather than the whole model. The quad has a
 * layer of its own so the fill pass draws it alone.
 *
 * ## What it does not do
 *
 * **A batched part is not capped** (ADR-0019, from `BATCH_MESH_THRESHOLD` patches on). A batch is one
 * draw call for many parts under one material, so there is no per-part mesh to hang a mark on. Above
 * that threshold the section still cuts and shows the hollow it always did — honest, rather than a cap
 * drawn for the wrong body. The threshold is far above any model a user sections by hand.
 *
 * **Curved cuts are flat.** The cap is a plane, so a cut through a curved wall shows the flat face the
 * plane makes, which is what a section view means. It is not a shell of the wall's thickness.
 */

import * as THREE from "three";

/**
 * How far the fill quad is pushed along the plane's normal, in world units.
 *
 * Zero would z-fight with anything exactly at the plane; a hair in front wins the depth test without a
 * visible gap at any sane zoom.
 */
const QUAD_OFFSET = 1e-6;

/**
 * The layers the section draws on, in **one table**, because two files using two constants is how
 * they collided (Claude's defect 2).
 *
 * `SECTION_HANDLE_LAYER = 9` was the second colour group's mark layer: the marks begin at 8, so the
 * handle sat inside the range the marks occupy. Those marks are drawn in every main pass and the
 * handle was drawn in that group's mark pass, so the two drew over each other. The comment claiming
 * the handle had "its own layer" was wrong.
 *
 * The ranges are laid out with a gap between them so a later addition cannot land on a neighbour by
 * accident, and `sectionLayersOverlap()` is what a test asserts — the layout is checked, not trusted.
 * Layers 0-3 are the scene's own (parts, occluders, glyphs).
 */
export const SECTION_LAYERS = {
  /** The drag handle, and its invisible pick sphere. */
  handle: 8,
  /** The first layer a colour's marks use. */
  markBase: 12,
  /** How many colour groups can be capped at once. */
  markCount: 20,
  /** The fill quad, on its own so its pass draws nothing else. */
  quad: 10,
} as const;

/**
 * **Every layer must be under 32, and `quad: 40` was not** (Claude's defect 6).
 *
 * `THREE.Layers` has 32 layers, numbered 0 to 31, and `layers.set(n)` stores `(1 << n | 0) >>> 0`.
 * JavaScript's `<<` shifts **modulo 32**, so `1 << 40 === 1 << 8 === 256`: the quad's layer 40 was
 * bit-for-bit the handle's layer 8. The fill pass therefore drew the handle, and the main camera
 * enabled the quad's layer along with the handle's. Verified against the repo's own three:
 * `1 << 40 === 256`, and `quad.layers.mask === handle.layers.mask`.
 *
 * **Why the previous check did not catch it.** `sectionLayersOverlap` compared layer *numbers*, so the
 * ranges `handle 8..8` and `quad 40..40` read as disjoint and the test passed while the masks were
 * identical. It now compares **masks**, which is the only comparison that means anything, and
 * `assertSectionLayers` refuses a table that is out of range at all.
 */
const LAYER_COUNT = 32;

/** The layer the fill quad uses, on its own so its pass draws nothing else. */
const QUAD_LAYER = SECTION_LAYERS.quad;

/** How many distinct colours can be capped at once: the layers the marks may use. */
export const MAX_CAP_COLOURS = SECTION_LAYERS.markCount;

/**
 * The mask a layer number stands for, as three computes it.
 *
 * Spelled out rather than left implicit because the modulo-32 behaviour of `<<` is exactly what hid
 * the defect: `1 << 40` looks like a distinct layer and is not one.
 */
export function layerMask(layer: number): number {
  return (1 << layer) >>> 0;
}

/**
 * Any layer in the table that three cannot represent, with the reason.
 *
 * A **build-time assertion** rather than a test alone: an out-of-range layer aliases another, so the
 * failure is a pass drawing the wrong things, which is hard to trace back to a number. `sectionCaps`
 * is imported before the scene draws, so this throws with the table in the message.
 */
export function invalidSectionLayers(): string[] {
  const bad: string[] = [];
  for (const [name, layer] of Object.entries(SECTION_LAYERS)) {
    if (!Number.isInteger(layer) || layer < 0 || layer >= LAYER_COUNT) {
      bad.push(`${name}: ${layer} is outside 0..${LAYER_COUNT - 1}`);
    }
  }
  // The mark block is a RANGE, so its end is checked too: markBase 30 with markCount 4 would put the
  // last group on layer 33, which aliases layer 1 and would collide with nothing this table names.
  const lastMark = SECTION_LAYERS.markBase + SECTION_LAYERS.markCount - 1;
  if (lastMark >= LAYER_COUNT) {
    bad.push(
      `marks: base ${SECTION_LAYERS.markBase} + ${SECTION_LAYERS.markCount} reaches layer ${lastMark}, ` +
        `outside 0..${LAYER_COUNT - 1}`,
    );
  }
  return bad;
}

/** Throw when the table asks for a layer three cannot represent. Called at module load. */
function assertSectionLayers(): void {
  const bad = invalidSectionLayers();
  if (bad.length > 0) {
    throw new Error(`SECTION_LAYERS is not usable by three.js: ${bad.join("; ")}`);
  }
}

assertSectionLayers();

/** The layer a colour group's marks use, or -1 for a group past the budget. */
export function markLayer(index: number): number {
  return index < MAX_CAP_COLOURS ? SECTION_LAYERS.markBase + index : -1;
}

/**
 * The layer ranges the section claims, for a test to check they do not overlap.
 *
 * Returned as data rather than asserted here so the test can fail with a useful message naming the two
 * that collided; an assertion inside the module would throw at import and take the whole page with it.
 */
export function sectionLayerRanges(): { name: string; from: number; to: number }[] {
  return [
    { name: "handle", from: SECTION_LAYERS.handle, to: SECTION_LAYERS.handle },
    {
      name: "marks",
      from: SECTION_LAYERS.markBase,
      to: SECTION_LAYERS.markBase + SECTION_LAYERS.markCount - 1,
    },
    { name: "quad", from: SECTION_LAYERS.quad, to: SECTION_LAYERS.quad },
  ];
}

/** The overlapping pairs of section layers, empty when the table is sound. */
export function sectionLayersOverlap(): [string, string][] {
  const ranges = sectionLayerRanges();
  const clashes: [string, string][] = [];
  for (let i = 0; i < ranges.length; i++) {
    for (let j = i + 1; j < ranges.length; j++) {
      const a = ranges[i];
      const b = ranges[j];
      // **Masks, not numbers.** Two different layer numbers can be the same bit (`1 << 40 ===
      // 1 << 8`), so comparing numbers reported `handle 8..8` and `quad 40..40` as disjoint while the
      // masks were identical — which is how the defect survived a test named for exactly this.
      const masksA = rangeMasks(a.from, a.to);
      const masksB = rangeMasks(b.from, b.to);
      if (masksA.some((m) => masksB.includes(m))) clashes.push([a.name, b.name]);
    }
  }
  return clashes;
}

/** Every mask a layer range stands for, clamped to what three can represent. */
function rangeMasks(from: number, to: number): number[] {
  const out: number[] = [];
  for (let n = from; n <= to; n++) {
    // Out-of-range numbers are reported by `invalidSectionLayers`; here they are folded onto their
    // real bit so an out-of-range table still shows up as a COLLISION rather than as silence.
    out.push(layerMask(n % LAYER_COUNT));
  }
  return out;
}

/** One fill colour: the parts drawn with it, and the material to cap them with. */
export interface CapLook {
  colour: THREE.Color;
  opacity: number;
}

/** One colour's marks, and the layer they live on. */
interface CapGroup {
  look: CapLook;
  layer: number;
  marks: THREE.Mesh[];
}

/** The two materials that mark a cut. One pair for every group: only the layer varies. */
function markMaterial(op: THREE.StencilOp, side: THREE.Side): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    side,
    // No colour and no depth: this pass exists only to write the stencil. `depthTest: false` because
    // the mark must cover the whole closed volume, not just the part of it in front of something else
    // — with depth testing on, a back face behind another body would drop out, the counters would not
    // cancel, and the cap would cover a region the plane never cut.
    colorWrite: false,
    depthWrite: false,
    depthTest: false,
    stencilWrite: true,
    stencilFunc: THREE.AlwaysStencilFunc,
    stencilFail: op,
    stencilZFail: op,
    stencilZPass: op,
  });
}

export class SectionCaps {
  private readonly markBack = markMaterial(THREE.IncrementWrapStencilOp, THREE.BackSide);
  private readonly markFront = markMaterial(THREE.DecrementWrapStencilOp, THREE.FrontSide);
  /** The quad, reused: one geometry and one material, re-coloured per pass. */
  private readonly quad: THREE.Mesh;
  /** The part a mark belongs to, so a part leaving the scene takes its marks with it. */
  private readonly marksByPart = new Map<THREE.Mesh, THREE.Object3D[]>();
  private groups: CapGroup[] = [];
  private readonly quadLayerObject = new THREE.Group();
  /** The camera the caps' own passes use; see `render` for why it is not the frame's. */
  private readonly passCamera = new THREE.PerspectiveCamera();
  /** The plane in world space, or null while the section is off. */
  private plane: THREE.Plane | null = null;
  private active = false;

  constructor() {
    this.quad = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({
        transparent: true,
        // The stencil decides the shape: keep exactly the marked region.
        stencilWrite: true,
        stencilFunc: THREE.NotEqualStencilFunc,
        stencilRef: 0,
        stencilFail: THREE.KeepStencilOp,
        stencilZFail: THREE.KeepStencilOp,
        stencilZPass: THREE.KeepStencilOp,
      }),
    );
    this.quad.frustumCulled = false;
    // **The quad itself carries QUAD_LAYER, and so does its group.** three.js layers are per-object and
    // are NOT inherited by children, so setting it on the group alone left the quad on layer 0: the
    // fill pass set the camera to QUAD_LAYER, the quad failed the layer test, and the caps drew
    // nothing with every count reporting healthy (`groups: 3, marks: 8`) — the most expensive kind of
    // bug to find, because every measurement of the bookkeeping says the work was done.
    this.quad.layers.set(QUAD_LAYER);
    this.quadLayerObject.layers.set(QUAD_LAYER);
    this.quadLayerObject.add(this.quad);
    // The marks are added to the PARTS, not to a group of ours, because a mark must share its part's
    // transform. `quadLayerObject` is the only thing the caller adds to a scene.
  }

  /** What the caller adds to the scene: the quad, and nothing else. */
  get object(): THREE.Object3D {
    return this.quadLayerObject;
  }

  /** Follow the section: the plane to cap, or null to draw nothing. */
  setSection(plane: THREE.Plane | null): void {
    this.plane = plane;
    // A plane with no normal is not a cut: refusing it here keeps the quad's quaternion finite, since
    // `setFromUnitVectors` on a zero vector produces NaN and the quad would vanish with no reason given.
    this.active = plane !== null && plane.normal.lengthSq() > 1e-12;
    // **The quad is hidden here, not left to `place`.** `place` returns early while the section is
    // off, so it never reached the line that hides the quad — and `render` also returns early on
    // `!showing`, so nothing else did either. A quad left visible from the last frame with a section on
    // would go on being drawn by the fill pass's layer after the section was removed, as a flat plane
    // hanging in the model. Found by a unit test, not on screen: the check reads the real flag.
    if (!this.active) this.quad.visible = false;
  }

  /**
   * Whether a section is shown, so the frame can skip this work entirely when it is not.
   *
   * **`active` alone, not `active && groups.length`.** Requiring groups here deadlocked the whole
   * feature: the caller builds the groups inside a guard on this getter, so a section turning on found
   * `groups.length === 0`, returned early, and never marked anything — measured as `clipping: 1` with
   * `groups: 0, marks: 0`, a section that cut the model and capped nothing. `render` still does
   * nothing with no groups, so the frame pays one loop over an empty array.
   */
  get showing(): boolean {
    return this.active;
  }

  /** The figures the review harness reads: see `SceneManager.capsInfo`. */
  info(): { groups: number; marks: number; quad: boolean; active: boolean } {
    return {
      groups: this.groups.length,
      marks: [...this.marksByPart.values()].reduce((sum, list) => sum + list.length, 0),
      quad: this.quad.visible,
      active: this.active,
    };
  }

  /**
   * Give each distinct colour its marks.
   *
   * Called when the parts or their colours change. A part is marked once per colour group it belongs
   * to, and its marks are children of its own mesh, so they follow its transform for nothing and are
   * removed with it when the part leaves.
   *
   * `colours` is every shown part's drawn colour and opacity, so a cap matches the body it belongs to.
   */
  setParts(parts: readonly { mesh: THREE.Mesh; colour: THREE.Color; opacity: number }[]): void {
    const byColour = new Map<string, { look: CapLook; meshes: THREE.Mesh[] }>();
    for (const part of parts) {
      const key = `${part.colour.getHexString()}:${part.opacity.toFixed(2)}`;
      const found = byColour.get(key);
      if (found === undefined) {
        byColour.set(key, {
          look: { colour: part.colour.clone(), opacity: part.opacity },
          meshes: [part.mesh],
        });
      } else {
        found.meshes.push(part.mesh);
      }
    }

    // Past the layer budget the extra colours are dropped rather than sharing a layer: two bodies
    // capped in one colour would put a flat wrong colour across a cut, which is worse than no cap on
    // the least of them, and the caller can say so if it needs to.
    const entries = [...byColour.values()].slice(0, MAX_CAP_COLOURS);
    this.clearMarks();
    this.groups = entries.map((entry, index) => {
      const layer = markLayer(index);
      const marks: THREE.Mesh[] = [];
      for (const mesh of entry.meshes) {
        const back = new THREE.Mesh(mesh.geometry, this.markBack);
        const front = new THREE.Mesh(mesh.geometry, this.markFront);
        for (const mark of [back, front]) {
          mark.frustumCulled = false;
          mark.layers.set(layer);
          mark.renderOrder = -1;
          mesh.add(mark);
          marks.push(mark);
        }
        const owned = this.marksByPart.get(mesh) ?? [];
        owned.push(back, front);
        this.marksByPart.set(mesh, owned);
      }
      return { look: entry.look, layer, marks };
    });
  }

  /** Stop marking every part: a re-import, a colour change, or a teardown. */
  clearMarks(): void {
    for (const marks of this.marksByPart.values()) {
      for (const mark of marks) mark.parent?.remove(mark);
    }
    this.marksByPart.clear();
    this.groups = [];
  }

  /**
   * Put the quad where the plane is, ready for the frame.
   *
   * Called once per frame while the section is on, before the scene is drawn: the quad must already be
   * in the plane when a fill pass reaches it.
   */
  place(bounds: THREE.Box3): void {
    if (!this.active || this.plane === null) return;
    const normal = this.plane.normal;
    const size = new THREE.Vector3();
    const centre = new THREE.Vector3();
    bounds.getSize(size);
    bounds.getCenter(centre);
    // Sized from the model's bounds so it covers the widest cut the plane can make, not the smallest.
    // The floor is a **hair above zero**, not a metre: flooring at a metre made every model smaller
    // than that get a quad twice its own size, which places the cap's edge far outside the model and
    // the fill nowhere near the cut. Only a degenerate bounds needs the floor at all.
    const span = Math.max(size.x, size.y, size.z, 1e-6) * 2;
    // A point ON the plane, through the model's centre along the normal, so the quad is centred on the
    // cut rather than on the model.
    const onPlane = centre.clone().addScaledVector(normal, -this.plane.distanceToPoint(centre));
    this.quad.position.copy(onPlane).addScaledVector(normal, QUAD_OFFSET);
    this.quad.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
    this.quad.scale.set(span, span, 1);
    // **Shown again here, not left as the last section left it.** `setSection(null)` hides the quad,
    // and nothing set it back: a `THREE.Mesh` is visible by default, so the FIRST section worked and a
    // section turned off and on again drew no caps at all — the marks were written and the fill never
    // drawn, with every count healthy. The review probe caught it as `quad: false` on a section that
    // was plainly capping.
    this.quad.visible = true;
  }

  /**
   * Draw the caps, one colour at a time.
   *
   * Each colour is a **clear, mark, fill** triple: the stencil is wiped first so a previous colour's
   * marks cannot leak into this one, the colour's own parts are marked, and the quad fills the region
   * they left. `camera` is the one the frame is drawn with; its layer mask is saved and restored so the
   * caller's own render is unaffected.
   */
  render(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera): void {
    if (!this.showing) return;
    // **The passes need a camera of their own, not the frame's.**
    //
    // three.js caches each material's projected clip planes and reuses them while `camera` is the
    // same object it saw last (`useCache` in `WebGLClipping.setState`). Running the marks through the
    // frame's camera made three treat that cache as valid for whichever material came next, so the
    // real parts inherited the MARK material's empty clip state and the model stopped being cut at
    // all — `clipping: 1` with a model that plainly was not clipped, which is how it read.
    //
    // A clone keeps the camera's position, projection and layers and gives the cache a different
    // identity, so the parts' own render afterwards recomputes its planes as it always did.
    // The pass camera takes the frame camera's whole transform and projection. `copy` carries
    // position, orientation, near/far and the projection matrix, which is all a clip plane needs; the
    // layer mask is set per pass below. The frame camera is never touched, so nothing has to be put
    // back on it.
    const pass = this.passCamera;
    pass.copy(camera as THREE.PerspectiveCamera);
    const material = this.quad.material as THREE.MeshBasicMaterial;
    // The stencil must survive between the mark and the fill, so this pass clears it by hand and stops
    // three from clearing on every `render` inside it.
    const autoClear = renderer.autoClear;
    const clearStencil = renderer.autoClearStencil;
    renderer.autoClear = false;
    renderer.autoClearStencil = false;
    try {
      for (const group of this.groups) {
        renderer.clearStencil();
        // 1. Mark this colour's cut.
        pass.layers.set(group.layer);
        renderer.render(scene, pass);
        // 2. Fill exactly what it marked.
        material.color.copy(group.look.colour);
        material.opacity = group.look.opacity;
        pass.layers.set(QUAD_LAYER);
        renderer.render(scene, pass);
      }
    } finally {
      renderer.autoClear = autoClear;
      renderer.autoClearStencil = clearStencil;
    }
  }

  dispose(): void {
    this.clearMarks();
    this.quad.geometry.dispose();
    (this.quad.material as THREE.Material).dispose();
    this.markBack.dispose();
    this.markFront.dispose();
  }
}

/**
 * A signature of what the cap marks are drawn from, so they are rebuilt when it changes and not before.
 *
 * **Why this is here rather than in the scene** (Claude's defect 8). Building the marks is real work —
 * two meshes per part per colour — and a drag moves the plane many times a second without changing
 * *which* parts are cut or what colour they are. The guard is a string compare against this signature,
 * and it is the whole of the fix: measured on the cold plate, a section-on plus 40 moves rebuilt 21
 * times before and **1** time after.
 *
 * The scene passed its own force-clear of the cached key before every compare, so the guard existed and
 * never held. Keeping the rule in a pure function is what makes that testable — the scene's key compare
 * lives beside `window` code and a renderer, and the caps object itself always rebuilds when asked.
 *
 * Covers exactly what a cap depends on: which parts are drawn, and in what colour and opacity. A
 * batched part has no per-part mesh to mark, so it is left out here for the same reason
 * `syncSectionCaps` leaves it out.
 */
export function capsSignature(
  showing: boolean,
  parts: readonly {
    name: string;
    shown: boolean;
    color: readonly number[];
    bucket: number;
    batched: unknown;
    /** The drawn geometry's own identity, so a swapped mesh forces a rebuild (defect 7). */
    geometry?: string;
  }[],
): string {
  if (!showing) return "off";
  const drawn: string[] = [];
  for (const part of parts) {
    if (!part.shown || part.batched !== null) continue;
    // Three decimals: the colour is a float per channel, and a change too small to see is not worth a
    // rebuild. This is a deliberate rounding, not a lossy accident.
    const colour = `${part.color[0].toFixed(3)},${part.color[1].toFixed(3)},${part.color[2].toFixed(3)}`;
    // **The geometry id is part of the signature** (Claude's defect 7). `refill` swaps a part's
    // geometry without touching its name, colour or bucket, so a signature of those alone was
    // unchanged and the marks kept the OLD, disposed geometry — a stale cap and a leak. The id is
    // optional so a caller with no geometry (a test of the grouping rules) still works, and a part
    // that gains one changes the signature, which is correct.
    const geometry = part.geometry === undefined ? "" : `:${part.geometry}`;
    drawn.push(`${part.name}:${colour}:${part.bucket}${geometry}`);
  }
  return `on|${drawn.join("|")}`;
}
