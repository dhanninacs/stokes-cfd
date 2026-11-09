/**
 * three.js scene manager: environment-lit PBR patches, fading ground with a contact shadow,
 * selection outline, animated camera, view modes, picking and the orientation gizmo (the view cube).
 *
 * Content comes in as sources: the sample part, or one imported part each (a part node's id). A
 * source is a few transport groups, and every group is one mesh and so one draw call. Sources are
 * added, replaced (a coarse mesh by the fine one) and removed one at a time, in any order, while the
 * file is still importing. What such a change costs beyond the mesh itself (the bounds, the ground
 * and its shadow, the camera frame, the drawing mode) is gathered up and done once per frame at
 * most, in `flushContent`.
 *
 * Many parts (ADR-0019): from BATCH_MESH_THRESHOLD patches on, the patches are drawn by a few
 * BatchedMeshes (batch.ts), one per look, instead of a mesh each, and below it again by their own
 * meshes; the switch follows the patch count both ways. Every patch keeps its own mesh either way,
 * out of the scene while batched: picking, the face tint and the selection outline use it, and a
 * hovered or selected patch is drawn by it, its instance hidden, until its highlight has faded, so the
 * highlight and the outline look as they do on their own. Part meshes and batches share PART_LAYER,
 * which is what the contact shadow draws, and the opaque ones OCCLUDER_LAYER, which is what the
 * outline's occluders draw. The feature edges of a batched scene are one merged line object
 * (edges.ts), found a slice at a time.
 *
 * Opacity (ADR-0021 4.4): a part's `opacity` below 1 draws it translucent in tenths (materials.ts
 * `opacityBucket`), by its own mesh through a translucent clone of its look and, when batched, in
 * the batch of its bucket (batch.ts). A translucent part stays pickable and keeps its contact shadow,
 * but hides nothing from the selection outline, so the outline of a part behind it shows through.
 *
 * Rendering is on demand: a frame is drawn only when something changed (camera, size, theme,
 * visibility, selection, a highlight that is still fading). The sources reach it through
 * `setSource`, the rest of the state through the `set*` methods, which the React wrapper calls from
 * the store.
 *
 * Patches are named: the name is what the store selects, hides and isolates, so it is unique in
 * the scene, and the caller (`PartsController`) chooses it. A group with face ids also answers
 * "which face is under the pointer": hovering tints that face (`FaceHighlight`).
 *
 * The pointer's candidates (ADR-0022 6.1, 6.2): `pickAll` returns every intersection under a client
 * position, reduced to distinct parts or distinct faces, which is what `Alt` + wheel cycles and what
 * the click count escalates. Both run on input alone — the hover pick costs what it always did —
 * and the hover text ({label, index, count}) goes to the store for the status strip (T-049).
 *
 * Tags (ADR-0021 3.5) reach a part as per-face state the scene keeps by source and applies again to
 * every mesh the source gets (`setFaceTint`, `setHiddenFaces`, fed by tags.ts): tag colours are written
 * into the geometry's tint attribute, which the look mixes in (materials.ts), so they draw nothing
 * of their own; hidden faces leave the drawn index, so they are neither drawn, picked nor outlined.
 *
 * The mesh (T-211) is more sources of the same scene: each boundary patch of a mesh (`MeshPatchesController`)
 * is a source `mesh:<node id>` whose one group has the kind `mesh`, with a matte recipe of its own. Which
 * layer the view draws, the geometry parts or the mesh patches, is `setLayerFilter`, a filter over the
 * parts' own visibility. A mesh patch draws no feature edges, gets no section cap, and has cell edges of its
 * own (`setSourceCellEdges`). Any source can have a colour per vertex (`setSourceVertexTint`), kept with the
 * source so it survives the source being set again.
 *
 * The id buffer (T-032, ADR-0021 3.3 and 3.4) is the other way to ask what is under the pointer:
 * `IdPass` (idbuffer.ts) draws the part layer alone into an offscreen RGBA8 target, each pixel naming
 * a part and a face, and a box or a lasso over thousands of parts is one read-back. The scene numbers
 * its parts (`assignIdIndices`), hands each own mesh its number as the id material's `uPartIndex` and
 * lets a batch name its instances through the DataTexture batch.ts keeps; the per-vertex face ordinal
 * was built with the geometry (geometry.ts `groupFaceIds`). The pass redraws only when a camera, a
 * content or a visibility change has marked it dirty, and a gesture reads it back once, at its end.
 * `idPick` and `marquee` are the two halves of that: the probe's one-pixel pick, and the drag on
 * empty space (Alt, or the Modes island's lasso toggle, draws a lasso) that hands its whole result to
 * the store's `pickMany` as one pick.
 *
 * Measure (T-098) rides the one-shot point pick (T-029): while it is on, every click that meets a
 * face records a world point, and two of them become a dimension — a line and an end marker each,
 * drawn in world space in the accent colour so they stay put while the camera moves, plus the
 * distance as an HTML label at the projected midpoint (the arithmetic is measure.ts, the label
 * MeasureReadout.tsx). It mutates no model state: view state only, like the scale bar.
 *
 * The module is long on purpose. One class owns the three.js state, and its invariants (names,
 * visibility, what a flush recomputes, which of mesh and batch draws a patch) run through every
 * section; the face tint is in faces.ts, the batches in batch.ts, the edges in edges.ts, the loading
 * of meshes in parts.ts, the parser in geometry.ts.
 */

import { fitTargetIds, isCutSource, namesOfSources } from "./fitTargets";
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import type { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import type { GizmoPlacement } from "../workspace/gizmoSlot";
import type { HoverInfo, NavMode, PickMode } from "../state/store";
import { leftPressOrbits } from "./leftDrag";
import { BatchedMeshPool, type BatchedPart } from "./batch";
import { CameraRig, type FitFrame, type ProjectionCamera } from "./camera";
import { edgeLines, featureEdges, MergedEdges } from "./edges";
import { LineLayer } from "./lineLayer";
import { streamlineLines } from "./streamlineLayer";
import { DEFAULT_COLORMAP, type ColormapId } from "../state/colormap";
import { createStudio, disposeStudio } from "./environment";
import { FaceHighlight, type FaceSource } from "./faces";
import { ViewGizmo } from "./gizmo";
import { FACE_ID_ATTRIBUTE, FACE_ID_NONE, groupFaceIds, type LoadedGroup, type PatchMeta } from "./geometry";
import { GroundGrid } from "./grid";
import { IdPass, PART_INDEX_NONE, polygonMask, rectMask, scanIdPixels } from "./idbuffer";
import {
  createLook,
  disposeLook,
  OPAQUE_BUCKET,
  opacityBucket,
  paintLook,
  parseHexColor,
  setLookColor,
  setLookOpacity,
  TINT_ATTRIBUTE,
  type PartLook,
} from "./materials";
import {
  RELATION_LABEL,
  intrinsicOfElement,
  measureBetween,
  measureDetails,
  measureElements,
  type ElementMeasurement,
  type IntrinsicMeasurement,
  type MeasureDetails,
  type MeasureElement,
  type MeasureReadout,
  type Measurement,
  type Vec3,
} from "./measure";
import type { PartTopology } from "./topology";
import { TopologyOverlay } from "./topologyOverlay";
import { capsSignature, SECTION_LAYERS, SectionCaps } from "./sectionCaps";
import { GlyphLayer } from "./glyphLayer";
import { axisDistance, draggedOffset } from "./sectionDrag";
import { firstKept, hitsCap, keptSide } from "./sectionPick";
import { edgesAtVertex, incidenceOf, wholePart, wireThrough, type Incidence } from "./wire";
import { glyphsFor, type TagAnchor } from "./glyphs";
import type { ConditionKind } from "../api/types";
import {
  cycleCandidate,
  hiddenByFaces,
  hitEdges,
  hitVertices,
  type EdgeCandidate,
  type VertexCandidate,
} from "./topologypick";
import { canvasCssPoint, projectPoint, projectPolyline } from "./topology";
import { SelectionOutline } from "./outline";
import { scaleBar, type ScaleBar } from "./scale";
import { SelectionHighlight, type FacePick } from "./selection";
import { ContactShadow } from "./shadow";
import type { SceneTheme } from "./theme";
import { chooseTint, groupSlice, tintedTriangles, tintLayout } from "./vertexTint";
import { upFor } from "./viewDirections";

/** The streamlines' width on screen, in pixels (`LineMaterial`). */
const STREAMLINE_WIDTH_PX = 2;

export type { SceneTheme } from "./theme";
export type ViewMode = "shaded" | "edges" | "wireframe";

/** The source id of the sample part; part nodes use their node id. */
export const SAMPLE_SOURCE = "@sample";

/**
 * A part's tag colours (ADR-0021 3.5) as `packTint` values: one for every face (0 for none) and one
 * per face over it.
 */
export interface FaceTint {
  all: number;
  faces: ReadonlyMap<number, number>;
}

const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;

/**
 * An sRGB colour (0..1) and a strength (0..1, the tag's opacity) as one word of the tint attribute,
 * whose bytes are r, g, b, a in memory whatever the platform's byte order. 0 is no tint.
 */
export function packTint(srgb: readonly [number, number, number], strength: number): number {
  const byte = (v: number): number => Math.round(THREE.MathUtils.clamp(v, 0, 1) * 255);
  const [r, g, b, a] = [byte(srgb[0]), byte(srgb[1]), byte(srgb[2]), byte(strength)];
  if (a === 0) return 0;
  return (LITTLE_ENDIAN ? r | (g << 8) | (b << 16) | (a << 24) : (r << 24) | (g << 16) | (b << 8) | a) >>> 0;
}

export interface ScreenAnchor {
  /** Horizontal middle of the selection's projected bounds. */
  x: number;
  /** Top edge of the projected bounds. */
  y: number;
  /** Bottom edge of the projected bounds; absent when only the centre could be projected. */
  bottom?: number;
}

/** What the pointer picked: the source id, the patch name and the face id (ADR-0021 3.2). */
export interface PickHit {
  /** The source id: a part node id, or `SAMPLE_SOURCE` for the sample part. */
  part: string;
  /** The patch name of the group under the pointer. */
  patch: string;
  /** `faceIds[triangle]`, or -1 for a mesh without face ids. */
  face: number;
}

/** The modifier keys of a click: Shift adds, Ctrl toggles, neither replaces. */
export interface PickModifiers {  shift: boolean;
  ctrl: boolean;
  /**
   * How many clicks the pointerup ends (ADR-0022 6.2): 1 for a click, 2 for a double-click within
   * `CLICK_INTERVAL` and `POINTER_SLOP` of it, 3 for a triple, and 1 again after that.
   */
  clicks: number;
}

/** How a source looks, from its part node's parameters. */
/**
 * How solid a body left out of the simulation is drawn (T-222).
 *
 * **A tenth above the floor**, chosen against the picture rather than by taste: the existing buckets
 * are tenths, and a third reads as "present but not used" while a fifth started to disappear against
 * the ground at typical zoom.
 */
const EXCLUDED_OPACITY = 0.3;

export interface PartStyle {
  /** `#rrggbb` that replaces the colours of the mesh, or null to keep the file's. */
  color: string | null;
  /** The part's own `visible` parameter. */
  visible: boolean;
  /** The part's own `opacity` parameter, 0 transparent to 1 opaque; 1 when the node has none. */
  opacity: number;
  /**
   * The part's own `simulate` parameter: false when the user left it out of the simulation.
   *
   * **A body left out is drawn FAINT, not hidden** (T-222). Keeping it on screen is the point: a user
   * who excludes a body to see the fluid wants to see WHERE it was, and a body that vanished would
   * leave them unsure whether it was excluded or lost. The tree's slashed `⚡` and this fade are the
   * same fact said in two places, which is why they read one flag.
   */
  simulate?: boolean;
  /**
   * True for a cut of the results (slice, iso-surface, section cap): drawn with no lighting and two sides
   * (`materials.ts` `createLook`), and never batched. The mesh patches leave it out and stay lit.
   */
  unlit?: boolean;
}

/** Figures for the measurement hook (Viewport.tsx); nothing in them changes what is drawn. */
export interface SceneStats {
  /** Smoothed CPU time of a frame in ms: what the frame loop spent, not the display's refresh interval. */
  frameCostMs: number;
  parts: number;
  sources: number;
  /** 1 while the patches are drawn by batches (ADR-0019). */
  batched: number;
  batches: number;
  /** Batches of translucent parts, at most nine per look (ADR-0021 4.4); included in `batches`. */
  translucentBatches: number;
  /** Instances the batches draw, and patches drawn by their own mesh for now because they are highlighted. */
  batchInstances: number;
  raised: number;
  /** Over every pass of the last frame: main, shadow, outline and view cube. */
  drawCalls: number;
  triangles: number;
  /** Triangles drawn in a tag's colour (a vertex attribute: no draw call or triangle of its own). */
  tagTriangles: number;
  /** Parts drawn with no lighting: the cuts of the results (`PartStyle.unlit`). */
  unlitParts: number;
  /** Triangles a tag with its eye off takes out of the drawn index. */
  hiddenTriangles: number;
  geometries: number;
  textures: number;
  width: number;
  height: number;
  pixelRatio: number;
}

/** Times of frames: forced ones (`measureRender`) or the ones the scene drew (`startFrames`). */
/**
 * How much of the environment's intensity the ambient light stands in for when reflections are off.
 *
 * **Measured against the failure, not guessed**: at 0.55 absolute the dark theme's model was
 * unreadable (claude-win2's GUI review item 2), and 0.55 against a 0.9 environment is only 61% of the
 * light. 82% keeps the matte render in the same brightness class while still leaving the key light a
 * direction to shade by - at 1.0 a cube's faces become one flat tone.
 */
const AMBIENT_SHARE = 0.82;

export interface FrameTiming {
  /**
   * "gpu": `ms` is the GPU time of each frame, from EXT_disjoint_timer_query_webgl2. "cpu": the
   * extension is missing, and `ms` is only the CPU time to submit the frame; the GPU may still be
   * drawing when it ends, so it understates a GPU-bound frame.
   */
  method: "gpu" | "cpu";
  ms: number[];
  /** CPU submission time of each frame, measured either way. */
  cpuMs: number[];
}

/**
 * The patches one source draws.
 *
 * **This exists because the lookup key is the whole bug.** `this.parts` is keyed by patch *name*
 * (`setSource` names each group; a multi-solid part has one patch per solid), while a topology record
 * is keyed by the *source id* the controller fetched for. `this.parts.get(recordKey)` therefore never
 * matched: measured on the demo, 4 records loaded, 4 parts drawn, and the edge loop skipped all four
 * at the `entry === undefined` guard, so Edge mode found no candidate anywhere and selected nothing
 * (and the earlier defect report — "can't select edges in the default loaded model" — was exactly
 * this, not a missing fetch, a null mode, or a bad tolerance, each of which I checked first).
 *
 * Pulled out as a pure function so the rule can be tested without a WebGL context: a scene needs one,
 * and this is the part that was wrong.
 */
export function sourcePatches<TPart>(
  sources: ReadonlyMap<string, readonly TPart[]>,
  source: string,
): readonly TPart[] {
  return sources.get(source) ?? [];
}

/**
 * The patches of ``source`` the topology pick may consider: its patches, minus the hidden ones.
 *
 * **This exists because the test for the bug was not testing the loop.** The `sourcePatches` tests
 * built a `Map` and asserted `Map.get` returned the right entries, which is a test of the standard
 * library: it would pass with the loop calling the function wrongly, or not calling it at all
 * (Claude's defect 7). What the loop actually decides is **which patches become candidates**, and that
 * is this: the source's patches, filtered by what is drawn.
 *
 * Pulled out as a pure function so it can be tested without a WebGL context, the same reason
 * `sourcePatches` was. The second filter — whether the edge or vertex projects on screen, and whether
 * its bordering faces are hidden — needs the camera matrix and stays in the loop.
 */
export function candidatePatches<TPart extends { shown: boolean }>(
  sources: ReadonlyMap<string, readonly TPart[]>,
  source: string,
): readonly TPart[] {
  return sourcePatches(sources, source).filter((patch) => patch.shown);
}

interface Part {
  /** The patch name the selection, hiding and isolating work with; unique in the scene. */
  name: string;
  /** The source the group came from. */
  source: string;
  /**
   * Whether the body is in the simulation; false when the user left it out.
   *
   * **Kept on the part** because two things read it after the style has been applied: the fade, and
   * the box-select rule that skips a left-out body. A style passed to `restyle` and not kept would
   * make the pick rule guess.
   */
  simulate?: boolean;
  /** True for a cut of the results: its look is unlit (`PartStyle.unlit`), and no batch draws it. */
  unlit: boolean;
  /** The patch's own mesh. In the scene unless a batch draws the patch; picked, outlined and tinted either way. */
  mesh: THREE.Mesh<THREE.BufferGeometry, THREE.Material>;
  look: PartLook;
  /** The patch's instance while a batch draws it (ADR-0019); null while its own mesh does. */
  batched: BatchedPart | null;
  /** A batched patch drawn by its own mesh for now, because it is highlighted or its highlight is fading. */
  raised: boolean;
  group: LoadedGroup;
  /** The colour drawn: the group's own, or the source's colour parameter. */
  color: [number, number, number];
  /** The source's opacity in tenths (`opacityBucket`); below OPAQUE_BUCKET the patch is translucent. */
  bucket: number;
  /** What the store is told about the group; replaced, never edited, so changes show by identity. */
  meta: PatchMeta;
  /** Feature edge segments, found the first time the "edges" view mode needs them. */
  edgeSegments: Float32Array | null;
  /** The patch's own edge lines, below the batching threshold; above it the merged lines draw them. */
  edges: LineSegments2 | null;
  /**
   * The index and face ids drawn, picked and outlined, aligned triangle by triangle: the group's own,
   * or a copy without the faces a tag hides. The group keeps the full arrays.
   */
  indices: Uint32Array;
  faceIds: Uint32Array | null;
  /**
   * The transport face id of each face ordinal the id buffer names (T-032, `groupFaceIds`): a pixel's
   * B,A bytes are an ordinal into this, not a face id. Empty for a mesh without face ids.
   */
  faceTable: Uint32Array;
  /** The part's number in the id buffer, 1-based; 0 while it has none (see `assignIdIndices`). */
  idIndex: number;
  /** True once the patch's faces past the 16-bit ordinal limit have been logged (T-032). */
  idLimitLogged: boolean;
  /** Triangles that carry a tag colour now. */
  tinted: number;
  /** Where the group's vertices lie in the source's vertex tint (`setSourceVertexTint`), and the source's total. */
  tintAt: { offset: number; total: number };
  /** True while the attribute holds the source's vertex tint, so clearing it later writes the zeros. */
  vertexTinted: boolean;
  /** Bounds in world space. */
  box: THREE.Box3;
  /** True unless hidden, or another patch is isolated, or the source's `visible` is off. */
  shown: boolean;
  /** The source's own visibility. */
  enabled: boolean;
  /** Highlight now and where it is heading: 0 none, 1 selected. */
  lift: number;
  liftTarget: number;
}

/** A triangle of a group under the pointer. */
interface Hit {
  part: Part;
  triangle: number;
}

/**
 * A point picked in the view (T-029): where the ray met a face, the face's outward normal and the
 * part it belongs to, all in world units (metres). The seed of an operation is this point moved a
 * little along the normal, so the caller needs the normal and the part's size as well.
 */
export interface PointHit {
  /** The point on the face, world space. */
  point: THREE.Vector3;
  /** The face's outward normal at that point, unit length, world space. */
  normal: THREE.Vector3;
  /** The source id of the part: a part node id, or `SAMPLE_SOURCE`. */
  part: string;
  /** The patch name of the group under the pointer. */
  patch: string;
  /** `faceIds[triangle]`, or -1 for a mesh without face ids. */
  face: number;
  /** The diagonal of the part's bounds, world units; 0 for a part with no bounds yet. */
  diagonal: number;
  /**
   * The pointer that produced the hit, in CSS pixels relative to the canvas (T-113). Kept so a
   * caller can run a second, screen-space test at the same point — measuring an edge needs the
   * position the user clicked, not just the face the ray happened to meet.
   */
  pointer: { x: number; y: number };
  /**
   * The camera ray the click was made along, world space. The plot over a line carries a click to the slice's plane
   * along it (a click lands on whatever face the pick met, which is not on the slice).
   */
  ray?: { origin: THREE.Vector3; direction: THREE.Vector3 };
}

/** What the viewport answers a point pick with (the callback `requestPoint` was given). */
export type PointPick = (hit: PointHit) => void;

/** One item of a bulk pick (T-032): a body (no `faces`), or the faces of the part node `part`. */
export interface BulkPickItem {
  part: string;
  faces?: number[];
}

/** The gesture being drawn over the canvas: a rubber band, or a freehand lasso. */
export type MarqueeShape = "box" | "lasso";

/** The pointer drag that selects a whole region, in CSS pixels of the canvas. */
interface Marquee {
  from: { x: number; y: number };
  to: { x: number; y: number };
  shape: MarqueeShape;
  /** The lasso's path, flat `[x0, y0, x1, y1, ...]` in CSS pixels; empty for a box. */
  points: number[];
}

/** The parts the id buffer can name: a part index is 16 bit and 0 is the clear value. */
const MAX_ID_PARTS = 0xffff;

/** Edges between faces that differ by more than this angle are feature edges. */
const EDGE_ANGLE = 30;
const EDGE_WIDTH = 1.25;
/** The transport kind of a mesh patch (`stokes/mesh/surfaces.py`), which `materials.ts` has a recipe for. */
const MESH_KIND = "mesh";
const HOVER_LIFT = 0.33;
/** The measurement's line, in CSS pixels: thicker than a feature edge, so it reads over the surface. */
const MEASURE_WIDTH = 2;
/** The size of each end marker, in CSS pixels: constant on screen, so a 10 mm pair shows them too. */
const MEASURE_DOT = 7;
/** Drawn after the parts, their edges (1) and the grid, so the dimension is never buried. */
/**
 * Whether a raycast intersection is a FRONT face, as the renderer would draw it.
 *
 * three reports the face normal in the mesh's own space, so it is turned into world space with the
 * object's normal matrix before being compared with the direction the ray arrived from. A face whose
 * outward normal points back along the ray is the one the camera sees; the other is the inside of a
 * far wall, which a double-sided material lets a ray hit and the id pass never draws.
 *
 * A hit with no face (a line or a point, which this path does not use) counts as front, so an
 * unexpected object is picked rather than silently dropped.
 */
function isFrontFace(intersection: THREE.Intersection, from: THREE.Vector3): boolean {
  // **`face.normal`, not `intersection.normal`.** three FLIPS `intersection.normal` to oppose the ray
  // for a back face (`Mesh.js`: `if (intersection.normal.dot(ray.direction) > 0) multiplyScalar(-1)`),
  // so it cannot tell front from back by then. `face.normal` is `Triangle.getNormal` of the winding —
  // the geometry's own outward direction, untouched — which is what the renderer's culling uses.
  const normal = intersection.face?.normal;
  if (normal === undefined) return true; // no face: a line or point, which this path does not use
  const world = normal
    .clone()
    .applyMatrix3(new THREE.Matrix3().getNormalMatrix(intersection.object.matrixWorld))
    .normalize();
  // The ray travels from `from` towards the hit; a front face's outward normal opposes that.
  const along = intersection.point.clone().sub(from);
  return world.dot(along) <= 0;
}

/**
 * A point from a shape's own space into world space, through a model matrix.
 *
 * **Not the view-projection matrix.** The topology loops build `world` as
 * `mesh.matrixWorld.premultiply(cameraMatrix)`, which is model → clip: feeding that to this function
 * gives a clip-space point, and comparing it against a world-space plane tests nonsense. The transform
 * needed here is the mesh's own `matrixWorld`, and the two are easy to confuse because both are
 * called `world` in that loop. Caught by the pick test rather than by reading it.
 */
function worldPoint(
  point: { x: number; y: number; z: number },
  m: ArrayLike<number>,
): { x: number; y: number; z: number } {
  return {
    x: m[0] * point.x + m[4] * point.y + m[8] * point.z + m[12],
    y: m[1] * point.x + m[5] * point.y + m[9] * point.z + m[13],
    z: m[2] * point.x + m[6] * point.y + m[10] * point.z + m[14],
  };
}

const MEASURE_ORDER = 3;
/** At most this many points of a face go into a curved-face distance (two faces: a few million pairs). */
const MEASURE_SAMPLES = 2500;
/** At most this many triangles of a face go into the exact minimum distance (two faces: ~10 M checks). */
const MEASURE_TRIANGLES = 600;
/** Two triangles of one face within 0.5° of each other: the face is planar. */
const MEASURE_PLANAR_COS = Math.cos((0.5 * Math.PI) / 180);
/** The section handle's own layer, so no other pass draws it (T-065d). */
/**
 * The drag handle's layer, taken from the section's own table.
 *
 * It used to be a literal `9` here while the colour marks began at 8 in `sectionCaps.ts`, so the
 * handle sat **inside** the mark range and the two drew over each other (Claude's defect 2). The
 * comment above the old constant claimed it had "its own layer" and that was simply untrue. Deriving
 * it from `SECTION_LAYERS` is what stops the two files drifting apart again; a test asserts the
 * ranges do not overlap.
 */
const SECTION_HANDLE_LAYER = SECTION_LAYERS.handle;
/**
 * How far the measurement label's centre is kept from the viewport's edges, in CSS pixels: half the
 * widest label the ladder produces on the sides, so a long figure stays inside the frame.
 *
 * The top inset is the collision one. The label is centred on its point, so its top sits about half
 * its height (7 px) above that point; the top centre belongs to the scale bar — 23 px tall — which an
 * import slides down to `top: 30px`, for a bottom edge at about 53 px. 64 px therefore clears the
 * worst case by a few pixels and keeps a figure over the grid from printing through either of them
 * (the collision fixed 2026-10-06).
 */
const MEASURE_LABEL_HALF = 44;
const MEASURE_LABEL_TOP = 64;
/** Half the label's height again on the bottom, so it stays inside a short viewport. */
const MEASURE_LABEL_BOTTOM = 12;
/**
 * How far, in CSS pixels, the pointer may be from an edge or vertex to pick it (ADR-0022 §15.3).
 * Multiplied by the device pixel ratio at the call site, so the *device*-pixel tolerance the hit
 * tests apply is a real finger-sized target on both a normal and a HiDPI canvas. The ADR asks for
 * this to be established by a measurement on the 4,500-part case; 6 px is the starting value, and it
 * is a device-pixel figure rather than a world one so it cannot vary with zoom.
 */
const EDGE_PICK_TOLERANCE_PX = 6;

/** Time constant of the highlight fade, seconds. */
const LIFT_TIME = 0.05;
/** Share of the canvas a fitted part may fill. Leaves the floating panels clear. */
const FIT_RESERVE_X = 360;
const FIT_RESERVE_Y = 72;
/** Least time between two hover picks, in ms; a move inside the window is picked when it ends. */
const HOVER_INTERVAL = 30;
/**
 * Pointer movement that is not a click but a drag, and that ends both a run of clicks and a cycle
 * of the candidates under the pointer, in CSS pixels (ADR-0022 6.1, 6.2).
 */
const POINTER_SLOP = 4;
/** Time within which a second pointerup counts as a double-click, and a third as a triple, in ms. */
const CLICK_INTERVAL = 400;
/**
 * Least time between two content flushes, in ms, so a burst of arriving parts costs one. The gap
 * grows with what a frame costs, a few times as much, up to the second constant: with thousands of
 * parts a frame takes tens of milliseconds, and redrawing after every arrival would leave no time
 * for the arrivals themselves.
 */
const FLUSH_INTERVAL = 80;
const MAX_FLUSH_INTERVAL = 1500;
const FLUSH_COST_FACTOR = 4;
/** Beyond this many shown parts the camera frames the union of their boxes, not every corner. */
const MAX_FIT_BOXES = 256;
/**
 * From this many patches on (this count included) they are drawn by BatchedMeshes (ADR-0019); below
 * it, each by its own mesh. Checked at every flush, so the mode follows the count both ways.
 */
const BATCH_MESH_THRESHOLD = 100;
/**
 * The layer every patch's mesh and every batch is on besides the default one: the contact shadow
 * draws what is on it, so it keeps nothing per part.
 */
const PART_LAYER = 1;
/**
 * The layer of the opaque ones among them: the outline's occluders draw what is on it. A translucent
 * patch or batch stays off it (ADR-0021 4.4), so the selection outline shows through it.
 */
const OCCLUDER_LAYER = 2;
/**
 * The boundary glyphs' own layer (ADR-0022 §8.1): outside `PART_LAYER`, so they are excluded from
 * the outline occluder pass, the id pass, the contact shadow and the batches. Nothing else draws it,
 * which is the point — a glyph must never be picked as geometry or shadow a part.
 */
const GLYPH_LAYER = 3;
/**
 * A batched scene finds feature edges for at most EDGE_SLICE ms a frame, and while it is still
 * finding them redraws the merged lines at most every EDGE_MERGE_INTERVAL ms.
 */
const EDGE_SLICE = 6;
const EDGE_MERGE_INTERVAL = 250;
/** How long after the content last changed the translucent programs are compiled ahead, in ms (see `warmGlass`). */
const GLASS_WARM_DELAY = 1500;
/**
 * How long after the content last changed the box-select programs are compiled ahead, in ms (see
 * `warmPickSelect`). Shorter than the glass delay: a box select is an ordinary first action on a
 * freshly loaded model, so the wait only has to outlast the burst of flushes a load ends with.
 */
const PICK_WARM_DELAY = 700;

/** Frames being timed: CPU time of each, and its GPU timer query when there is one. */
interface FrameRecord {
  cpuMs: number[];
  queries: WebGLQuery[];
}

/** The extension behind GPU frame timing; typed here because the DOM library does not name it. */
interface TimerQuery {
  readonly TIME_ELAPSED_EXT: GLenum;
  readonly GPU_DISJOINT_EXT: GLenum;
}

function nextFrame(): Promise<number> {
  return new Promise((resolve) => requestAnimationFrame(resolve));
}

function boxCorners(box: THREE.Box3, out: THREE.Vector3[]): void {
  for (let i = 0; i < 8; i++) {
    out.push(
      new THREE.Vector3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z),
    );
  }
}

function sameColor(a: readonly number[], b: readonly number[]): boolean {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
}

/** The B-rep face id of a triangle of a part, or -1 for a mesh that carries no face ids (ADR-0021 3.2). */
function faceIdOf(part: Part, triangle: number): number {
  const ids = part.faceIds;
  return ids && triangle >= 0 && triangle < ids.length ? ids[triangle] : -1;
}

export class SceneManager {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  /** The model's size for section tolerances; null until asked, cleared when the content changes. */
  private spanCache: number | null = null;
  /** What the caps were last built from, so one is not rebuilt for nothing (defect 4). */
  private sectionCapsKey = "";
  /** The live section plane, so the drag handle can sit on it (T-065d). */
  private sectionPlane = new THREE.Plane();
  /** The axis the section cuts along, which is the direction its handle slides. */
  private sectionAxis: "x" | "y" | "z" = "z";
  /** True while a section is shown. */
  private activeSection = false;
  /** The section's cut faces (T-065b); one object in the scene, hidden while no section is on. */
  private readonly sectionCaps = new SectionCaps();
  readonly rig: CameraRig;

  /** Every drawn group by patch name. */
  private readonly parts = new Map<string, Part>();
  /** The groups of each source, in transport order. */
  private readonly sources = new Map<string, Part[]>();
  private readonly partOf = new Map<THREE.Object3D, Part>();
  private readonly grid: GroundGrid;
  private readonly shadow: ContactShadow;
  private readonly outline: SelectionOutline;
  private readonly edgeMaterial: LineMaterial;
  /**
   * The measurement's own materials (T-098), made once: the line in pixels of width and the end
   * markers in pixels of size, so a 10 mm pair and a 10 m one both read. They take the accent, and
   * a resize reaches the line's `resolution`.
   */
  private readonly measureLineMaterial: LineMaterial;
  private readonly measureDotMaterial: THREE.PointsMaterial;
  private readonly faces: FaceHighlight;
  private readonly selection: SelectionHighlight;
  /**
   * The edge/vertex hover and pick overlay (ADR-0022 §15.3, T-058): two line objects and two marker
   * objects, rebuilt only when the pick changes, never in the frame or orbit path.
   */
  private readonly topologyOverlay: TopologyOverlay;
  /**
   * The boundary glyphs (ADR-0022 §8.1, T-046): one instanced mesh per shape on their own layer,
   * rebuilt only when the boundaries or a part's mesh change — never in the frame or orbit path.
   */
  private readonly glyphs: GlyphLayer;
  /** The anchors `tag.anchor` last returned, kept so a camera move can rescale without a refetch. */
  private anchors: TagAnchor[] = [];
  /** The kind of each boundary tag, by tag path, for the glyph's direction and colour. */
  private kindOfTag = new Map<string, ConditionKind>();
  /** Whether the glyphs are shown at all (`view.glyphs`); on by default. */
  private glyphsShown = true;
  /** Per source, the faces tags hide and the tag colours, applied again to every mesh the source gets. */
  private readonly hiddenFaces = new Map<string, ReadonlySet<number>>();
  private readonly faceTints = new Map<string, FaceTint>();
  /**
   * Per source, a per-vertex tint (`setSourceVertexTint`): RGBA8 for the vertices of its groups. It
   * outlives the geometry, because `applyTint` runs again whenever a part is made or replaced and takes
   * this, when present, in place of the source's face tint.
   */
  private readonly vertexTints = new Map<string, Uint8Array>();
  /**
   * Which layers the view draws: the geometry parts, and the mesh patches (kind `mesh`). A view filter,
   * not a part parameter: nothing is journaled and no part's own `visible` changes (T-211).
   */
  private layers = { geometry: true, mesh: false };
  /** The cell edges of each mesh source (`setSourceCellEdges`), one line object per source. */
  private readonly cellEdges = new Map<string, LineSegments2>();
  /** Whether the cell edges are drawn at all (the Display menu's "Mesh with cells"). */
  private cellEdgesOn = false;
  /** The sources whose cell edges ignore that mode (`setSourceCellEdges` `own`): drawn whenever they exist. */
  private readonly ownCellEdges = new Set<string>();
  /** The contour lines of each mesh source (`setSourceContours`), one line object per source. */
  private readonly contourLines = new Map<string, THREE.LineSegments>();
  private readonly batchPool: BatchedMeshPool;
  private readonly mergedEdges: MergedEdges;
  private readonly accent = new THREE.Color();
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly timer = new THREE.Timer();
  private readonly scratch = new THREE.Vector3();
  private readonly drawingBuffer = new THREE.Vector2();
  private readonly gizmo: ViewGizmo;
  private environment: THREE.WebGLRenderTarget | null = null;
  /** The display switches (the owner's display toggles); each defaults to what draws today. */
  private shadowOn = true;
  /** The ambient lift used when reflections are off; made in the constructor with the other lights. */
  private ambient: THREE.AmbientLight | undefined;
  private reflectionsOn = true;
  private edgesOn = true;
  private theme: SceneTheme;
  private viewMode: ViewMode = "shaded";
  private hidden = new Set<string>();
  private isolated: string | null = null;
  private hovered: string | null = null;
  private selected: string | null = null;
  /** The pick the store holds, as the scene draws it (ADR-0021 3.5). */
  private facePick: ReadonlyMap<string, ReadonlySet<number>> = new Map();
  private bodyPick: { active: string | null; others: readonly string[] } = { active: null, others: [] };
  /** A signature of the drawn pick, so an unchanged one is not rebuilt. */
  private selectionKey: string | null = null;
  /** Milliseconds the last raycast took; the probe reports it (ADR-0021 3.2). */
  lastPickMs = 0;
  /** Milliseconds the last id-buffer pass and its read-back took; the probe reports it (T-032). */
  lastIdReadMs = 0;
  private downAt: { x: number; y: number } | null = null;
  private pointerAt: { x: number; y: number } | null = null;
  private hoverTimer = 0;
  private lastHoverCheck = 0;
  /**
   * The pick mode the candidates are reduced by and the hover text is made for (ADR-0022 6.1); the
   * store owns it, the viewport reports it here.
   */
  private pickMode: PickMode = "body";
  /**
   * Paint select (T-043): while it is on the canvas paints instead of orbiting. The store owns the
   * mode and reports it through `setPaintMode`; the stroke itself lives here.
   */
  private painting = false;
  /** A left press of a paint stroke holds the camera still until the stroke ends. */
  private stroke = false;
  /** Ctrl at the last pointermove of the stroke: it removes instead of adding (T-043). */
  private strokeCtrl = false;
  /** The part and face the stroke applied last, so a hover pick on the same one touches nothing. */
  private strokeAt: { part: string; face: number } | null = null;
  /** The candidate `Alt` + wheel stands on, of the ones under the pointer, and where it started. */
  private cycle = 0;
  private cycleAt: { x: number; y: number } | null = null;
  private cycleHits: Hit[] = [];
  /** The edges/vertices under the pointer for the `Alt` + wheel cycle (T-058), nearest first. */
  private topologyCycle: { part: string; kind: "edge" | "vertex"; id: number }[] = [];
  /** Where `topologyCycle` was gathered: a wheel at a new spot re-gathers rather than reusing. */
  private topologyCycleAt: { x: number; y: number } | null = null;
  /** What the topology wheel is currently standing on, shown as the hover before a click. */
  private topologyPicked: { part: string; kind: "edge" | "vertex"; id: number } | null = null;
  // ------------------------------------------------------------------ the id buffer (T-032)
  private readonly idPass: IdPass;
  /** A part was added, removed or renamed: the id buffer's part numbers are rebuilt before its next pass. */
  private idIndicesDirty = true;
  /** A camera, content or visibility change since the last id pass: the target no longer shows the view. */
  private idDirty = true;
  /** True while the id pass draws: a part mesh's `onBeforeRender` writes its uniform only then. */
  private idActive = false;
  /** The store's lasso toggle: the next drag on empty space draws a lasso instead of a box (T-032). */
  private lassoMode = false;
  /** The mouse scheme and, for `toggle`, whether a left drag orbits (ADR-0021 3.4). */
  private navMode: NavMode = "paraview";
  private orbitMode = false;
  /** The drag being drawn and, when it ends, applied; null while there is none. */
  private marquee: Marquee | null = null;
  /** A left press that started on empty space, or with Alt: it is a marquee, not an orbit. */
  private marqueeArmed = false;
  private marqueeShape: MarqueeShape = "box";
  /**
   * The one-shot point pick (T-029): set by `requestPoint`, and cleared by the next click that
   * meets a face, by `cancelPoint` or by `dispose`. While it is set the canvas shows a crosshair
   * and a click answers it instead of picking a body or a face; a drag still orbits.
   */
  private pointPick: PointPick | null = null;
  /**
   * A plane a point pick may land on when the click meets no part (R5, the plot over a line on a
   * slice). **Measured on pipe3, looking along the pipe at the mid-plane disc:** the walls are
   * edge-on and the fluid body hidden, so `pointAt` met nothing and the two clicks never answered.
   * The slice is not a part, so the ray's crossing of its plane stands in for a face there.
   */
  private pointPlane: { axis: "x" | "y" | "z"; offset: number } | null = null;
  /**
   * Measure mode (T-098, `view.measure`): while it is on, every click on a face records a world
   * point. It rides the one-shot point pick above — `takeMeasurePoint` is the answer it is armed
   * with, and it re-arms itself after each point — so a click measures instead of picking, exactly
   * as the operation card's seed pick does.
   */
  private measuring = false;
  private probing = false;
  /** The measurement's two points, world metres. `measureA` alone means a first click has landed. */
  private measureA: THREE.Vector3 | null = null;
  private measureB: THREE.Vector3 | null = null;
  /** The completed measurement's arithmetic (measure.ts); null until both points are known. */
  private measurement: Measurement | ElementMeasurement | null = null;
  /**
   * The size of ONE element, when that is what the click asked for: an edge's length, a face's area,
   * a body's volume. Null while the measurement is a distance between two points.
   *
   * One of these two is set at any time, never both: a click either starts/continues a two-point
   * distance or reports one element's own size, and the readout's caption says which. Keeping them
   * separate rather than as one union is what lets `measureReadout` project a single `at` for the
   * isolated case and a midpoint for the pair.
   */
  private intrinsic: IntrinsicMeasurement | null = null;
  /**
   * The element the first click landed on, or null when only its surface point is known (T-113).
   * Kept beside `measureA` so the second click can choose between the element-aware measurement and
   * the plain point one without re-deriving what the first click was.
   */
  private measureElementA: MeasureElement | null = null;
  /** What the second click resolved to, for the Measure panel's description of the pair. */
  private measureElementB: MeasureElement | null = null;
  /**
   * What a measure click should prefer: the pick mode's own kind, mapped to the measure element it
   * means. Body mode has no per-element kind, so it prefers the FACE — a body's volume is reported
   * through the face the user pointed at, whose id names its solid.
   */
  private measureWant: "edge" | "vertex" | "face" | "body" = "face";

  /**
   * The decoded edge/vertex topology of each part that has one (ADR-0022 §15.2, T-058), filled by
   * the `TopologyController` the viewport owns. Kept here rather than in the controller because only
   * the scene knows a part's project matrix and depth, which the overlay and the screen-space hit
   * tests need; the controller owns the *fetch lifecycle* and nothing else.
   */
  private readonly topologies = new Map<string, PartTopology>();
  /** Part → its built vertex/edge incidence, for the double-click walk (§15.4). Built on first use. */
  private readonly incidences = new Map<string, Incidence>();
  /** The line and the end markers, remade with each point and taken out on a clear. */
  private measureLine: LineSegments2 | null = null;
  private measureDots: THREE.Points | null = null;
  /** The plot-over-line's segment and end markers (`lineLayer.ts`), set from the store's `line`. */
  private readonly lineLayer: LineLayer;
  /** Scratch for projecting the midpoint; the readout's x and y come from it. */
  private readonly measureMid = new THREE.Vector3();
  /** What was last published, so an unchanged readout is not sent again. */
  private measureSent = "";
  /** The 2D overlay the marquee is drawn on, over the canvas; made with the first marquee. */
  private overlay: HTMLCanvasElement | null = null;
  /** The probe's flag: hover and click ask the id buffer instead of the raycaster (T-032). */
  private idPickFromBuffer = false;
  /** The patch each id-buffer part index names, rebuilt with the numbers (T-032). */
  private idParts: (Part | null)[] = [];
  /** True once the scene has more patches than the id buffer can number and that has been logged. */
  private idOverflowLogged = false;
  /** The run of clicks: how many, when the last one was, and where it was. */
  private clickCount = 0;
  private clickAt = 0;
  private clickFrom: { x: number; y: number } = { x: 0, y: 0 };
  /** What was last reported to the store, so an unchanged hover line is not sent again. */
  private lastHoverInfo: HoverInfo | null = null;
  private frame = 0;
  private needsRender = true;
  private contextLost = false;
  private disposed = false;
  private width = 1;
  private height = 1;
  private pixelRatio = 1;
  private pixelRatioQuery: MediaQueryList | null = null;
  private lastAnchor: ScreenAnchor | null = null;
  /** Something changed that `flushContent` has to take care of. */
  private contentDirty = false;
  /** A source was added or removed since the last flush: the camera may need to frame again. */
  private orbitOnly = false;
  private sourcesChanged = false;
  private hadContent = false;
  private lastFlush = 0;
  /** What the last frames took to draw, in ms, smoothed: the content flushes keep their distance by it. */
  private frameCost = 0;
  private patchList: PatchMeta[] | null = null;
  /** True while the patches are drawn by batches; follows the patch count at every flush. */
  private batched = false;
  /** The merged edges no longer show what the shown patches' edges are. */
  private mergedStale = true;
  private lastMerge = 0;
  /** A look per kind, compiled ahead for drawing a batched part on its own (see `warmKinds`). */
  private readonly warmLooks = new Map<string, PartLook>();
  /** A translucent look per kind of the imported parts, compiled ahead (see `warmGlass`). */
  private readonly glassLooks = new Map<string, PartLook>();
  /** When `warmGlass` is due, in `performance.now()` milliseconds; 0 while it is not. */
  private glassDue = 0;
  /** When `warmPickSelect` is due, in `performance.now()` milliseconds; 0 while it is not. */
  private pickSelectDue = 0;
  /**
   * The batch mode the box-select programs were compiled for, or null while they are not. A compile
   * is for the life of the GL context, so a later change of content must not buy it again: warming
   * is a full id-pass render, and doing it after every content change cost one of those each time.
   */
  private pickSelectWarmed: boolean | null = null;
  private warmProbe: THREE.BufferGeometry | null = null;
  /** The camera changed since the last frame (input, a resize, a projection switch). */
  private viewChanged = true;
  /** The camera did not move in the frame being drawn. */
  private viewStill = false;
  /** The batches' kept draw lists are sorted for the current view (see `setSorting` in batch.ts). */
  private sortedView = false;
  /** Frames the scene draws are timed into this while `startFrames` is on. */
  private recording: FrameRecord | null = null;
  /** True while `captureExport` draws its frame: the helpers (outline, view cube, anchors) are left out of it. */
  private exporting = false;
  private timerQuery: TimerQuery | null | undefined = undefined;

  onHover: (name: string | null) => void = () => undefined;
  onPick: (hit: PickHit | null, modifiers: PickModifiers) => void = () => undefined;
  /**
   * A click resolved to a B-rep edge or vertex while Edge or Point mode is on (ADR-0022 §15.3,
   * T-058). The scene does the screen-space test because it alone has the camera, the matrix and the
   * canvas size; the viewport turns the answer into a store pick.
   */
  onTopologyPick: (part: string, kind: "edge" | "vertex", id: number, modifiers: PickModifiers) => void = () => undefined;
  /**
   * Called when the camera has moved, after the renderer has updated the matrices: the glyphs
   * rescale here so they keep a constant on-screen size (ADR-0022 §8.1). This is the one per-frame
   * glyph cost, and it is one matrix per boundary, never per face.
   */
  onCameraMove: (() => void) | undefined = undefined;
  /** What the pointer is over, for the status strip (ADR-0022 6.1); null when it is over nothing. */
  onHoverInfo: (info: HoverInfo | null) => void = () => undefined;
  /**
   * The edge or vertex under the pointer in a topology mode, or null (A5). Reported so the overlay
   * can draw a hover highlight: without it the hover set was always empty and only the *picked*
   * element was ever visible, which is why §15.3's hover feedback never appeared.
   */
  onTopologyHover: (hit: { part: string; kind: "edge" | "vertex"; id: number } | null) => void = () =>
    undefined;
  /**
   * The paint stroke crossed a new face (face mode) or body (body mode) (T-043); `ctrl` removes it
   * from the pick instead of adding it. Lives on the throttled hover pick, so it is called at most
   * once per hover interval and never per face of the mesh.
   */
  onPaint: (hit: PickHit | null, ctrl: boolean) => void = () => undefined;
  /**
   * A whole box or lasso result, applied in one store update through `pickMany` (T-032, ADR-0022 6.5):
   * bodies in body mode, faces in face mode, and the pick is left with one history entry for the
   * gesture. Never called empty.
   */
  onPickMany: (items: BulkPickItem[], how: "add" | "remove" | "replace") => void = () => undefined;
  /**
   * Projected bounds of the selected patch in CSS pixels (see `updateAnchor`); null when none is
   * selected or its centre is off screen.
   */
  onAnchor: (anchor: ScreenAnchor | null) => void = () => undefined;

  /**
   * The scale bar changed: how long the model is at the size it is drawn, or null when there is no
   * model to scale against. Called only when the figure actually changes, so a drag does not
   * re-render the overlay sixty times a second for the same label.
   */
  onScale: (bar: ScaleBar | null) => void = () => undefined;
  /**
   * The section handle was dragged: the plane's new offset along its own axis, in metres (T-065d).
   *
   * The scene does not write the store itself; the viewport turns this into `setSectionPlane`, the
   * same path the popover's slider uses, so a drag and a slider move are one edit and undo sees one.
   */
  onSectionDrag: (offset: number) => void = () => undefined;
  /**
   * The measurement changed: the distance as text and where its label belongs on screen, or null
   * when there is none (T-098). Like `onScale`, called only when the readout actually moves or
   * changes, so an orbit that leaves it where it was does not re-render the overlay.
   */
  onMeasure: (readout: MeasureReadout | null) => void = () => undefined;
  /** The Measure panel's content, sent whenever a click changes the measurement (null: tool off). */
  onMeasureDetails: (details: MeasureDetails | null) => void = () => undefined;
  /** The drawn groups changed (a source came, went, was replaced or restyled); called at most once per flush. */
  onContent: (patches: PatchMeta[]) => void = () => undefined;

  constructor(
    readonly canvas: HTMLCanvasElement,
    theme: SceneTheme,
  ) {
    this.theme = theme;
    this.accent.set(theme.accent);
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
      // The section's cut faces are marked in the stencil buffer (T-065b). three.js defaults this to
      // FALSE from r163 on, so without it the marks write into a buffer that does not exist, the fill
      // finds nothing marked, and the caps silently do not appear.
      stencil: true,
      // **NO `preserveDrawingBuffer`** (claude-win2's 0800 review, and he is right): the flag makes
      // the browser keep the drawing buffer after EVERY frame, which is a cost on the orbit path for
      // a capability used once per save. A capture does not need it when the draw and the read
      // happen in the SAME task - nothing may run between them, because that is what clears it.
    });
    // Section view (T-065): the renderer's GLOBAL clip list, not a plane pushed into each material.
    // A per-material plane would have to be found and updated on every material the viewport makes —
    // the parts' own looks, the batched instances, the tag overlays, the glyphs and the topology
    // overlay — and whichever one forgot would draw straight through the cut, which reads as a
    // rendering bug rather than a missing feature. The renderer's list applies to every material it
    // draws, so there is nothing to keep in step.
    //
    // `localClippingEnabled` is the switch that makes the global list apply to materials whose own
    // `clippingPlanes` is empty, which is all of ours.
    this.renderer.localClippingEnabled = true;
    this.pixelRatio = Math.min(window.devicePixelRatio, 2);
    this.renderer.setPixelRatio(this.pixelRatio);
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.toneMapping = THREE.NeutralToneMapping;
    this.renderer.toneMappingExposure = theme.exposure;
    this.renderer.autoClear = false;
    // A frame is several renders (shadow, scene, outline, view cube); the counters cover all of them.
    this.renderer.info.autoReset = false;

    // `Alt` + wheel cycles the candidates under the pointer and must not zoom (ADR-0022 6.1). The
    // camera rig adds its own wheel listener when it is made, and at the target every listener runs
    // in the order it was added: this one goes in first, so stopping the event here is what keeps
    // the zoom away even though OrbitControls does not look at `altKey`.
    canvas.addEventListener("wheel", this.onWheel, { passive: false });

    this.rig = new CameraRig(canvas);
    this.rig.onChange = () => {
      this.viewChanged = true;
      this.needsRender = true;
      this.reportScale();
      // **Tell the glyphs the camera moved** (found by a review audit of T-046-fix-4). `onCameraMove`
      // was declared, documented as "called when the camera has moved", and **assigned in
      // `Viewport.tsx` — but nothing ever invoked it.** So `currentGlyphs` ran only when the anchors
      // or the toggle changed, and the glyph scale was frozen at whatever camera the anchors were
      // fetched under: the constant-on-screen sizing did not hold during a zoom, and the edge-on floor
      // added by fix-4 did not apply while orbiting, which is when an edge-on face appears.
      //
      // A callback wired on the receiving side and never fired on the sending side is invisible: the
      // assignment reads as correct, the type checks, and the feature silently does nothing until the
      // camera happens to move for another reason. That is why the fix-4 tests passed and the defect
      // survived them — they exercise `glyphShrink`, which is pure and correct, not this path.
      this.onCameraMove?.();
    };
    this.gizmo = new ViewGizmo(canvas, this.rig.target, this.rig.camera, theme);
    this.rig.onProjectionChange = (camera) => {
      this.gizmo.setCamera(camera);
      this.needsRender = true;
    };

    this.buildEnvironment();
    this.applyLighting();
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(1.5, 2.5, 1.2);
    const fill = new THREE.DirectionalLight(0xbfd4ff, 0.3);
    fill.position.set(-2, 1, -1.5);
    this.scene.add(key, fill);
    /*
      **An ambient fallback for when the reflections are off** (measured: turning them off left the
      model nearly black). The environment map is the ONLY indirect light in this scene, so zeroing it
      removes most of what lights a surface facing away from the key - a "plain matte look" has to put
      something back, or the switch turns the model into a silhouette.

      **Off by default and off when reflections are on**, so today's look is unchanged: adding light
      that is always on would make the existing renders brighter for everybody, which is not what the
      owner asked for.
    */
    this.ambient = new THREE.AmbientLight(0xffffff, 0);
    this.scene.add(this.ambient);
    this.applyLighting();

    this.grid = new GroundGrid(theme);
    this.grid.setPixelRatio(this.pixelRatio);
    this.shadow = new ContactShadow(theme, PART_LAYER);
    this.scene.add(this.grid.mesh, this.shadow.mesh);
    this.outline = new SelectionOutline(theme.accent, OCCLUDER_LAYER);
    this.edgeMaterial = new LineMaterial({
      color: new THREE.Color(theme.edge),
      linewidth: EDGE_WIDTH,
      alphaToCoverage: true,
      toneMapped: false,
    });
    // The measurement (T-098): the accent colour, drawn over the model (`depthTest` off) so the
    // dimension is not swallowed by the part it measures. Widths and sizes are in pixels, so the pair
    // reads at any zoom; the line's `resolution` follows a resize in `setSize`, and both colours
    // follow a theme in `setTheme`.
    this.measureLineMaterial = new LineMaterial({
      color: new THREE.Color(theme.accent),
      linewidth: MEASURE_WIDTH,
      transparent: true,
      opacity: 0.95,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    this.measureLineMaterial.resolution.set(Math.max(1, this.width), Math.max(1, this.height));
    this.measureDotMaterial = new THREE.PointsMaterial({
      color: new THREE.Color(theme.accent),
      size: MEASURE_DOT,
      sizeAttenuation: false,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    this.faces = new FaceHighlight(theme.accent);
    this.scene.add(this.faces.mesh);
    this.selection = new SelectionHighlight(theme.accent, this.scene);
    // The topology overlay adds its four objects to the same scene; they start hidden, so body and
    // face mode draw nothing extra (§15.3: "no topology scan or extra draw in body/face mode").
    this.topologyOverlay = new TopologyOverlay(theme.accent, this.scene);
    // T-046-fix-1: the camera that must DRAW the glyphs is handed over, and the layer is enabled on
    // it there. It used to be enabled nowhere, so the glyphs were invisible: three.js draws an
    // object only when `camera.layers.test(object.layers)` holds, and the main camera stayed on
    // layer 0. Passing the camera makes that un-forgettable.
    this.glyphs = new GlyphLayer(this.scene, GLYPH_LAYER, this.rig.camera);
    this.topologyOverlay.setResolution(Math.max(1, this.width), Math.max(1, this.height));
    this.lineLayer = new LineLayer(this.scene, theme.accent, this.width, this.height);
    this.scene.add(this.selection.mesh);
    // The section's cut faces (T-065b): one object, invisible until a section is on, so the scene
    // carries it always and the frame needs no conditional to add or remove it.
    this.scene.add(this.sectionCaps.object);
    this.batchPool = new BatchedMeshPool(this.scene, PART_LAYER, OCCLUDER_LAYER, theme.isDark);
    this.idPass = new IdPass(PART_LAYER);
    // The id pass reads the last frame's camera, which is the one this frame would draw with.
    this.batchPool.onBindParts = (parts) => {
      this.idPass.setBatchParts(parts);
      this.idPass.refreshUniforms();
    };
    this.mergedEdges = new MergedEdges(this.edgeMaterial);
    this.scene.add(this.mergedEdges.object);

    canvas.addEventListener("pointermove", this.onPointerMove);
    canvas.addEventListener("pointerdown", this.onPointerDown);
    canvas.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("pointercancel", this.onPointerCancel);
    canvas.addEventListener("pointerleave", this.onPointerLeave);
    canvas.addEventListener("webglcontextlost", this.onContextLost);
    canvas.addEventListener("webglcontextrestored", this.onContextRestored);
    this.watchPixelRatio();
    this.timer.connect(document);
    this.frame = requestAnimationFrame(this.animate);
  }

  /** The camera in use: perspective or orthographic. */
  get camera(): ProjectionCamera {
    return this.rig.camera;
  }

  // ------------------------------------------------------------------ lifecycle
  /**
   * A small PNG of what is on screen, or "" when the canvas cannot give one.
   *
   * **Its own offscreen draw at the thumbnail's size**, rather than a downscale of the window: a
   * capture that resized the live canvas would flicker, and one taken at window size would store
   * megabytes per project for a 320x200 tile. The camera and the drawing buffer are put back
   * afterwards, so a capture is invisible to the user.
   *
   * **`renderFrame()` and `toDataURL()` are ONE task, with nothing awaited between them**, which
   * is what makes a capture work without `preserveDrawingBuffer` - the browser clears the drawing
   * buffer when the task ends, so a read in the same task still sees the pixels. The renderer
   * carries no global flag for this (claude-win2's 0800 review): keeping the buffer after every
   * frame would tax the orbit path for a capability used once per save.
   *
   * **Never throws**: a thumbnail is a nicety, and a project that cannot make one must still save.
   * The caller shows a placeholder for "".
   */
  captureThumbnail(width = 320, height = 200): string {
    const canvas = this.renderer.domElement;
    const before = { w: canvas.width, h: canvas.height };
    // **The free rectangle is cleared for the frame**, as the image export does: the guided card and the
    // panels leave the window's free area off to one side, and in a 320 px frame that rectangle sits
    // mostly outside the picture, so the model was centred out of it and the thumbnail came out blank.
    const free = this.rig.freeArea;
    try {
      this.renderer.setSize(width, height, false);
      this.rig.setFreeRect(null);
      this.rig.setSize(width, height);
      this.syncDrawingBuffer();
      this.renderFrame();
      const url = canvas.toDataURL("image/png");
      return url.startsWith("data:image/png") ? url : "";
    } catch {
      return "";
    } finally {
      // Put the live size and the free rectangle back and redraw, so the capture leaves no trace.
      try {
        this.renderer.setSize(before.w, before.h, false);
        this.rig.setFreeRect(free);
        this.rig.setSize(this.width, this.height);
        this.syncDrawingBuffer();
        this.renderFrame();
      } catch {
        /* a frame that cannot be redrawn is redrawn by the next one */
      }
    }
  }

  /**
   * The longest side, in pixels, a frame for the image export may have: what the graphics driver gives a
   * viewport and a render buffer. 4x of a wide window is 8000 px, which most drivers allow and some do not.
   */
  get maxExportSide(): number {
    const gl = this.renderer.getContext();
    const dims = gl.getParameter(gl.MAX_VIEWPORT_DIMS) as ArrayLike<number> | null;
    const buffer = gl.getParameter(gl.MAX_RENDERBUFFER_SIZE) as number;
    const viewport = dims ? Math.min(dims[0], dims[1]) : buffer;
    return Math.max(1, Math.min(viewport, buffer, 16384));
  }

  /** The window's size in CSS pixels: what scale 1 means for an export. */
  get liveSize(): { width: number; height: number } {
    return { width: this.width, height: this.height };
  }

  /**
   * One frame at exactly `width` x `height` device pixels, for a figure: a 2-D canvas holding the model on a
   * TRANSPARENT ground (the caller lays the background, the legend and the title around it), or null when the
   * frame cannot be drawn. The scale bar is measured for that frame and returned beside it.
   *
   * **What the frame leaves out**: the ground grid and its axes, the view cube, the selection outline and
   * highlight, the hovered and picked faces, edges and vertices, the measurement, the section's drag handle,
   * and the lift a selected part gets. A figure shows the results, not the tool that made them.
   *
   * **The same one-task rule as `captureThumbnail`**: the frame is drawn and copied to the 2-D canvas with
   * nothing awaited between (`drawImage` of a WebGL canvas reads the buffer the browser has not yet cleared),
   * so the renderer still carries no `preserveDrawingBuffer`. The copy, not `toBlob`, is what makes the PNG
   * encoding free to be asynchronous.
   *
   * **Pixel ratio 1 for the frame**: the figure's size is the pixel size asked for, not a multiple of the
   * screen's. The free rectangle is cleared too, so the model is centred on the picture and not on the part
   * of the window the cards leave open. Everything is put back, in a `finally`, and redrawn.
   * Never throws.
   *
   * **Only the drawing buffer is resized for the frame** (R3 review): the pick buffer (`idPass`) and the outline's
   * target are not drawn in an export frame, and sized to a 4x frame they were two more full-size render targets
   * on top of the drawing buffer, enough to exhaust a modest GPU's memory and leave a blank picture. They keep
   * their live size throughout, so there is nothing for the restore to resize back. The overlay canvas is a 2-D
   * canvas at the window's size, not the frame's, so it is left alone too (resizing it clears a marquee being drawn).
   *
   * **A lost context is reported as `"lost"`**, not as a picture: a frame that ran the GPU out of memory leaves
   * the context lost, and copying it would produce an empty canvas that encodes as a valid, blank PNG.
   */
  captureExport(width: number, height: number): { canvas: HTMLCanvasElement; scale: ScaleBar | null } | "lost" | null {
    if (this.contextLost || this.disposed || !(width >= 1) || !(height >= 1)) return null;
    const w = Math.round(width);
    const h = Math.round(height);
    const live = { w: this.width, h: this.height };
    const free = this.rig.freeArea;
    const ratio = this.pixelRatio;
    const edgeWidth = this.edgeMaterial.linewidth;
    const hidden: THREE.Object3D[] = [
      this.grid.mesh,
      this.faces.mesh,
      ...this.selection.objects,
      this.topologyOverlay.hover,
      this.topologyOverlay.picked,
      this.topologyOverlay.hoverPoints,
      this.topologyOverlay.pickedPoints,
    ];
    if (this.measureLine !== null) hidden.push(this.measureLine);
    if (this.measureDots !== null) hidden.push(this.measureDots);
    hidden.push(...this.lineLayer.objects);
    if (this.sectionHandle !== null) hidden.push(this.sectionHandle);
    const shown = hidden.map((object) => object.visible);
    const lifted = [...this.parts.values()].filter((part) => part.lift > 0);
    try {
      this.exporting = true;
      for (const object of hidden) object.visible = false;
      for (const part of lifted) paintLook(part.look, this.accent, this.theme.isDark, 0);
      this.pixelRatio = 1;
      this.renderer.setPixelRatio(1);
      this.renderer.setSize(w, h, false);
      this.rig.setFreeRect(null);
      this.rig.setSize(w, h);
      // Lines are drawn in pixels: the edges keep the weight they have on screen, at the figure's size.
      this.edgeMaterial.resolution.set(w, h);
      this.edgeMaterial.linewidth = edgeWidth * (w / Math.max(1, live.w));
      this.setStreamlineResolution(w, h, w / Math.max(1, live.w));
      this.renderer.getDrawingBufferSize(this.drawingBuffer);
      this.renderFrame();
      if (this.renderer.getContext().isContextLost()) return "lost";
      const scale = this.scaleForView(w);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const context = canvas.getContext("2d");
      if (context === null) return null;
      context.drawImage(this.renderer.domElement, 0, 0, w, h);
      return { canvas, scale };
    } catch {
      return null;
    } finally {
      this.exporting = false;
      hidden.forEach((object, i) => (object.visible = shown[i]));
      this.pixelRatio = ratio;
      this.renderer.setPixelRatio(ratio);
      this.renderer.setSize(live.w, live.h, false);
      this.rig.setSize(live.w, live.h);
      this.rig.setFreeRect(free);
      this.edgeMaterial.resolution.set(live.w, live.h);
      this.edgeMaterial.linewidth = edgeWidth;
      this.setStreamlineResolution(live.w, live.h);
      this.renderer.getDrawingBufferSize(this.drawingBuffer);
      this.idDirty = true;
      // Last, and guarded: a part that cannot be repainted or a frame that cannot be drawn must not leave the
      // size, the pixel ratio and the free rectangle above unrestored (R3 review).
      try {
        for (const part of lifted) this.paint(part);
      } catch {
        /* the next repaint of the part puts its look back */
      }
      try {
        this.renderFrame();
      } catch {
        /* the next frame draws it */
      }
    }
  }

  setSize(width: number, height: number): void {
    this.width = width;
    this.height = height;
    this.renderer.setSize(width, height, false);
    this.rig.setFrame(this.fitFrame());
    this.rig.setSize(width, height);
    this.syncDrawingBuffer();
    this.edgeMaterial.resolution.set(width, height);
    // The measurement's line is drawn in pixels too, so it needs the new size to keep its width.
    this.measureLineMaterial.resolution.set(Math.max(1, width), Math.max(1, height));
    this.topologyOverlay.setResolution(Math.max(1, width), Math.max(1, height));
    this.lineLayer.setResolution(width, height);
    this.setStreamlineResolution(width, height);
    // A glyph's world size is a pixel size over the canvas height, so a new height needs new glyphs even when the
    // camera stays where it is (the frame loop rescales them only on a camera move).
    this.rescaleGlyphs();
    this.idDirty = true;
    // Drawn in the same task as the resize, so the cleared canvas never reaches the screen.
    this.renderFrame();
    this.needsRender = false;
  }

  setTheme(theme: SceneTheme): void {
    this.theme = theme;
    this.accent.set(theme.accent);
    this.renderer.toneMappingExposure = theme.exposure;
    this.applyLighting();
    this.grid.setTheme(theme);
    this.shadow.setTheme(theme);
    this.outline.setAccent(theme.accent);
    this.edgeMaterial.color.set(theme.edge);
    // The measurement keeps the accent, like the selection: one colour for "this is yours" whatever
    // the theme, read from the token rather than baked in here.
    this.measureLineMaterial.color.set(this.accent);
    this.measureDotMaterial.color.set(this.accent);
    this.lineLayer.setAccent(this.accent);
    this.faces.setAccent(theme.accent);
    this.selection.setAccent(theme.accent);
    for (const part of this.parts.values()) this.paint(part);
    this.batchPool.setTheme(theme.isDark);
    this.gizmo.setTheme(theme);
    this.needsRender = true;
  }

  dispose(): void {
    this.disposed = true;
    this.pointPick = null;
    cancelAnimationFrame(this.frame);
    window.clearTimeout(this.hoverTimer);
    const canvas = this.canvas;
    canvas.removeEventListener("wheel", this.onWheel);
    canvas.removeEventListener("pointermove", this.onPointerMove);
    canvas.removeEventListener("pointerdown", this.onPointerDown);
    canvas.removeEventListener("pointerup", this.onPointerUp);
    canvas.removeEventListener("pointercancel", this.onPointerCancel);
    canvas.removeEventListener("pointerleave", this.onPointerLeave);
    canvas.removeEventListener("webglcontextlost", this.onContextLost);
    canvas.removeEventListener("webglcontextrestored", this.onContextRestored);
    this.pixelRatioQuery?.removeEventListener("change", this.onPixelRatioChange);
    this.timer.dispose();
    this.clear();
    // The measurement's objects live on the scene root, so `clear` (which takes the sources out)
    // leaves them: they go here, and the store is told the readout is gone rather than left to draw
    // a label for a measurement that no longer exists.
    this.clearMeasure();
    this.coolKinds();
    this.mergedEdges.dispose();
    this.faces.dispose();
    this.selection.dispose();
    this.grid.dispose();
    this.shadow.dispose();
    this.outline.dispose();
    this.edgeMaterial.dispose();
    this.measureLineMaterial.dispose();
    this.measureDotMaterial.dispose();
    this.lineLayer.dispose();
    this.gizmo.dispose();
    // **The section's own resources go with it** (Claude's defect 5). `SectionCaps.dispose` removes
    // every mark mesh from the parts and disposes the quad, the mark materials and the sphere; the
    // handle and its invisible pick sphere are disposed here, because the scene owns them.
    this.sectionCaps.dispose();
    this.disposeSectionHandle();
    this.environment?.dispose();
    this.idPass.dispose();
    this.overlay?.remove();
    this.overlay = null;
    this.rig.dispose();
    this.renderer.dispose();
  }

  // ------------------------------------------------------------------ content
  /** Removes every source. */
  clear(): void {
    this.clearFaceHover();
    this.resetCycle();
    this.facePick = new Map();
    this.bodyPick = { active: null, others: [] };
    this.selectionKey = null;
    this.selection.clear();
    // The batches go in one step; taking their parts out one by one would only compact them first.
    this.batchPool.clear();
    for (const parts of this.sources.values()) {
      for (const part of parts) {
        part.batched = null;
        this.discard(part);
      }
    }
    this.sources.clear();
    this.parts.clear();
    this.vertexTints.clear();
    for (const source of [...this.cellEdges.keys()]) this.dropCellEdges(source);
    for (const source of [...this.contourLines.keys()]) this.dropContours(source);
    this.batched = false;
    this.idIndicesDirty = true;
    this.idDirty = true;
    this.idParts = [];
    this.mergedEdges.clear();
    this.shadow.hide();
    this.shadow.mesh.visible = this.shadowOn;
    this.grid.hide();
    this.setAnchor(null);
    this.contentDirty = false;
    this.sourcesChanged = false;
    this.hadContent = false;
    this.patchList = null;
    this.needsRender = true;
  }

  /**
   * Shows `groups` as the source `source`, replacing what the source showed before. A group that
   * replaces one of the same kind takes over its mesh and keeps its highlight, so a fine mesh
   * arriving over a coarse one does not blink. `names` gives the patch name of each group; a name
   * another source already uses is made unique here, but the caller is expected to send free ones.
   */
  setSource(source: string, groups: readonly LoadedGroup[], names: readonly string[], style: PartStyle): void {
    const previous = this.sources.get(source) ?? [];
    for (const part of previous) if (this.parts.get(part.name) === part) this.parts.delete(part.name);
    const next: Part[] = [];
    // Where each group's vertices lie in the source's vertex tint, known before any group is built.
    const layout = tintLayout(groups.map((group) => group.vertices));
    groups.forEach((group, index) => {
      const name = this.freeName(names[index] ?? group.name);
      const reused = previous[index];
      const unlit = style.unlit === true;
      const at = { offset: layout.offsets[index], total: layout.total };
      let part: Part;
      if (reused && reused.group.kind === group.kind && reused.unlit === unlit) {
        part = this.refill(reused, group, at);
      } else {
        if (reused) this.discard(reused);
        // Built at its opacity, so a batched scene puts it straight into the batch of its bucket.
        part = this.build(source, group, opacityBucket(style.opacity), at, unlit);
      }
      part.name = name;
      part.mesh.name = name;
      this.restyle(part, style);
      part.meta = this.metaFor(source, part.name, part.color, part.group);
      this.parts.set(name, part);
      next.push(part);
    });
    for (let i = groups.length; i < previous.length; i++) this.discard(previous[i]);
    // A cut (slice, cap, iso) coming or going is not a reason to re-frame: the first show fits the body once (R4c).
    if (!this.sources.has(source) && !isCutSource(source)) this.sourcesChanged = true;
    this.sources.set(source, next);
    this.touch();
  }

  /** Drops a source and frees its meshes. */
  removeSource(source: string): void {
    const parts = this.sources.get(source);
    if (!parts) return;
    for (const part of parts) this.discard(part);
    this.sources.delete(source);
    this.vertexTints.delete(source);
    this.dropCellEdges(source);
    this.dropContours(source);
    if (!isCutSource(source)) this.sourcesChanged = true;
    this.touch();
  }

  hasSource(source: string): boolean {
    return this.sources.has(source);
  }

  /** The patch names of a source's groups, in transport order. */
  patchNames(source: string): string[] {
    return (this.sources.get(source) ?? []).map((part) => part.name);
  }

  /** Applies a source's colour, visibility and opacity parameters without touching its meshes. */
  setSourceStyle(source: string, style: PartStyle): void {
    const parts = this.sources.get(source);
    if (!parts) return;
    let changed = false;
    for (const part of parts) {
      if (!this.restyle(part, style)) continue;
      part.meta = this.metaFor(source, part.name, part.color, part.group);
      changed = true;
    }
    if (!changed) return;
    this.applyVisibility();
    this.touch();
  }

  /**
   * Renames patches, old name to new. The groups keep their meshes and highlights; the scene's own
   * copies of the selection, hover, isolation and hidden set follow, so nothing is lost on the way.
   */
  renamePatches(renames: ReadonlyMap<string, string>): void {
    const moved: Part[] = [];
    for (const from of renames.keys()) {
      const part = this.parts.get(from);
      if (!part) continue;
      this.parts.delete(from);
      moved.push(part);
    }
    for (const part of moved) {
      const to = renames.get(part.name) ?? part.name;
      part.name = to;
      part.mesh.name = to;
      part.meta = this.metaFor(part.source, to, part.color, part.group);
      this.parts.set(to, part);
    }
    const follow = (name: string | null): string | null => (name === null ? null : (renames.get(name) ?? name));
    this.selected = follow(this.selected);
    this.hovered = follow(this.hovered);
    this.isolated = follow(this.isolated);
    this.hidden = new Set([...this.hidden].map((name) => follow(name) ?? name));
    this.applyVisibility();
    this.applyHighlights();
    this.touch();
  }

  /** What the store is told about every drawn group, in source and transport order. */
  patches(): PatchMeta[] {
    if (this.patchList === null) {
      const list: PatchMeta[] = [];
      for (const parts of this.sources.values()) for (const part of parts) list.push(part.meta);
      this.patchList = list;
    }
    return this.patchList;
  }

  /** Animated fit of the visible patches, keeping the view direction. */
  fit(): void {
    this.rig.fit();
  }

  /** Explicit fit-all action, using the same visible-body framing as ordinary Fit. */
  fitAll(): void {
    this.fitEverything = true;
    try {
      this.rig.fit();
    } finally {
      this.fitEverything = false;
    }
  }

  /** Set while `fitAll` runs, so `fitCorners` frames the whole model once for that call. */
  private fitEverything = false;

  /** Set while `fitSources` runs: the sources `fitCorners` frames, null for its own rule. */
  private fitOnly: ReadonlySet<string> | null = null;

  /**
   * Animated fit of the parts drawn from `sources` (null: the ordinary fit), optionally seen from `from` (the
   * direction the camera sits in, as `fitFrom`). The Looks card asks for the fluid body's patches
   * (`fitTargets.ts` `fitSourcesFor`), which no key of the viewport does. The rig keeps those fit points until
   * the next visibility change or flush replaces them, so a resize while the move runs re-aims at the same box.
   */
  fitSources(sources: readonly string[] | null, from?: readonly [number, number, number]): void {
    this.fitOnly = sources === null ? null : new Set(sources);
    try {
      // **The centre moves with the points.** The rig's centre is the one the last flush set (the sphere of every part
      // THEN), and a fit aims at it: with patches that landed after that flush the frame sat off the body (R4c).
      const corners = this.fitCorners(true);
      this.rig.setFitPoints(corners, corners.length > 0 ? new THREE.Box3().setFromPoints(corners).getCenter(new THREE.Vector3()) : undefined);
      // A face-on view turns the camera's up so the longer in-plane axis of the targets' box runs across the screen (R4e).
      const box = corners.length > 0 ? new THREE.Box3().setFromPoints(corners) : null;
      const up = from === undefined ? null : upFor([from[0], from[1], from[2]], box === null ? null : { min: [box.min.x, box.min.y, box.min.z], max: [box.max.x, box.max.y, box.max.z] });
      this.rig.fit(from === undefined ? undefined : new THREE.Vector3(from[0], from[1], from[2]), up === null ? undefined : new THREE.Vector3(up[0], up[1], up[2]));
    } finally {
      this.fitOnly = null;
    }
  }

  /** The sources the scene holds, in the order they arrived: what a fit can be aimed at. */
  sourceIds(): string[] {
    return [...this.sources.keys()];
  }

  /** Animated fit of the visible patches seen from the direction `(x, y, z)` points from (a view of the model along it). For the review harness. */
  fitFrom(x: number, y: number, z: number): void {
    this.rig.fit(new THREE.Vector3(x, y, z));
  }

  setOrthographic(on: boolean): void {
    this.rig.setOrthographic(on);
  }

  /**
   * 2D results (ADR-0031): look face-on at the meshed plane, orthographic, with rotation off (pan and zoom stay), or,
   * with null, give rotation back. `from` is the side the camera sits on and `up` the screen's up (`viewport/twoD.ts`).
   */
  lockToPlane(view: { from: readonly [number, number, number]; up: readonly [number, number, number] } | null): void {
    this.rig.controls.enableRotate = view === null;
    if (view === null) return;
    this.rig.setOrthographic(true);
    this.rig.fit(new THREE.Vector3(view.from[0], view.from[1], view.from[2]), new THREE.Vector3(view.up[0], view.up[1], view.up[2]));
  }

  /** Where the view cube is drawn (a corner of the canvas and the distances from its edges), or null to hide it. */
  setGizmo(placement: GizmoPlacement | null): void {
    this.gizmo.setPlacement(placement);
    this.needsRender = true;
  }

  /** The ground grid, with its axes and floor tint, shows or hides; the contact shadow stays. */
  setGrid(on: boolean): void {
    this.grid.setEnabled(on);
    this.needsRender = true;
  }

  /**
   * The red/green/blue axis lines, separately from the ground grid.
   *
   * **Separate because they answer different questions** - "where is the origin" and "how big is
   * this" - and a user who wants a clean ground usually still wants to know which way is X.
   */
  setAxes(on: boolean): void {
    this.grid.setAxes(on);
    this.needsRender = true;
  }

  /**
   * The contact shadow under the parts.
   *
   * **Removing the mesh rather than making it transparent**: a faded shadow is a shadow that still
   * costs a draw call, and the owner's request is as much about the frame cost as about the look.
   */
  setShadow(on: boolean): void {
    this.shadowOn = on;
    this.shadow.mesh.visible = on;
    this.needsRender = true;
  }

  /**
   * The environment map, which is what a material reflects.
   *
   * **Intensity rather than the texture**: `scene.environment` is also the only source of soft
   * indirect light, so clearing it would make the model go black. Zeroing `environmentIntensity` and
   * raising the ambient lift is the "plain matte look" the owner asked for, and it keeps the model
   * readable.
   */
  setReflections(on: boolean): void {
    this.reflectionsOn = on;
    this.applyLighting();
    this.needsRender = true;
  }

  /** The part edge lines. */
  setEdges(on: boolean): void {
    this.edgesOn = on;
    this.applyViewMode();
    this.needsRender = true;
  }

  /**
   * The environment intensity and the ambient lift, from the theme and the reflections switch.
   *
   * **One function so the two cannot disagree**: `setTheme` and `setReflections` both land here, and a
   * second copy of this arithmetic is how a theme change came to switch reflections back on.
   */
  private applyLighting(): void {
    this.scene.environmentIntensity = this.reflectionsOn ? this.theme.environment : 0;
    /*
      **The lift that replaces the indirect light the environment was giving, scaled to the THEME**
      (claude-win2's GUI review item 2: *"reflections off = nearly black model… the matte ambient is
      far too weak"*).

      **A fixed 0.55 was measured as too dark**, and the reason is arithmetic: the environment is
      `0.9` in the dark theme and `1.25` in the light one, so one constant cannot stand in for both.
      This is `environment × AMBIENT_SHARE`, which keeps the matte look in the same brightness class as
      the reflective one in each theme.

      **Short of 1:1 on purpose**: the environment lights a surface by its ORIENTATION (that is what
      makes a rounded part read), while ambient lights every face equally. Matching the two exactly
      would make a cube's faces the same shade and dissolve the shape, so the share is below one and
      the key light keeps carrying the form.
    */
    if (this.ambient !== undefined) {
      this.ambient.intensity = this.reflectionsOn ? 0 : this.theme.environment * AMBIENT_SHARE;
    }
    this.scene.traverse((obj) => {
      const withEnv = obj as THREE.Mesh & { material?: THREE.MeshStandardMaterial };
      if (!withEnv.material || !(withEnv.material as THREE.MeshStandardMaterial).isMeshStandardMaterial) return;
      const material = withEnv.material as THREE.MeshStandardMaterial;
      // A matte look needs some lift where the environment was lighting the surface.
      material.envMapIntensity = this.reflectionsOn ? 1 : 0;
    });
  }

  setViewMode(mode: ViewMode): void {
    if (mode === this.viewMode) return;
    this.viewMode = mode;
    this.applyViewMode();
  }

  /**
   * Which layers the view draws (T-211): the geometry parts, and the mesh patches (kind `mesh`). The
   * Mesh stage shows the mesh and sets the geometry aside, and leaving it brings the geometry back
   * exactly as it was, because this is a view filter over the parts' own visibility (their `visible`
   * parameter, Hide and Isolate are not touched, so nothing is journaled and nothing is undone).
   */
  setLayerFilter(layers: { geometry: boolean; mesh: boolean }): void {
    if (layers.geometry === this.layers.geometry && layers.mesh === this.layers.mesh) return;
    this.layers = { geometry: layers.geometry, mesh: layers.mesh };
    this.applyVisibility();
    this.refreshSectionCaps();
  }

  /**
   * Draws a mesh patch's cell edges (`edges.ts` `edgeLines`, in the feature edges' own material and so
   * their theme colour), or removes them with `null`. `segments` is six floats per segment, in metres.
   * They are shown while `setCellEdgesShown` is on and the source's patch is shown.
   *
   * **`own`: the source's edges answer to the caller, not to the Display menu's "Mesh with cells".** A slice of
   * the results is a figure, and the mode (left on from the Mesh stage) drew its cell edges as black dashes
   * through the colours; the slice layer passes `own` and gives the edges only when the slice's own Edges switch is
   * on, so the walls can show their cells and the cut stay clean, or the other way round.
   */
  setSourceCellEdges(source: string, segments: Float32Array | null, own = false): void {
    this.dropCellEdges(source);
    if (segments === null || !this.sources.has(source)) return;
    const lines = edgeLines(segments, this.edgeMaterial, `${source}:cells`);
    if (lines === null) return;
    this.scene.add(lines);
    this.cellEdges.set(source, lines);
    if (own) this.ownCellEdges.add(source);
    this.applyCellEdges();
  }

  /**
   * Draws contour lines on a source (a slice of the results), or removes them with `null`: `positions` is six
   * floats per segment, in metres, and `colours` the sRGB bytes of its two ends, three per end, or null for
   * one line colour (`neutral`, `#rrggbb`). They are plain depth-tested lines of the width the GPU draws
   * (one pixel), kept with the source and dropped with it. The caller lifts them off the surface they
   * follow, so they do not fight it for the depth buffer.
   */
  setSourceContours(source: string, positions: Float32Array | null, colours: Uint8Array | null, neutral = "#101418"): void {
    this.dropContours(source);
    if (positions === null || positions.length === 0 || !this.sources.has(source)) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    let material: THREE.LineBasicMaterial;
    if (colours !== null && colours.length === positions.length) {
      // The bytes are sRGB, as the surface's tint is; the shader wants linear.
      const linear = new Float32Array(colours.length);
      const colour = new THREE.Color();
      for (let i = 0; i + 2 < colours.length; i += 3) {
        colour.setRGB(colours[i] / 255, colours[i + 1] / 255, colours[i + 2] / 255, THREE.SRGBColorSpace);
        linear[i] = colour.r;
        linear[i + 1] = colour.g;
        linear[i + 2] = colour.b;
      }
      geometry.setAttribute("color", new THREE.BufferAttribute(linear, 3));
      material = new THREE.LineBasicMaterial({ vertexColors: true });
    } else {
      material = new THREE.LineBasicMaterial({ color: new THREE.Color(neutral) });
    }
    const lines = new THREE.LineSegments(geometry, material);
    lines.name = `${source}:contours`;
    lines.frustumCulled = false;
    lines.renderOrder = 2;
    this.scene.add(lines);
    this.contourLines.set(source, lines);
    this.needsRender = true;
  }

  /** True when the source has contour lines. */
  hasContours(source: string): boolean {
    return this.contourLines.has(source);
  }

  private dropContours(source: string): void {
    const lines = this.contourLines.get(source);
    if (lines === undefined) return;
    this.scene.remove(lines);
    lines.geometry.dispose();
    (lines.material as THREE.Material).dispose();
    this.contourLines.delete(source);
    this.needsRender = true;
  }

  /** The Display menu's "Mesh with cells": every source's cell edges are drawn, or hidden again (kept for the next time). */
  setCellEdgesShown(on: boolean): void {
    if (on === this.cellEdgesOn) return;
    this.cellEdgesOn = on;
    this.applyCellEdges();
  }

  /** True when the source has cell edges loaded, drawn or not. */
  hasCellEdges(source: string): boolean {
    return this.cellEdges.has(source);
  }

  private dropCellEdges(source: string): void {
    const lines = this.cellEdges.get(source);
    if (lines === undefined) return;
    this.scene.remove(lines);
    lines.geometry.dispose();
    this.cellEdges.delete(source);
    this.ownCellEdges.delete(source);
    this.needsRender = true;
  }

  /** Cell edges follow their patch: drawn while the mode is on and the patch is shown. */
  private applyCellEdges(): void {
    for (const [source, lines] of this.cellEdges) {
      lines.visible = (this.cellEdgesOn || this.ownCellEdges.has(source)) && (this.sources.get(source) ?? []).some((part) => part.shown);
    }
    this.needsRender = true;
  }

  /**
   * Set, move or clear the section plane (T-065); `null` removes the cut.
   *
   * The plane is built in **world space** and handed to the renderer's global clip list, so it cuts
   * the parts wherever they are: the scene applies each part's own transform when it draws, and the
   * clip plane is not part of that transform, which is exactly what "cut the model at Z = 0" means.
   *
   * `constant` is the plane's signed distance from the origin along its normal, in metres, with the
   * sign inverted from the user-facing `offset`: three.js keeps the half-space where
   * `normal · point + constant > 0`, so a user-facing "keep +Z" offset `d` needs `constant = -d`.
   */
  setSection(plane: { axis: "x" | "y" | "z"; offset: number; invert: boolean } | null): void {
    if (plane === null) {
      const wasClipping = (this.renderer.clippingPlanes ?? []).length > 0;
      this.renderer.clippingPlanes = [];
      if (wasClipping) this.idPass.material.needsUpdate = true;
      this.sectionCaps.setSection(null);
      this.activeSection = false;
      this.sectionPlane = new THREE.Plane();
      if (this.sectionHandle !== null) this.sectionHandle.visible = false;
      // **The marks go, they are not merely hidden** (Claude's defect 5). `marksByPart` kept a mesh
      // per part per colour alive — and kept removed parts alive with it, because the marks are
      // children of the parts — for as long as the section stayed off. Removing the section is the
      // user saying they are done with it, so the resources are released and rebuilt if it returns.
      this.sectionCaps.clearMarks();
      this.sectionCapsKey = "";
      // **The id buffer is stale here too** (Claude's defect 8). `needsRender` redraws the visible
      // FRAME, but the id buffer is a separate cached render that `renderIdPass` re-draws only when
      // `idDirty` says so — and this branch, unlike the section-ON one below, never set it. So after
      // turning a cut off, a box or a lasso read the picture taken WHILE the cut was active: it saw
      // only the near half and silently omitted every face the removed half had been covering, even
      // though those faces were back on screen. `needsRender` alone cannot fix that, which is why the
      // two flags are separate.
      //
      // The OFF branch is the mirrored case of the ON branch's line, and the ON branch's comment
      // measures the same symptom from the other direction.
      this.idDirty = true;
      this.needsRender = true;
      return;
    }
    const sign = plane.invert ? -1 : 1;
    const normal = plane.axis === "x"
      ? new THREE.Vector3(sign, 0, 0)
      : plane.axis === "y"
        ? new THREE.Vector3(0, sign, 0)
        : new THREE.Vector3(0, 0, sign);
    // **Invert is the normal's sign, nothing else.** A clip plane is a half-space, so reversing the
    // normal keeps the other side and leaves the cut exactly where the user put it. Negating the
    // OFFSET instead would also change which side shows, but it would move the plane to the mirrored
    // position — a cut on the far side of the model, which is not what "flip this" means.
    //
    // `constant` still uses the user-facing offset unscaled: the offset is measured along the axis,
    // not along the kept side, so it does not flip with the normal.
    const next = new THREE.Plane(normal, -plane.offset * sign);
    // Kept as well as handed to the renderer: the handle needs the plane to place itself, and a
    // second computation of it from the state would be one more place to get the sign wrong.
    this.sectionPlane = next;
    this.activeSection = true;
    this.sectionAxis = plane.axis;
    // A NEW array each time: three.js reads the list when it compiles a program, and mutating the
    // existing array in place left the shader holding the first plane it ever saw, so moving the
    // slider cut nothing. Replacing the array is what makes the renderer re-evaluate it.
    // **The id material's program is keyed on the clip count, and it compiles on FIRST render.**
    // `#if NUM_CLIPPING_PLANES > 0` guards the whole clipping chunk, so a program compiled while the
    // list was empty has the code baked out — and the cached key holds that zero, so no later plane
    // can revive it. The id pass is the one material the renderer does not compile for us, so it is
    // the one that needs telling. (`three` recompiles a material whose `needsUpdate` is set.)
    const wasClipping = (this.renderer.clippingPlanes ?? []).length > 0;
    this.renderer.clippingPlanes = [next];
    if (!wasClipping) this.idPass.material.needsUpdate = true;
    // The caps are drawn from the same plane, so they follow the flip without a second decision here.
    // Moving the plane updates the quad's transform and the clip uniform, which is what `setSection`
    // does; it does NOT change which parts are marked, in what colour, or how many colours there are.
    this.sectionCaps.setSection(next);
    // **Rebuild only when the marked set actually changes** (Claude's defect 8). This used to clear
    // `sectionCapsKey` first, which forced `refreshSectionCaps` to rebuild on EVERY move — the exact
    // opposite of what the guard is for, and the reason a drag cost 5.63 ms a move (worst 11.10 ms,
    // measured over 40 moves) rebuilding two meshes per part per colour for a plane that had not
    // changed which parts are cut. Leaving the key alone lets the signature decide, so a move costs a
    // string compare and the first move after a part arrives or is recoloured still rebuilds.
    this.refreshSectionCaps();
    // **The id buffer is now out of date** (defect 7c). It is a CACHED image of the parts, redrawn
    // only when `idDirty` says so, and `setSection` did not set it: the id pass kept the picture from
    // before the cut, so a box or lasso selected faces the drawn frame had already removed — the
    // clipping chunks in `idbuffer.ts` were correct but never exercised, because the pass that would
    // have drawn them never ran. Measured with the cut on: the id buffer was byte-identical to the
    // uncut one (3806px/9f/2parts), and identical again after the section was removed.
    this.idDirty = true;
    this.needsRender = true;
  }

  // --------------------------------------------------------------- the section's drag handle (T-065d)

  /**
   * The handle drawn on the model at the cut, or null while no section is shown.
   *
   * A cone on a short stem along the plane's normal, at the cut's centre. It is picked by the ordinary
   * raycast against `sectionHandle`, and a press on it starts a slide instead of an orbit — the same
   * "the controls are off for this drag" arrangement the paint stroke uses, so the two cannot fight
   * for the pointer.
   */
  private sectionHandle: THREE.Mesh | null = null;
  /** Where a handle drag began: the offset then, and the axis parameter under the pointer. */
  private handleDrag: { offset: number; startAlong: number; axis: THREE.Vector3 } | null = null;

  /**
   * The solid the handle is drawn for: a cone whose point is along +Y, turned to the plane's normal.
   *
   * Sized from the model so it is the same apparent size on a 50 mm pipe and a 5 m duct: a fixed world
   * size would be invisible on one and swallow the other.
   */
  private buildSectionHandle(span: number): THREE.Mesh {
    if (this.sectionHandle !== null) {
      this.sectionHandle.geometry.dispose();
      (this.sectionHandle.material as THREE.Material).dispose();
      this.scene.remove(this.sectionHandle);
    }
    const radius = Math.max(span * 0.035, 1e-4);
    const height = radius * 2.4;
    const geometry = new THREE.ConeGeometry(radius, height, 20);
    const material = new THREE.MeshBasicMaterial({
      color: this.theme.accent,
      // Drawn over the model whatever is in front: a handle the model can hide is a handle the user
      // cannot find, and its whole job is to be visible at the cut. `depthTest: false` with a high
      // render order does that without touching the parts' materials.
      depthTest: false,
      transparent: true,
      opacity: 0.95,
    });
    const mesh = new THREE.Mesh(geometry, material);
    // **A pick target bigger than the drawn cone.** A cone is thin at its tip, and the raycast that
    // starts a drag grazes past it: measured, a press on the tip did nothing while a press 20 px lower
    // moved the cut. A sphere a little larger than the cone, invisible, gives the handle the grab area
    // a hand expects without drawing anything bigger. It is a CHILD, so it carries the cone's transform
    // for nothing, and it shares the handle's layer so it is invisible and only ever raycast.
    const target = new THREE.Mesh(
      new THREE.SphereGeometry(radius * 1.9, 12, 8),
      new THREE.MeshBasicMaterial({ visible: false }),
    );
    target.layers.set(SECTION_HANDLE_LAYER);
    target.name = "section-handle-target";
    mesh.add(target);
    // Its own layer, so the id pass, the outline and the caps' own passes never draw it (see the
    // layer note on the caps: layers are per-object and not inherited).
    mesh.layers.set(SECTION_HANDLE_LAYER);
    mesh.renderOrder = MEASURE_ORDER + 5;
    mesh.frustumCulled = false;
    this.rig.camera.layers.enable(SECTION_HANDLE_LAYER);
    // **The raycaster has a layer mask of its own, and it defaults to layer 0 only.** Putting the
    // handle on its own layer kept it out of every other pass, which is what the layer is for — but it
    // also kept it out of the RAYCAST, so `intersectObject(handle)` returned nothing, the press fell
    // through to the model, and the handle could not be grabbed at all while looking perfectly solid
    // on screen. Enabling the layer here is what makes it pickable again.
    this.raycaster.layers.enable(SECTION_HANDLE_LAYER);
    this.scene.add(mesh);
    this.sectionHandle = mesh;
    return mesh;
  }

  /** Put the handle on the cut, pointing along the kept side, or hide it with no section. */
  private placeSectionHandle(bounds: THREE.Box3): void {
    if (!this.activeSection) {
      if (this.sectionHandle !== null) this.sectionHandle.visible = false;
      return;
    }
    const size = new THREE.Vector3();
    bounds.getSize(size);
    const span = Math.max(size.x, size.y, size.z, 1e-6);
    const handle = this.sectionHandle ?? this.buildSectionHandle(span);
    const plane = this.sectionPlane;
    const normal = plane.normal;
    const centre = new THREE.Vector3();
    bounds.getCenter(centre);
    const onPlane = centre.clone().addScaledVector(normal, -plane.distanceToPoint(centre));
    // The cone's point is along +Y in its own space; `setFromUnitVectors` turns that to the normal, so
    // the arrow points at the half the section KEEPS. Inverting the section flips the normal, so the
    // arrow follows the flip without a second rule here.
    handle.position.copy(onPlane).addScaledVector(normal, span * 0.02);
    handle.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal);
    handle.visible = true;
  }

  /**
   * Start a section drag if the press is on the handle; false otherwise, so the caller carries on.
   *
   * The camera is disabled for the length of the drag, exactly as a paint stroke does it: OrbitControls
   * has already seen this pointerdown and ignores every move while disabled, so the two cannot both
   * respond to one gesture.
   */
  private beginSectionDrag(e: PointerEvent): boolean {
    const handle = this.sectionHandle;
    if (handle === null || !handle.visible) return false;
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.set(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(this.pointer, this.rig.camera);
    // `recursive = true`: the handle's grabbable area is its invisible pick sphere, a CHILD (see
    // `buildSectionHandle`), and a non-recursive raycast never tests a child at all.
    if (this.raycaster.intersectObject(handle, true).length === 0) return false;
    // A generous pick radius: the cone is small on a large model, and a handle that is hard to hit is
    // a handle nobody uses. The raycast above is exact, so this only widens the target by growing the
    // geometry the user clicks, which is done below rather than by a tolerance here.
    const along = this.alongAxis(e.clientX, e.clientY);
    if (along === null) return false;
    this.handleDrag = { offset: this.currentOffset(), startAlong: along, axis: this.axisDirection() };
    this.rig.controls.enabled = false;
    this.setHover(null);
    return true;
  }

  /**
   * The axis parameter under a client position: where the pointer's ray comes closest to the axis.
   *
   * `axisDistance` does the arithmetic (and is tested without a scene); this only builds the two lines
   * from the camera and the canvas. Null when the ray looks down the axis, where the drag has no
   * meaningful direction — the caller then leaves the cut where it is.
   */
  private alongAxis(clientX: number, clientY: number): number | null {
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.set(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(this.pointer, this.rig.camera);
    const origin = this.raycaster.ray.origin;
    const direction = this.raycaster.ray.direction;
    return axisDistance(
      { origin: { x: origin.x, y: origin.y, z: origin.z }, direction: { x: direction.x, y: direction.y, z: direction.z } },
      { origin: { x: 0, y: 0, z: 0 }, direction: this.axisDirection() },
    );
  }

  /**
   * The direction the cut slides, **including the half that is kept**.
   *
   * The section's `invert` flips which side is kept, so the axis direction carries that sign: dragging
   * the handle moves the cut the way the arrow points, whichever half is showing. Without the sign an
   * inverted section would drag backwards, which reads as the handle being broken.
   */
  private axisDirection(): THREE.Vector3 {
    const sign = this.sectionPlane.normal.dot(new THREE.Vector3(
      this.sectionAxis === "x" ? 1 : 0,
      this.sectionAxis === "y" ? 1 : 0,
      this.sectionAxis === "z" ? 1 : 0,
    )) < 0 ? -1 : 1;
    return new THREE.Vector3(
      this.sectionAxis === "x" ? sign : 0,
      this.sectionAxis === "y" ? sign : 0,
      this.sectionAxis === "z" ? sign : 0,
    );
  }

  /** The plane's offset along its axis, from the plane the renderer holds. */
  private currentOffset(): number {
    const axis = new THREE.Vector3(
      this.sectionAxis === "x" ? 1 : 0,
      this.sectionAxis === "y" ? 1 : 0,
      this.sectionAxis === "z" ? 1 : 0,
    );
    // `plane.constant` is `-offset · sign`, so dividing the two signs back out gives the offset the
    // popover shows. Reading it from the plane keeps one source of truth: the cut the user sees.
    const sign = axis.dot(this.sectionPlane.normal) < 0 ? -1 : 1;
    return -this.sectionPlane.constant * sign;
  }

  /** Extend the drag: the cut moves by however far the pointer travelled along the axis. */
  private dragSectionHandle(e: PointerEvent): void {
    const drag = this.handleDrag;
    if (drag === null) return;
    const along = this.alongAxis(e.clientX, e.clientY);
    if (along === null) return;
    this.onSectionDrag(draggedOffset(drag.offset, along - drag.startAlong));
  }

  /** End a section drag, if one was running. The camera comes back with the button. */
  private endSectionDrag(): boolean {
    if (this.handleDrag === null) return false;
    this.handleDrag = null;
    this.rig.controls.enabled = true;
    return true;
  }

  /**
   * Release the drag handle and its pick sphere, and take them out of the scene.
   *
   * Called by `dispose` and by `removeSectionHandle`, so removing the section and tearing down the
   * scene do the same thing and neither can leave a geometry on the GPU or a mesh in the scene.
   */
  private disposeSectionHandle(): void {
    const handle = this.sectionHandle;
    if (handle === null) return;
    this.scene.remove(handle);
    handle.traverse((child) => {
      // The pick sphere is a child with its own geometry and material; a traverse disposes both
      // without this having to know how they are nested.
      const mesh = child as THREE.Mesh;
      if (mesh.isMesh || (mesh as unknown as THREE.Line).isLine) {
        mesh.geometry?.dispose();
        const material = mesh.material;
        if (Array.isArray(material)) for (const m of material) m.dispose();
        else material?.dispose();
      }
    });
    handle.geometry.dispose();
    const material = handle.material;
    if (Array.isArray(material)) for (const m of material) m.dispose();
    else material.dispose();
    this.sectionHandle = null;
  }

  /**
   * The first raycast hit on the KEPT side of the section, or undefined.
   *
   * With no section this is simply the first hit, so callers pay one `if` for the feature. The epsilon
   * is scaled to the model: an absolute tolerance is wrong at both ends of the scale, and the point of
   * it is only to keep the cap's own vertices, which lie exactly on the plane, from being dropped to
   * floating point.
   */
  private firstKeptIntersection(
    hits: readonly THREE.Intersection[],
  ): THREE.Intersection | undefined {
    if (!this.activeSection) return hits[0];
    const epsilon = Math.max(this.sectionSpan() * 1e-5, 1e-9);
    const kept = firstKept(
      hits,
      (hit) => ({ x: hit.point.x, y: hit.point.y, z: hit.point.z }),
      this.sectionPlane,
      epsilon,
    );
    return kept ?? undefined;
  }

  /**
   * A length that stands for the model's size, for tolerances that must scale with it.
   *
   * Built from the shown parts' boxes, which the scene keeps current, and cached per call site rather
   * than per frame: the number only has to be the right order of magnitude, and a `Box3.union` of
   * thousands of parts on every pick would be its own cost.
   */
  private sectionSpan(): number {
    if (this.spanCache !== null) return this.spanCache;
    const bounds = this.sceneBounds();
    if (bounds.isEmpty()) return 1;
    const size = bounds.getSize(new THREE.Vector3());
    this.spanCache = Math.max(size.x, size.y, size.z, 1e-6);
    return this.spanCache;
  }

  /**
   * Whether any of an edge's points is on the kept side of the section.
   *
   * **Any, not all.** An edge that straddles the cut is half drawn and half removed — the cap cuts it
   * — so part of it is visible and the user can click that part. Requiring every point to be kept
   * would make a cut edge unpickable on the visible side, which is the surface most likely to be
   * aimed at right after a cut is made.
   *
   * The points are in the shape's own units and are put into world space with the same matrix the
   * projection uses, so the test and the drawing agree about where the edge is.
   */
  private edgeKept(points: Float32Array, count: number, model: number[]): boolean {
    const epsilon = Math.max(this.sectionSpan() * 1e-5, 1e-9);
    for (let i = 0; i < count; i++) {
      if (
        keptSide(
          worldPoint({ x: points[i * 3], y: points[i * 3 + 1], z: points[i * 3 + 2] }, model),
          this.sectionPlane,
          epsilon,
        )
      ) {
        return true;
      }
    }
    return false;
  }

  /** Hidden patches are neither drawn nor picked; an isolated patch hides every other one. */
  setVisibility(hidden: readonly string[], isolated: string | null): void {
    this.hidden = new Set(hidden);
    this.isolated = isolated;
    this.applyVisibility();
    // A hidden part is no longer capped (defect 4): the marks are child meshes of the parts it no
    // longer draws, and the signature below sees the change in `shown`.
    this.refreshSectionCaps();
  }

  setSelected(name: string | null): void {
    if (this.selected === name) return;
    this.selected = name;
    this.applyHighlights();
    if (name === null) this.setAnchor(null);
  }

  /**
   * Record a part's decoded topology, or drop it when the record is gone (T-058).
   *
   * Called by the `TopologyController` as each part's record arrives or is released. The record
   * serves three things, all of them live: the **edge/point hover and selection overlays**
   * (`setTopologyPick`), the **screen-space pick** in Edge and Point mode (`topologyAt`), and the
   * **measure tool's** element-aware measurement (T-113).
   *
   * This docstring used to say the overlays were "the next step of T-058" and that nothing was drawn
   * from the record but a pending state. That was true when written and false by the time it was read:
   * the overlays exist and are wired. Corrected rather than left, because a comment that denies a
   * working feature sends the next reader looking for something that is already there.
   */
  setTopology(part: string, topology: PartTopology | null): void {
    if (topology === null) this.topologies.delete(part);
    else this.topologies.set(part, topology);
    this.needsRender = true;
  }

  /** A part's decoded topology, or undefined when it has none loaded (T-058). */
  topologyOf(part: string): PartTopology | undefined {
    return this.topologies.get(part);
  }

  /**
   * Whether a source has at least one patch the pick can reach: a record, and something drawn.
   *
   * For `publishTopologyLoaded`. A record for a part that is hidden cannot be picked — both topology
   * loops skip hidden patches — so it must not be reported as "loaded", which the readout shows as
   * pickable. The scene owns the shown flags, so the answer comes from here rather than being guessed
   * from the store's meta, which carries no visibility at all.
   */
  hasShownTopology(source: string): boolean {
    if (this.topologies.get(source) === undefined) return false;
    return this.partsOfSource(source).length > 0;
  }

  /**
   * The drawn patches of one source (a part node id).
   *
   * **Keyed by `part.source`, not by `part.name`.** The topology records are keyed by the *source id*
   * the controller fetches for (the patch's `part`), while `this.parts` is keyed by the patch *name*
   * (`setSource` names each group). Looking a topology record up with `this.parts.get(id)` therefore
   * never matched anything: measured on the demo, all four records were loaded, 4 parts were drawn,
   * and `noEntry: 4` — every part skipped by the `entry === undefined` guard — so Edge mode found no
   * candidate at any point and selected nothing. Nine earlier probes missed it because each one
   * checked a different stage of the pipeline and this one guard silently discarded everything.
   */
  private partsOfSource(source: string): readonly Part[] {
    // `candidatePatches`, not `sourcePatches`: the loop's decision is "this source's DRAWN patches",
    // and pulling that out is what makes the rule testable without a renderer (defect 7). The loops
    // below then skip an already-filtered list, so an undrawn patch cannot re-enter through a caller
    // that forgets the check.
    return candidatePatches(this.sources, source);
  }

  /**
   * What the topology pick sees: the loaded records' sizes, and the result of a pick at a canvas
   * point (defaulting to the centre). For the review hook — figures, never a decision.
   */
  topologyInfo(
    x?: number,
    y?: number,
  ): {
    parts: number;
    edges: number;
    vertices: number;
    faces: number;
    solids: number;
    mode: string;
    want: string;
    pick: string | null;
    forced: string | null;
  } {
    let edges = 0;
    let vertices = 0;
    let faces = 0;
    let solids = 0;
    for (const topology of this.topologies.values()) {
      edges += topology.edges.length;
      vertices += topology.vertices.length;
      faces += topology.faceSizes.length;
      solids += topology.solids.length;
    }
    const at = x === undefined || y === undefined ? { x: this.width / 2, y: this.height / 2 } : { x, y };
    const found = this.topologyMode === null ? null : this.topologyAt(at.x, at.y, this.topologyMode);
    // The forced probe ignores `topologyMode`, so a null pick can be told apart from a null mode:
    // those are different faults (the overlay effect never ran, vs. the hit test finding nothing).
    const forced = this.topologyAt(at.x, at.y, "edge");
    return {
      parts: this.topologies.size,
      edges,
      vertices,
      faces,
      solids,
      mode: String(this.topologyMode),
      want: this.measureWant,
      pick: found === null ? null : `${found.kind}:${found.id}`,
      forced: forced === null ? null : `edge:${forced.id}`,
    };
  }

  /**
   * Show the hovered and picked edges and vertices (ADR-0022 §15.3, T-058).
   *
   * Called when the pick or the hover changes — never per frame. Refs are `{part, id}` pairs, which
   * is exactly what the transient pick holds, so the caller passes them straight through. A part
   * with no record loaded contributes nothing (the record is still arriving, or the part has no
   * B-rep), which is why the overlay can be updated before every record is in without flicker.
   *
   * World matrices are taken from the drawn parts, so an edge follows its part's transform without a
   * second source of truth.
   */
  /**
   * The pick mode the scene should resolve clicks in for topology: `null` for body and face mode, so
   * no topology test runs and no overlay is drawn (§15.3: no work in body/face mode).
   */
  private topologyMode: "edge" | "vertex" | null = null;

  /** Set the topology pick mode, called by the viewport as the store's `pickMode` changes. */
  setTopologyMode(mode: "edge" | "vertex" | null): void {
    this.topologyMode = mode;
    if (mode === null) this.topologyOverlay.clear();
    this.needsRender = true;
  }

  setTopologyPick(
    hover: { edges?: readonly { part: string; id: number }[]; vertices?: readonly { part: string; id: number }[] },
    picked: { edges?: readonly { part: string; id: number }[]; vertices?: readonly { part: string; id: number }[] },
  ): void {
    const matrices = new Map<string, THREE.Matrix4>();
    for (const part of this.parts.values()) {
      matrices.set(part.name, part.mesh.matrixWorld);
    }
    // The parts' world matrices are current because the frame loop updates them; a caller that
    // changes the pick between frames still gets the last frame's transform, which is what is on
    // screen, so the overlay cannot be drawn somewhere the part is not.
    this.topologyOverlay.set(
      hover.edges ?? [],
      picked.edges ?? [],
      hover.vertices ?? [],
      picked.vertices ?? [],
      this.topologies,
      matrices,
    );
    this.needsRender = true;
  }

  /**
   * Show the boundary glyphs (ADR-0022 §8.1, T-046), from the anchors `tag.anchor` returned and the
   * kind of each boundary tag.
   *
   * `kinds` maps a tag path to the condition kind using it, so the glyph's direction (inward for an
   * inlet, outward for an outlet) and its colour come from the boundary rather than from the tag
   * alone. Called when the boundaries, the tags or a part's mesh change — never per frame, except
   * for `rescaleGlyphs` below.
   */
  setBoundaryGlyphs(anchors: readonly TagAnchor[], kinds: ReadonlyMap<string, ConditionKind>): void {
    this.anchors = [...anchors];
    this.kindOfTag = new Map(kinds);
    this.rebuildGlyphs();
  }

  /** Turn the glyphs off and on (`view.glyphs`); off hides them without losing the anchors. */
  setGlyphsShown(shown: boolean): void {
    this.glyphsShown = shown;
    this.glyphs.set(shown ? this.currentGlyphs() : []);
    this.needsRender = true;
  }

  /**
   * Rescale for the camera's new distance. §8.1 wants a constant **28 px** on screen, so the world
   * size depends on how far the camera is; the per-frame work is one matrix per glyph, never per
   * face. This is the only glyph work the frame loop does.
   */
  rescaleGlyphs(): void {
    if (this.glyphsShown && this.anchors.length > 0) this.glyphs.set(this.currentGlyphs());
  }

  /** Clear the glyphs (a project change, or nothing to show). */
  clearBoundaryGlyphs(): void {
    this.anchors = [];
    this.kindOfTag.clear();
    this.glyphs.set([]);
    this.needsRender = true;
  }

  private rebuildGlyphs(): void {
    this.glyphs.set(this.glyphsShown ? this.currentGlyphs() : []);
    this.needsRender = true;
  }

  /** The glyphs for the current anchors, camera and viewport. */
  private currentGlyphs(): ReturnType<typeof glyphsFor> {
    const camera = this.rig.camera as THREE.PerspectiveCamera;
    const fov = camera.isPerspectiveCamera ? camera.fov : 45;
    // One camera read for every anchor: the forward axis and the eye position do not change between
    // them, and building them per anchor was two `Vector3` allocations each.
    const forward = new THREE.Vector3();
    camera.getWorldDirection(forward);
    const eye = camera.getWorldPosition(new THREE.Vector3());
    const viewOf = (
      position: [number, number, number],
      normal: [number, number, number],
    ): { distance: number; cosAngle: number } => {
      const point = new THREE.Vector3(position[0], position[1], position[2]);
      // The distance along the camera's forward axis, not the euclidean range: a glyph at the edge
      // of a wide view is the same size as one in the middle, which is what "constant on screen" means.
      const toAnchor = point.clone().sub(eye);
      const distance = toAnchor.dot(forward);
      // How square-on the surface is: the cosine between its outward normal and the direction from
      // the surface to the camera. `glyphShrink` turns it into the floor under the glyph's size, so
      // a boundary seen edge-on stays findable (T-046-fix-4).
      const toCamera = toAnchor.clone().negate().normalize();
      const cosAngle = normal[0] * toCamera.x + normal[1] * toCamera.y + normal[2] * toCamera.z;
      return { distance, cosAngle };
    };
    return glyphsFor(
      this.anchors,
      (tag) => this.kindOfTag.get(tag) ?? null,
      viewOf,
      fov,
      // **No canvas, no glyphs**: `glyphScale` answers 0 for a height of 0 and the glyph is skipped. Clamped to
      // 1 px, a hidden window's 0-high canvas sized every glyph 28 times the view's height, and the outlet and
      // symmetry markers filled the screen like a camera inside the part (the elbow, 2026-11-08).
      this.drawingBuffer.y,
    );
  }

  /** Clear the topology overlay (leaving Edge or Point mode). */
  clearTopologyPick(): void {
    this.topologyOverlay.clear();
    this.needsRender = true;
  }

  /** How many parts have a topology record loaded, for a test or a perf readout. */
  topologyCount(): number {
    return this.topologies.size;
  }

  setHover(name: string | null): void {
    if (this.hovered === name) return;
    this.hovered = name;
    this.applyCursor();
    this.applyHighlights();
    this.onHover(name);
  }

  /**
   * The faces the store has picked, by part node id (ADR-0021 3.5). The scene turns them into the
   * accent overlay, borrowing a single group's buffers and copying across groups or parts.
   */
  setFaceSelection(faces: ReadonlyMap<string, ReadonlySet<number>>): void {
    this.facePick = faces;
    this.updateSelection();
  }

  /** The plot-over-line's segment and end markers: both points draw the line, one draws its marker, null takes them away. */
  setPlotLine(a: readonly number[] | null, b: readonly number[] | null): void {
    this.lineLayer.set(a, b);
    this.needsRender = true;
  }

  /** The streamline object currently drawn, kept so a new set replaces it rather than stacking. */
  private streamlineObject: import("three/addons/lines/LineSegments2.js").LineSegments2 | null = null;

  /**
   * Draw a set of streamlines, coloured by speed (results item 4), or none.
   *
   * **The geometry is `streamlineLayer.ts`'s** - segments with per-segment colour - and this only owns
   * the object's lifetime: a new set disposes the old one, because a user who draws twice expects the
   * second answer rather than two pictures overlaid.
   *
   * **The range is the caller's**, across every line, so two lines at the same speed are the same
   * colour in one picture. **The map is the results view's** (`map`, `reverse`), as the slices' and the legend's.
   */
  setStreamlines(
    lines: readonly { points: readonly (readonly [number, number, number])[]; speeds: readonly number[] }[],
    range: { min: number; max: number } | null,
    map: ColormapId = DEFAULT_COLORMAP,
    reverse = false,
    tubes = false,
  ): void {
    if (this.streamlineObject !== null) {
      this.scene.remove(this.streamlineObject);
      // **Dispose both**, or a redraw leaks a geometry and a material per press.
      this.streamlineObject.geometry.dispose();
      (this.streamlineObject.material as THREE.Material).dispose();
      this.streamlineObject = null;
    }
    // **A redraw is asked for, as every other overlay does** (`setPlotLine`): the frame loop draws only when
    // something says it must, and lines that arrive after the slice were in the scene but not on the screen.
    this.needsRender = true;
    if (lines.length === 0 || range === null) return;
    // **Tubes have a width in the MODEL** (0.4 % of its diagonal): `LineMaterial`'s world units make the ribbon
    // a real size that shrinks with distance, where a hairline keeps its pixels.
    const box = tubes ? this.modelBounds() : null;
    const diagonal = box === null ? 0 : Math.hypot(box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]);
    const object = streamlineLines(lines, range, tubes && diagonal > 0 ? 0.004 * diagonal : STREAMLINE_WIDTH_PX, map, reverse, { width: this.width, height: this.height }, tubes && diagonal > 0);
    if (object === null) return;
    this.streamlineObject = object;
    this.scene.add(object);
  }

  /** Figures about the drawn streamlines, for the measurement hook; null when none are drawn. */
  streamlineInfo(): { segments: number; visible: boolean; width: number; resolution: [number, number]; bounds: number[] } | null {
    const object = this.streamlineObject;
    if (object === null) return null;
    const material = object.material as unknown as { linewidth: number; resolution: THREE.Vector2 };
    const box = new THREE.Box3().setFromObject(object);
    return {
      segments: object.geometry.instanceCount,
      visible: object.visible,
      width: material.linewidth,
      resolution: [material.resolution.x, material.resolution.y],
      bounds: [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z],
    };
  }

  /** The streamlines are drawn in pixels (`LineMaterial`): their width needs the canvas size, as the edges' does. */
  private setStreamlineResolution(width: number, height: number, scale = 1): void {
    const material = this.streamlineObject?.material as { resolution?: THREE.Vector2; linewidth?: number } | undefined;
    if (material === undefined) return;
    material.resolution?.set(Math.max(1, width), Math.max(1, height));
    // An export frame scales the width with the picture, as the edges do, so a 4x figure keeps the lines' weight.
    material.linewidth = STREAMLINE_WIDTH_PX * scale;
  }

  /** One arrow mesh per slice id, so a slice's arrows live and die with it. */
  private sliceArrows = new Map<string, THREE.InstancedMesh>();

  /**
   * Draw one slice's arrows, or none (claude-win2's item 2).
   *
   * **Keyed by the slice**, because several slices can be on at once and each cut's arrows belong to
   * its own plane. A replacement disposes the old mesh's geometry and material, or a redraw leaks one
   * of each per density change.
   */
  setSliceArrows(id: string, mesh: THREE.InstancedMesh | null): void {
    const old = this.sliceArrows.get(id);
    if (old !== undefined) {
      this.scene.remove(old);
      old.geometry.dispose();
      (old.material as THREE.Material).dispose();
      this.sliceArrows.delete(id);
      this.needsRender = true;
    }
    if (mesh === null) return;
    // **A redraw is asked for**: the volume's arrows arrive on their own, with no slice redraw to carry them.
    this.needsRender = true;
    this.sliceArrows.set(id, mesh);
    this.scene.add(mesh);
  }

  /** The picked bodies other than the active one, as patch names (ADR-0021 3.5). */
  setBodySelection(active: string | null, others: readonly string[]): void {
    this.bodyPick = { active, others };
    this.updateSelection();
  }

  /** A part's groups' meshes with the face ids and indices they draw (aligned), as the highlights take them. */
  faceSourceOf(part: string): FaceSource[] | null {
    const groups = this.sources.get(part);
    if (!groups) return null;
    const sources: FaceSource[] = [];
    for (const group of groups) {
      if (!group.faceIds) continue;
      sources.push({ mesh: group.mesh, faceIds: group.faceIds, indices: group.indices });
    }
    return sources.length > 0 ? sources : null;
  }

  /**
   * Hides faces of a part (a source), the faces of its tags whose eye is off (ADR-0021 3.5): its meshes
   * draw an index without them, the own mesh and the batch copy alike, so they are neither drawn,
   * picked nor outlined. Kept for the source and applied to every mesh it gets later (a fine mesh
   * after the coarse one); an empty set restores the full index.
   */
  /** The faces hidden on a part, or undefined when none are (T-124a's pick filter reads this). */
  hiddenFacesOf(part: string): ReadonlySet<number> | undefined {
    return this.hiddenFaces.get(part);
  }

  setHiddenFaces(part: string, faces: ReadonlySet<number>): void {
    if (faces.size === 0) this.hiddenFaces.delete(part);
    else this.hiddenFaces.set(part, new Set(faces));
    let changed = false;
    for (const group of this.sources.get(part) ?? []) {
      if (!this.applyHidden(group)) continue;
      changed = true;
      if (this.viewMode === "edges" && !this.batched) {
        this.ensureEdges(group);
        if (group.edges) group.edges.visible = group.shown;
      }
    }
    if (!changed) return;
    // The pick overlay is built from the drawn arrays, and the contact shadow draws the parts.
    this.selectionKey = null;
    this.updateSelection();
    this.shadow.invalidate();
    this.needsRender = true;
  }

  /**
   * A part's tag colours (null: none), kept for the source like its hidden faces and written into the
   * tint attribute of each of its meshes and their batch copies (materials.ts).
   */
  setFaceTint(part: string, tint: FaceTint | null): void {
    if (tint === null || (tint.all === 0 && tint.faces.size === 0)) this.faceTints.delete(part);
    else this.faceTints.set(part, tint);
    let changed = false;
    for (const group of this.sources.get(part) ?? []) changed = this.applyTint(group) || changed;
    if (changed) this.needsRender = true;
  }

  /**
   * Gives a source a colour per vertex, RGBA8 for the vertices of its groups in order (a mesh patch has
   * one group, so `4 * vertices` bytes: sRGB colour and strength, as the tag tint). The array is kept for
   * the source and written into the tint attribute of each of its meshes and their batch copies, now and
   * again whenever the source's part is made or replaced (`applyTint`), and it takes the place of the
   * source's tag colours while it is there. `null` removes it, and the tag colours (or none) come back.
   * An array whose length is not four bytes per vertex of the source is dropped with a warning, never
   * written. Not tied to a mesh patch: any source can have one. T-218 colours fields through it.
   */
  setSourceVertexTint(source: string, rgba: Uint8Array | null): void {
    if (rgba === null) this.vertexTints.delete(source);
    else this.vertexTints.set(source, rgba);
    let changed = false;
    for (const part of this.sources.get(source) ?? []) changed = this.applyTint(part) || changed;
    if (changed) this.needsRender = true;
  }

  /**
   * The slice of the source's vertex tint this group reads, or null when the source has none. A length
   * that does not fit the source's vertices removes the tint and says so, so the group falls back to
   * the source's tag colours instead of being written something misaligned.
   */
  private vertexTintOf(part: Part): Uint8Array | null {
    const vertex = this.vertexTints.get(part.source);
    switch (chooseTint(vertex, part.tintAt.total)) {
      case "face":
        return null;
      case "mismatch":
        console.warn(`vertex tint of ${part.source} has ${vertex?.length ?? 0} bytes for ${part.tintAt.total} vertices; it is dropped`);
        this.vertexTints.delete(part.source);
        return null;
      default:
        return vertex === undefined ? null : groupSlice(vertex, part.tintAt.offset, part.group.vertices);
    }
  }

  /**
   * Draws the part without the faces its source hides, filtered from the group's full arrays. With
   * every face hidden one degenerate triangle stays: it draws and picks nothing, and the part keeps
   * its place in a batch, which holds no empty geometry. `toBatch` is false while the caller puts the
   * mesh into its batch itself. True when the drawn index changed.
   */
  private applyHidden(part: Part, toBatch = true): boolean {
    const hidden = this.hiddenFaces.get(part.source);
    const full = part.group.indices;
    const fullIds = part.group.faceIds;
    let indices = full;
    let faceIds = fullIds;
    if (hidden !== undefined && fullIds !== null) {
      let kept = 0;
      for (let t = 0; t < fullIds.length; t++) if (!hidden.has(fullIds[t])) kept++;
      if (kept < fullIds.length) {
        const ids = new Uint32Array(Math.max(kept, 1));
        const own = new Uint32Array(ids.length * 3);
        if (kept === 0) {
          own.fill(full[0]);
          ids[0] = fullIds[0];
        } else {
          let at = 0;
          for (let t = 0; t < fullIds.length; t++) {
            if (hidden.has(fullIds[t])) continue;
            own[at * 3] = full[t * 3];
            own[at * 3 + 1] = full[t * 3 + 1];
            own[at * 3 + 2] = full[t * 3 + 2];
            ids[at++] = fullIds[t];
          }
        }
        indices = own;
        faceIds = ids;
      }
    }
    if (indices === part.indices) return false;
    part.indices = indices;
    part.faceIds = faceIds;
    part.mesh.geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    if (this.faces.owner === part) this.clearFaceHover();
    this.forgetEdges(part);
    if (toBatch && part.batched !== null) {
      try {
        this.batchPool.replace(part.batched, part.mesh.geometry);
      } catch (err) {
        console.warn(`patch ${part.name} is drawn on its own: ${(err as Error).message}`);
        this.fromBatch(part, true);
      }
    }
    return true;
  }

  /**
   * Writes the source's tag colours into the part's tint attribute, every triangle of the group,
   * hidden or not, so a face shown again has its colour (the import welds vertices by face, so no
   * vertex is shared between two faces). True when anything was written.
   */
  private applyTint(part: Part, toBatch = true): boolean {
    const tint = this.faceTints.get(part.source) ?? null;
    const vertex = this.vertexTintOf(part);
    if (tint === null && vertex === null && part.tinted === 0 && !part.vertexTinted) return false;
    const attribute = part.mesh.geometry.getAttribute(TINT_ATTRIBUTE);
    if (!(attribute instanceof THREE.BufferAttribute)) return false;
    const bytes = attribute.array;
    const words = new Uint32Array(bytes.buffer, bytes.byteOffset, attribute.count);
    const { indices, faceIds } = part.group;
    let tinted = 0;
    part.vertexTinted = vertex !== null;
    if (vertex !== null) {
      // A per-vertex tint (a field, a quality measure) wins over the source's tag colours.
      (bytes as Uint8Array).set(vertex);
      tinted = tintedTriangles(vertex, indices);
    } else if (tint === null) {
      words.fill(0);
    } else if (faceIds === null || tint.faces.size === 0) {
      words.fill(tint.all);
      tinted = tint.all === 0 ? 0 : indices.length / 3;
    } else {
      for (let t = 0; t < faceIds.length; t++) {
        const word = tint.faces.get(faceIds[t]) ?? tint.all;
        words[indices[t * 3]] = word;
        words[indices[t * 3 + 1]] = word;
        words[indices[t * 3 + 2]] = word;
        if (word !== 0) tinted++;
      }
    }
    part.tinted = tinted;
    attribute.needsUpdate = true;
    if (toBatch && part.batched !== null) this.batchPool.setTint(part.batched);
    return true;
  }

  // ------------------------------------------------------------------ building and freeing parts
  private geometryOf(group: LoadedGroup): { geometry: THREE.BufferGeometry; faces: Uint32Array; truncated: boolean } {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(group.positions, 3));
    geometry.setAttribute("normal", new THREE.BufferAttribute(group.normals, 3));
    // Tag colours, none yet: every part has the attribute, as the batches need the same set on all.
    geometry.setAttribute(TINT_ATTRIBUTE, new THREE.BufferAttribute(new Uint8Array((group.positions.length / 3) * 4), 4, true));
    // The face ordinal the id buffer names a face by (T-032); every part has it, for the same reason.
    const ids = groupFaceIds(group);
    geometry.setAttribute(FACE_ID_ATTRIBUTE, new THREE.BufferAttribute(ids.attribute, 1, false));
    geometry.setIndex(new THREE.BufferAttribute(group.indices, 1));
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    return { geometry, faces: ids.faces, truncated: ids.truncated };
  }

  /**
   * A patch with more faces than the id buffer can name (65,535) leaves the ones past it without a
   * face: body picking still finds the part, face picking does not find those faces. Said once per
   * patch, so a scene of such patches does not fill the log every frame (T-032).
   */
  private noteFaceLimit(part: Part, truncated: boolean): void {
    if (!truncated || part.idLimitLogged) return;
    part.idLimitLogged = true;
    console.warn(`patch ${part.name} has more faces than the id buffer can name; faces past 65,535 lose face picking`);
  }

  private worldBox(mesh: THREE.Mesh): THREE.Box3 {
    return (mesh.geometry.boundingBox ?? new THREE.Box3()).clone().applyMatrix4(mesh.matrixWorld);
  }

  private metaFor(source: string, name: string, color: [number, number, number], group: LoadedGroup): PatchMeta {
    const { positions, normals, indices, faceIds, ...meta } = group;
    return { ...meta, name, color, part: source === SAMPLE_SOURCE ? null : source };
  }

  private build(source: string, group: LoadedGroup, bucket: number, tintAt: { offset: number; total: number }, unlit = false): Part {
    const look = createLook(group.kind, group.color, this.theme.isDark, unlit);
    setLookOpacity(look, bucket);
    const geometry = this.geometryOf(group);
    const mesh = new THREE.Mesh<THREE.BufferGeometry, THREE.Material>(geometry.geometry, look.glass ?? look.surface);
    mesh.name = group.name;
    mesh.layers.enable(PART_LAYER);
    if (bucket >= OPAQUE_BUCKET) mesh.layers.enable(OCCLUDER_LAYER);
    mesh.updateMatrixWorld(true);
    const part: Part = {
      name: group.name,
      source,
      unlit,
      mesh,
      look,
      batched: null,
      raised: false,
      group,
      color: group.color,
      bucket,
      meta: this.metaFor(source, group.name, group.color, group),
      edgeSegments: null,
      edges: null,
      indices: group.indices,
      faceIds: group.faceIds,
      faceTable: geometry.faces,
      idIndex: PART_INDEX_NONE,
      idLimitLogged: false,
      tinted: 0,
      tintAt,
      vertexTinted: false,
      box: this.worldBox(mesh),
      shown: true,
      enabled: true,
      lift: 0,
      liftTarget: 0,
    };
    this.paint(part);
    this.partOf.set(mesh, part);
    this.bindIdIndex(part);
    this.noteFaceLimit(part, geometry.truncated);
    this.idIndicesDirty = true;
    // What the source's tags ask of it, before a batch copies the geometry.
    this.applyHidden(part, false);
    this.applyTint(part, false);
    // A batched scene takes the part into a batch straight away; its own mesh stays out of the scene.
    if (!this.batched || !this.toBatch(part)) this.scene.add(mesh);
    return part;
  }

  /**
   * Tells the id material which part an own mesh draws (T-032): the uniform is written just before
   * the mesh is drawn, because three uploads a shared override material's uniforms once per render
   * and the pass draws thousands of meshes with it (idbuffer.ts).
   */
  private bindIdIndex(part: Part): void {
    part.mesh.onBeforeRender = (): void => {
      if (!this.idActive) return;
      this.idPass.setPartIndex(part.idIndex);
      this.idPass.refreshUniforms();
    };
  }

  /** Puts a new mesh of the same kind into an existing part: the GPU buffers are swapped, nothing else is. */
  private refill(part: Part, group: LoadedGroup, tintAt: { offset: number; total: number }): Part {
    if (this.faces.owner === part) this.clearFaceHover();
    const previous = part.mesh.geometry;
    const geometry = this.geometryOf(group);
    part.mesh.geometry = geometry.geometry;
    part.group = group;
    part.indices = group.indices;
    part.faceIds = group.faceIds;
    part.faceTable = geometry.faces;
    part.tinted = 0;
    part.tintAt = tintAt;
    part.vertexTinted = false;
    this.noteFaceLimit(part, geometry.truncated);
    // The source's hidden faces and tag colours, on the new arrays, before the batch takes them.
    this.applyHidden(part, false);
    this.applyTint(part, false);
    part.mesh.updateMatrixWorld(true);
    part.box = this.worldBox(part.mesh);
    if (part.batched !== null) {
      try {
        this.batchPool.replace(part.batched, part.mesh.geometry);
      } catch (err) {
        console.warn(`patch ${part.name} is drawn on its own: ${(err as Error).message}`);
        this.fromBatch(part, true);
      }
    }
    // Only now: the batch kept the old geometry until it had the new one.
    previous.dispose();
    this.forgetEdges(part);
    return part;
  }

  /** Brings colour, visibility and opacity to what the style says; true when any changed. */
  private restyle(part: Part, style: PartStyle): boolean {
    part.simulate = style.simulate !== false;
    const override = style.color !== null ? parseHexColor(style.color) : null;
    const color = override ?? part.group.color;
    let changed = false;
    if (!sameColor(part.color, color)) {
      part.color = [color[0], color[1], color[2]];
      // The part's own look too: it draws the part while the part is highlighted.
      setLookColor(part.look, part.color);
      if (part.batched !== null) {
        try {
          this.batchPool.setColor(part.batched, part.color);
        } catch (err) {
          // A kind batched per colour moves batch; should that fail, the part draws on its own.
          console.warn(`patch ${part.name} is drawn on its own: ${(err as Error).message}`);
          this.fromBatch(part, true);
        }
      }
      changed = true;
    }
    if (part.enabled !== style.visible) {
      part.enabled = style.visible;
      changed = true;
    }
    /*
      **An excluded body is faded to a third** (T-222): faint enough to read as "not part of this",
      clear enough to see where it stood. `Math.min` with the part's own opacity, so a body the user
      already made translucent does not come back MORE solid when they exclude it - the two settings
      combine rather than the weaker one winning by accident.
    */
    const excluded = style.simulate === false;
    const bucket = opacityBucket(excluded ? Math.min(style.opacity, EXCLUDED_OPACITY) : style.opacity);
    if (part.bucket !== bucket) {
      part.bucket = bucket;
      this.applyOpacity(part);
      changed = true;
    }
    const shown = this.wantsShown(part, this.isolatedName());
    if (part.shown !== shown) {
      part.shown = shown;
      this.showPart(part);
    }
    if (changed) this.paint(part);
    return changed;
  }

  /**
   * Puts the part's opacity bucket on whatever draws it: its own mesh draws the look's glass while
   * translucent and leaves the occluder layer, and its instance moves to the batch of the bucket.
   */
  private applyOpacity(part: Part): void {
    setLookOpacity(part.look, part.bucket);
    if (this.viewMode !== "wireframe") part.mesh.material = part.look.glass ?? part.look.surface;
    if (part.bucket < OPAQUE_BUCKET) part.mesh.layers.disable(OCCLUDER_LAYER);
    else part.mesh.layers.enable(OCCLUDER_LAYER);
    if (part.batched === null) return;
    try {
      this.batchPool.setOpacity(part.batched, part.bucket);
    } catch (err) {
      // The move to the batch of the new bucket failed: the part draws on its own.
      console.warn(`patch ${part.name} is drawn on its own: ${(err as Error).message}`);
      this.fromBatch(part, true);
    }
  }

  /** Takes a part's own edge lines out of the scene; the segments stay for the next time. */
  private dropEdgeLines(part: Part): void {
    if (!part.edges) return;
    this.scene.remove(part.edges);
    part.edges.geometry.dispose();
    part.edges = null;
  }

  /** The part's mesh changed: its edges are found again when next needed. */
  private forgetEdges(part: Part): void {
    this.dropEdgeLines(part);
    part.edgeSegments = null;
    this.mergedStale = true;
  }

  /** Frees a part and the state that points at it. The name is released only if the part still owns it. */
  private discard(part: Part): void {
    if (this.faces.owner === part) this.clearFaceHover();
    if (part.batched !== null) {
      this.batchPool.remove(part.batched);
      part.batched = null;
    }
    part.raised = false;
    this.scene.remove(part.mesh);
    part.mesh.geometry.dispose();
    disposeLook(part.look);
    this.forgetEdges(part);
    this.partOf.delete(part.mesh);
    this.idIndicesDirty = true;
    this.idDirty = true;
    if (this.parts.get(part.name) === part) this.parts.delete(part.name);
    if (this.hovered === part.name && !this.parts.has(part.name)) {
      this.hovered = null;
      this.resetCycle();
      this.onHover(null);
    }
  }

  private freeName(base: string): string {
    if (!this.parts.has(base)) return base;
    for (let n = 2; ; n++) {
      const candidate = `${base}_${n}`;
      if (!this.parts.has(candidate)) return candidate;
    }
  }

  // ------------------------------------------------------------------ batched drawing (ADR-0019)
  /**
   * Hands a part to the batches. False when they cannot take it (a mesh they cannot hold, or an
   * error from three.js), and the part stays drawn by its own mesh.
   */
  private toBatch(part: Part): boolean {
    // A batch is lit (its look is the recipe's PBR material), and a cut must not be.
    if (part.unlit) return false;
    let handle: BatchedPart | null = null;
    try {
      handle = this.batchPool.add(part.mesh.geometry, part.group.kind, part.color, part.bucket, part.shown);
    } catch (err) {
      console.warn(`patch ${part.name} is drawn on its own: ${(err as Error).message}`);
    }
    if (handle === null) return false;
    part.batched = handle;
    // Its id-buffer number travels with it: the batch names it through the instance (T-032).
    this.batchPool.setPartIndex(handle, part.idIndex);
    this.dropEdgeLines(part);
    if (part.lift > 0 || part.liftTarget > 0) {
      // Highlighted: its own mesh goes on drawing it until the highlight has faded.
      part.raised = true;
      this.batchPool.setVisible(handle, false);
    } else {
      part.raised = false;
      this.scene.remove(part.mesh);
    }
    return true;
  }

  /** Takes a part out of the batches (`release`: out of the pool too) and back to drawing it by its own mesh. */
  private fromBatch(part: Part, release: boolean): void {
    if (part.batched === null) return;
    if (release) this.batchPool.remove(part.batched);
    part.batched = null;
    part.raised = false;
    part.mesh.visible = part.shown;
    if (part.mesh.parent !== this.scene) this.scene.add(part.mesh);
  }

  /** A batched part draws by its own mesh while highlighted: the instance hides and the mesh, in its own look, takes its place. */
  private raise(part: Part): void {
    if (part.batched === null || part.raised) return;
    part.raised = true;
    this.batchPool.setVisible(part.batched, false);
    part.mesh.visible = part.shown;
    this.scene.add(part.mesh);
  }

  /** The highlight has faded: the batch draws the part again. */
  private lower(part: Part): void {
    if (part.batched === null || !part.raised) return;
    part.raised = false;
    this.scene.remove(part.mesh);
    // Its GPU copy is no longer drawn. The arrays stay, for picking and for the batches.
    part.mesh.geometry.dispose();
    this.batchPool.setVisible(part.batched, part.shown);
  }

  /** Puts `part.shown` on whatever draws the part: its own mesh, or its instance. */
  private showPart(part: Part): void {
    if (part.batched !== null && !part.raised) {
      this.batchPool.setVisible(part.batched, part.shown);
    } else {
      part.mesh.visible = part.shown;
    }
  }

  /**
   * The drawing mode follows the patch count, both ways: entered at BATCH_MESH_THRESHOLD patches, left
   * below it (an undone import, an emptied project). Checked once per flush, so a burst of parts
   * arriving or leaving moves the parts at most once.
   */
  private syncBatchMode(): void {
    const wanted = this.parts.size >= BATCH_MESH_THRESHOLD;
    if (wanted === this.batched) return;
    if (wanted) this.enterBatchedMode();
    else this.leaveBatchedMode();
  }

  /** Every patch, the one that crossed the threshold included, moves into the batches. */
  private enterBatchedMode(): void {
    this.batched = true;
    // Each batch is sized for its own parts, so a colour with a few parts does not get the budget of all.
    const parts = [...this.parts.values()];
    this.batchPool.reserve(parts.filter((part) => !part.unlit).map((part) => ({ geometry: part.mesh.geometry, kind: part.group.kind, color: part.color, bucket: part.bucket })));
    for (const part of parts) if (part.batched === null) this.toBatch(part);
    this.mergedStale = true;
  }

  private leaveBatchedMode(): void {
    this.batched = false;
    for (const part of this.parts.values()) this.fromBatch(part, false);
    this.batchPool.clear();
    this.mergedEdges.clear();
  }

  /**
   * In a batched scene a part is drawn by its own mesh only while highlighted, so the plain (not
   * batched) shaders of its look would first be compiled at the first hover: a stall of up to a second
   * on ANGLE, measured on the demo with every part batched. Each kind's look is compiled ahead
   * instead, in the background (KHR_parallel_shader_compile), and kept, which keeps its programs in
   * the renderer's cache for every part of that kind. Colour and highlight do not change a program.
   */
  private warmKinds(kinds: Iterable<string>): void {
    for (const kind of kinds) {
      if (this.warmLooks.has(kind)) continue;
      const look = createLook(kind, [1, 1, 1], this.theme.isDark);
      this.warmLooks.set(kind, look);
      const triangle = this.probeGeometry();
      const probe = new THREE.Group().add(new THREE.Mesh(triangle, look.surface), new THREE.Mesh(triangle, look.wire));
      this.renderer.compileAsync(probe, this.rig.camera, this.scene).catch(() => undefined);
    }
  }

  /**
   * The translucent programs (ADR-0021 4.4) of the kinds the imported parts have, compiled ahead in
   * the background like `warmKinds`, so neither the first part made translucent nor its first
   * highlight waits for a shader: three draws a transparent double-sided material back faces first,
   * which gives a translucent look two programs on its own mesh and two in a batch, and compiling
   * them at first use held that frame for 0.7 s on the development laptop. It runs a while after the
   * content last changed (`flushContent`), so it never queues in front of what is about to be drawn.
   * The sample part has no opacity, so its kinds are left out.
   */
  private warmGlass(): void {
    this.glassDue = 0;
    const kinds = new Set<string>();
    for (const part of this.parts.values()) if (part.source !== SAMPLE_SOURCE) kinds.add(part.group.kind);
    for (const kind of kinds) {
      if (this.glassLooks.has(kind)) continue;
      const look = createLook(kind, [1, 1, 1], this.theme.isDark);
      // Any bucket below 1 will do: the opacity is a uniform, not part of the program.
      setLookOpacity(look, 5);
      this.glassLooks.set(kind, look);
      const glass = look.glass ?? look.surface;
      const triangle = this.probeGeometry();
      // One part in a batch with a colour per instance, as the batches draw them (batch.ts).
      const batch = new THREE.BatchedMesh(1, 3, 3, glass);
      batch.addInstance(batch.addGeometry(triangle));
      batch.setColorAt(0, new THREE.Color(1, 1, 1));
      const probe = new THREE.Group().add(new THREE.Mesh(triangle, glass), batch);
      const done = (): void => batch.dispose();
      this.renderer.compileAsync(probe, this.rig.camera, this.scene).then(done, done);
    }
  }

  /**
   * Compiles the two programs the first box select would otherwise build on its own frame.
   *
   * The bug this exists for (measured 2026-10-08 on the owner's Intel Iris Xe via ANGLE/D3D11). A box
   * select over the 4,500-part model was over the 50 ms budget, and the whole of it was the FIRST
   * one: the bench's raw arrays were `[239, 62, 33]` (body) and `[654, 27, 19]` (face) over three
   * identical drags, so the first release cost 4-30x the rest. That is warm-up, not steady state.
   *
   * A runtime spy on `gl.linkProgram` names it exactly: on the first body box the page links ONE
   * program, tagged `#define SHADER_NAME stokes:id` — the id-buffer pass shader — and that release
   * cost 288.7 ms. Every later box links nothing and costs 24-108 ms. A V8 profile of the same frame
   * put **2,507 ms in `getProgramInfoLog`**, which is the first GL call past `linkProgram`, so
   * ANGLE's D3D11 compile completes inside it, plus **489 ms in three's `WebGLBindingStates.reset`**,
   * the `deleteVertexArray`/`deleteBuffer` churn of swapping programs mid-frame.
   *
   * The id pass is offscreen and drawn only when a box, lasso or paint stroke asks for it (T-032), so
   * nothing compiled it during load. Warming it is `warmGlass`'s problem exactly, so it warms on the
   * same trigger and by the same means: a while after the content last changed, so the compile never
   * queues in front of a frame that is about to be drawn. `SelectionHighlight.prewarm` does the same
   * for the instanced pick overlay, which is a second program (three keys on `instancing`) that the
   * same first gesture would otherwise build.
   *
   * The id pass is the one the spy proved; both are warmed because a box select builds both, so the
   * cost still measured after this fix is not attributable to either compile alone.
   */
  private warmPickSelect(): void {
    this.pickSelectDue = 0;
    if (this.disposed || this.contextLost) return;
    // The pass is drawn whether or not `idDirty` says so: what is being bought here is the COMPILE,
    // and a still camera since the load can leave the buffer clean, which would make `renderIdPass`
    // return without compiling anything and the warm a no-op. `idDirty` is left as it was, so the
    // next real gesture still redraws it.
    if (this.contentDirty) this.flushContent();
    this.assignIdIndices();
    const dirty = this.idDirty;
    this.idActive = true;
    this.idPass.render(this.renderer, this.scene, this.rig.camera);
    this.idActive = false;
    this.idDirty = dirty;
    this.selection.prewarm(this.renderer, this.probeGeometry(), this.rig.camera, this.scene);
    this.pickSelectWarmed = this.batched;
  }

  /** One triangle laid out as the parts are: position and normal, indexed. */
  private probeGeometry(): THREE.BufferGeometry {
    if (!this.warmProbe) {
      this.warmProbe = new THREE.BufferGeometry();
      this.warmProbe.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
      this.warmProbe.setAttribute("normal", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]), 3));
      this.warmProbe.setIndex(new THREE.BufferAttribute(new Uint32Array([0, 1, 2]), 1));
    }
    return this.warmProbe;
  }

  /** Frees the warmed looks: on dispose, and when a lost context took their programs with it. */
  private coolKinds(): void {
    for (const look of this.warmLooks.values()) disposeLook(look);
    this.warmLooks.clear();
    for (const look of this.glassLooks.values()) disposeLook(look);
    this.glassLooks.clear();
    this.glassDue = 0;
    // The pick overlay's program went with the context too; `prewarm` must ask for it again.
    this.selection.uncool();
    this.pickSelectDue = 0;
    this.pickSelectWarmed = null;
    this.warmProbe?.dispose();
    this.warmProbe = null;
  }

  /**
   * Marks the drawn groups as changed. The costly follow-up, and the redraw that shows the change,
   * come with the next flush, which the frame loop runs as soon as its gap has passed.
   */
  /** Scratch for the scale bar: the projected span and the two points it is measured between. */
  private readonly projected = new THREE.Vector3();
  private readonly scaleA = new THREE.Vector3();
  private readonly scaleB = new THREE.Vector3();
  private scaleKey = "";

  /**
   * Times the pieces of one part's construction, `n` times over, for the load's apply budget (T-096).
   *
   * `createLook` is called once per part and makes two materials each time with no cache. On the
   * 4,500-part benchmark every pin has the same kind and colour, so this says whether the materials
   * are worth caching before anyone restructures the highlight path to allow it. It builds into
   * nothing and touches no scene state: a measurement, not a code path.
   */
  measureBuild(n: number): { lookMs: number; meshMs: number; distinctLooks: number; n: number } {
    const group = [...this.sources.values()][0]?.[0]?.group ?? null;
    const kind = group?.kind ?? "solid";
    const color: readonly [number, number, number] = group?.color ?? [0.7, 0.7, 0.72];
    const looks = new Set<unknown>();
    const lookStart = performance.now();
    for (let i = 0; i < n; i++) looks.add(createLook(kind, color, this.theme.isDark));
    const lookMs = performance.now() - lookStart;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
    const look = createLook(kind, color, this.theme.isDark);
    const meshStart = performance.now();
    for (let i = 0; i < n; i++) {
      const mesh = new THREE.Mesh(geometry, look.surface);
      mesh.updateMatrixWorld(true);
      this.worldBox(mesh);
    }
    const meshMs = performance.now() - meshStart;
    geometry.dispose();
    return { lookMs, meshMs, distinctLooks: looks.size, n };
  }

  private touch(): void {
    this.patchList = null;
    this.contentDirty = true;
  }

  /**
   * Tells the store what is drawn. Called from the flush, not per part: a 4,500-part load adds one
   * part at a time, and a `setGeometry` per part is a React update — plus a `syncParts` pass over
   * everything the loader holds — for each one, which is thousands of updates for a single load.
   * The list IS the picture, so it is published when the picture has finished changing.
   */
  private publishContent(): void {
    this.onContent(this.patches());
  }

  /**
   * The scale bar for the current view, measured **at the orbit target** — the point the user is
   * looking at, and the only point a perspective camera has one scale for. Under an orthographic
   * camera the figure is exact everywhere, which `ScaleBar.perspective` records.
   *
   * A metre is projected along the camera's own right axis and its screen length is what the bar
   * sizes itself from, so this works whatever the camera's orbit is.
   */
  private scaleForView(width: number = this.width): ScaleBar | null {
    if (this.parts.size === 0 && this.sources.size === 0) return null;
    const camera = this.rig.camera;
    const target = this.rig.target;
    this.projected.setFromMatrixPosition(camera.matrixWorld).sub(target);
    if (this.projected.lengthSq() < 1e-12) return null;
    // The camera's right axis, so the span is parallel to the screen and its length is direct.
    this.projected.cross(camera.up).normalize().multiplyScalar(1);
    this.scaleA.copy(target);
    this.scaleB.copy(target).add(this.projected);
    this.scaleA.project(camera);
    this.scaleB.project(camera);
    const pixels = Math.abs(this.scaleB.x - this.scaleA.x) * 0.5 * Math.max(1, width);
    if (!(pixels > 0)) return null;
    return scaleBar(1 / pixels, !this.rig.isOrthographic);
  }

  /** Sends the scale bar to the overlay when it has changed; called on every camera move. */
  private reportScale(): void {
    const bar = this.scaleForView();
    const key = bar === null ? "" : `${bar.label}|${Math.round(bar.pixels)}|${bar.perspective}`;
    if (key === this.scaleKey) return;
    this.scaleKey = key;
    this.onScale(bar);
  }

  private flushGap(): number {
    return Math.min(MAX_FLUSH_INTERVAL, Math.max(FLUSH_INTERVAL, this.frameCost * FLUSH_COST_FACTOR));
  }

  /**
   * Everything that depends on the whole set of parts, done once for however many arrived since
   * the last time: the drawing mode, the ground and its shadow, the camera's limits and frame,
   * view-mode extras, and the report to the store.
   */
  /** How many times `flushContent` has run, and the total time it spent (T-096 attribution). */
  flushCount = 0;
  flushMs = 0;

  private flushContent(): void {
    const flushStart = performance.now();
    this.flushCount += 1;
    this.contentDirty = false;
    this.lastFlush = performance.now();
    this.syncBatchMode();
    // The model's size changed with its parts, so the section's tolerance is recomputed on next use.
    this.spanCache = null;
    // The parts may have arrived, left or been recoloured; the caps follow (defect 4). This is the
    // signature-guarded call, so a flush that changed nothing about them costs one string compare.
    this.refreshSectionCaps();
    // From the parts themselves, however they are drawn: they are what the ground and the camera frame.
    const bounds = new THREE.Box3();
    const kinds = new Set<string>();
    let glass = false;
    for (const part of this.parts.values()) {
      if (!part.box.isEmpty()) bounds.union(part.box);
      kinds.add(part.group.kind);
      if (part.source !== SAMPLE_SOURCE && !this.glassLooks.has(part.group.kind)) glass = true;
    }
    if (this.batched) this.warmKinds(kinds);
    // Put off while parts keep arriving: each flush moves it on (see `warmGlass`).
    if (glass) this.glassDue = performance.now() + GLASS_WARM_DELAY;
    // Same for the pick overlay's instanced program, which the first box select would otherwise
    // compile on its own frame (see `warmPickSelect`).
    // once per context and batch mode: the programs stay compiled, and the pass is a full render.
    if (this.pickSelectWarmed !== this.batched) this.pickSelectDue = performance.now() + PICK_WARM_DELAY;
    const has = !bounds.isEmpty();
    if (has) {
      this.rig.setContent(this.fitCorners(false), bounds.getBoundingSphere(new THREE.Sphere()));
      this.shadow.place(bounds, this.grid.place(bounds).y);
    } else {
      this.grid.hide();
      this.shadow.hide();
      this.setAnchor(null);
    }
    this.batchPool.refresh();
    this.mergedStale = true;
    this.applyVisibility();
    this.applyViewMode();
    this.applyHighlights();
    const sample = has ? this.parts.values().next().value : undefined;
    if (sample) this.outline.warmUp(this.renderer, this.scene, this.rig.camera, sample.mesh, this.batched ? "batched" : "meshes");
    this.frameContent(has);
    this.publishContent();
    this.idIndicesDirty = true;
    this.idDirty = true;
    this.needsRender = true;
    this.flushMs += performance.now() - flushStart;
  }

  /**
   * The first content after an empty scene arrives with the intro move. After that a source that
   * comes or goes frames the view again, but only while the user has not taken the camera: the next
   * solid of an import must not pull the view away from where they put it. A mesh replacing its own
   * coarse version changes nothing about the frame.
   */
  private frameContent(has: boolean): void {
    const changed = this.sourcesChanged;
    this.sourcesChanged = false;
    if (!has) {
      this.hadContent = false;
      return;
    }
    if (!this.hadContent) {
      this.hadContent = true;
      this.rig.intro();
    } else if (changed && !this.rig.touched) {
      this.rig.fit();
    }
  }

  /**
   * The world extent along one axis, in metres, or null while nothing is shown.
   *
   * For the section slider's range: the cut has to be able to reach both ends of the model, and only
   * the scene knows where those are. Built from the same shown-part boxes `fitCorners` frames with, so
   * a hidden part cannot stretch the slider past what is on screen.
   */
  axisRange(axis: "x" | "y" | "z"): [number, number] | null {
    const union = new THREE.Box3();
    let found = false;
    for (const part of this.parts.values()) {
      if (!part.shown || part.box.isEmpty()) continue;
      union.union(part.box);
      found = true;
    }
    if (!found) return null;
    const min = axis === "x" ? union.min.x : axis === "y" ? union.min.y : union.min.z;
    const max = axis === "x" ? union.max.x : axis === "y" ? union.max.y : union.max.z;
    return [min, max];
  }

  /**
   * The section's cut-face state, for the review harness.
   *
   * The stencil pass has several ways to do nothing silently — no stencil buffer, a layer the camera
   * is not on, a part left unmarked — and none of them is visible in a screenshot if the caps happen
   * to be drawn somewhere harmless. These are the figures that tell them apart.
   */
  capsInfo(): { groups: number; marks: number; quad: boolean; active: boolean; rebuilds: number } {
    return { ...this.sectionCaps.info(), rebuilds: this.capRebuilds };
  }

  /**
   * What the scene draws of the mesh (T-211), for the review harness: the patches in the scene and how
   * many of them are shown, the cell-edge objects loaded and how many are drawn, and which layers the view
   * filter lets through. A screenshot cannot tell "the edges are off" from "the edges failed to load".
   */
  meshInfo(): {
    patches: number;
    patchesShown: number;
    cellEdges: number;
    cellEdgesDrawn: number;
    geometryLayer: boolean;
    meshLayer: boolean;
    /** Sources with contour lines, and the vertices (two per segment) of all of them. */
    contours: number;
    contourVertices: number;
  } {
    let patches = 0;
    let patchesShown = 0;
    for (const part of this.parts.values()) {
      if (!this.isMeshPart(part)) continue;
      patches++;
      if (part.shown) patchesShown++;
    }
    let drawn = 0;
    for (const lines of this.cellEdges.values()) if (lines.visible) drawn++;
    let contourVertices = 0;
    for (const lines of this.contourLines.values()) contourVertices += lines.geometry.getAttribute("position").count;
    return {
      patches,
      patchesShown,
      cellEdges: this.cellEdges.size,
      cellEdgesDrawn: drawn,
      geometryLayer: this.layers.geometry,
      meshLayer: this.layers.mesh,
      contours: this.contourLines.size,
      contourVertices,
    };
  }

  /**
   * What a click ESCALATES to on an edge or a vertex (ADR-0022 §15.4): a wire, or a body.
   *
   * `clicks` decides: one is the id itself, two is the wire through an edge (or a point's incident
   * edges), three is every edge and vertex of the picked body. The walk itself is pure (`wire.ts`);
   * this is the part that needs the loaded record, which is why it lives here.
   *
   * **The incidence is built once per part and cached**, because the walk needs it and the record does
   * not carry it: `TopoVertex.edges` is the vertex-to-edge list, and edge-to-vertex is its inverse. The
   * heat sink has thousands of edges, so rebuilding the inverse per click would be felt.
   *
   * Returns the id alone when nothing is loaded, so the caller cannot be handed an empty escalation.
   */
  topologyEscalation(
    part: string,
    kind: "edge" | "vertex",
    id: number,
    clicks: number,
  ): { kind: "edge" | "vertex"; ids: number[] } {
    const same = { kind, ids: [id] };
    const topology = this.topologies.get(part);
    if (topology === undefined) return same;
    if (clicks >= 3) {
      // Triple: every edge of the body, whichever kind was clicked. §15.4 escalates to the body's
      // topology, and an EDGE selection is what carries it — a body can have hundreds of vertices and
      // the useful escalation is the edges, which is also what an edge tag can consume.
      return { kind: "edge", ids: wholePart(topology.edges, topology.vertices).edges };
    }
    if (clicks !== 2) return same;
    const incidence = this.incidenceOf(part, topology);
    if (incidence === null) return same;
    if (kind === "vertex") {
      // **A double-click on a POINT selects the EDGES that meet there** (§15.4), so the result's kind
      // changes. Returning the ids while keeping the caller's kind put edge ids into the VERTEX map:
      // measured, a double-click on vertex 11 (incident edges `[13, 14, 15]`) left `edges={}` and the
      // vertices holding edge ids. The kind has to travel with the ids, not be inferred from the click.
      return { kind: "edge", ids: edgesAtVertex(id, incidence) };
    }
    // An edge's wire. The walk starts at the edge's own first end and REPORTS a branch rather than
    // guessing a direction, so either way round is honest and the result is stable.
    return { kind: "edge", ids: wireThrough(id, incidence).edges };
  }

  /** The cached incidence of one part's record, or null when the record cannot describe one. */
  private incidenceOf(part: string, topology: PartTopology): Incidence | null {
    const cached = this.incidences.get(part);
    if (cached !== undefined) return cached;
    if (topology.vertices.length === 0) return null;
    const built = incidenceOf(topology.vertices);
    this.incidences.set(part, built);
    return built;
  }

  /**
   * What the `Alt` + wheel cycle is standing on: the candidate a click would take.
   *
   * **For the review harness.** The wheel's effect is a hover overlay, which a screenshot shows but a
   * check cannot read a value out of. Reporting the choice lets a check assert that the wheel MOVED it,
   * rather than that the frame merely changed.
   */
  cycleStanding(): {
    part: string;
    kind: "edge" | "vertex";
    id: number;
    candidates: number;
  } | null {
    const one = this.topologyPicked;
    if (one === null) return null;
    return { part: one.part, kind: one.kind, id: one.id, candidates: this.topologyCycle.length };
  }

  /**
   * Where the section handle is, in client pixels, or null when there is none.
   *
   * **For the review harness, which finds the handle by its colour in a screenshot.** That scan has
   * already been wrong once (it matched the UI's own accent pixels), and when it finds nothing it
   * cannot say whether the handle is off-canvas, behind the model, or not drawn. Projecting the
   * handle's own position answers that directly, so a failing check can be diagnosed instead of
   * guessed at.
   */
  handleScreen(): { x: number; y: number; visible: boolean; inFront: boolean } | null {
    const handle = this.sectionHandle;
    if (handle === null) return null;
    const rect = this.canvas.getBoundingClientRect();
    const at = handle.position.clone().project(this.rig.camera);
    return {
      x: rect.left + ((at.x + 1) / 2) * rect.width,
      y: rect.top + ((1 - at.y) / 2) * rect.height,
      visible: handle.visible,
      // `z` outside [-1, 1] means outside the camera's near/far range; the sign says which side.
      inFront: at.z >= -1 && at.z <= 1,
    };
  }

  /**
   * The world bounds of every shown part, for the review harness.
   *
   * **Why the harness needs this.** A check that hardcodes a cut offset is a check against one model:
   * `offset 0.04` is the middle of the cold plate and *outside* the demo pipe, so the cut removed
   * nothing and the check failed while the feature worked. The harness must ask the model where its
   * middle is. `partBounds` is screen space and cannot answer that.
   */
  modelBounds(): { min: [number, number, number]; max: [number, number, number] } | null {
    const bounds = this.sceneBounds();
    if (bounds.isEmpty()) return null;
    return { min: [bounds.min.x, bounds.min.y, bounds.min.z], max: [bounds.max.x, bounds.max.y, bounds.max.z] };
  }

  /** The world bounds of one source (a part node id), shown or not, or null when the scene holds none of it. */
  sourceBounds(source: string): { min: [number, number, number]; max: [number, number, number] } | null {
    const box = new THREE.Box3();
    for (const part of this.parts.values()) {
      if (part.source === source && !part.box.isEmpty()) box.union(part.box);
    }
    if (box.isEmpty()) return null;
    return { min: [box.min.x, box.min.y, box.min.z], max: [box.max.x, box.max.y, box.max.z] };
  }

  /** The clip plane the renderer holds, for the review harness: normal, constant and count. */
  planeInfo(): { count: number; normal: number[]; constant: number }[] {
    return (this.renderer.clippingPlanes ?? []).map((plane) => ({
      count: 1,
      normal: [plane.normal.x, plane.normal.y, plane.normal.z],
      constant: plane.constant,
    }));
  }

  /**
   * The world bounds of every shown patch, or an empty box when nothing is shown.
   *
   * For the section's cap quad, which is sized from the model so it covers the widest cut the plane
   * can make. Built from the parts' own boxes, which the rest of the scene keeps current.
   */
  private sceneBounds(): THREE.Box3 {
    const box = new THREE.Box3();
    for (const part of this.parts.values()) {
      if (part.shown && !part.box.isEmpty()) box.union(part.box);
    }
    return box;
  }

  /**
   * Tell the caps which parts to mark, and in what colours.
   *
   * Called when the parts or their looks change, not per frame: it rebuilds child meshes, which is
   * real work. A hidden part is left out, so a hidden body does not cap its own cut.
   */
  // **The caps are rebuilt when the SECTION is applied, not when the content flushes.**
  //
  // Syncing them from `flushContent` was tried and reverted: the marks are child meshes OF the parts,
  // so adding them dirties the very content that triggered the flush, and the pass ended up fighting
  // its own trigger — measured as a section whose plane was correct (`normal [0,0,1]`, `constant 0`)
  // while the model came out whole and uncut. Syncing on `setSection` is one rebuild per section
  // change, which is the only moment a cap's shape can change; a colour edited with the section
  // already on is picked up when the section is next applied.

  /**
   * A signature of what the caps are drawn from, so they are rebuilt when it changes and not before.
   *
   * The cap's marks are child meshes of the parts, so building them is real work — two meshes per
   * part per colour — and doing it every frame would cost a drag its frame budget. The signature
   * covers exactly what a cap depends on: which parts are drawn, and in what colour and opacity.
   */
  private sectionCapsSignature(): string {
    // **The geometry's own identity, which the part's name and colour cannot carry** (Claude's defect
    // 7). `refill` replaces `part.mesh.geometry` on a coarse-to-final pass and on a re-import
    // (`scene.ts`: `part.mesh.geometry = geometry.geometry`, then the previous geometry is disposed),
    // and it leaves the name, colour and bucket untouched — so a signature built from those alone was
    // UNCHANGED and `refreshSectionCaps` returned early. The marks kept the disposed geometry: the cap
    // drew the old cut, and the old geometry could not be freed while they referenced it.
    //
    // `geometry.uuid` is three's own per-object identity, so it changes exactly when the geometry
    // object does and needs no bookkeeping to keep in step. Read per part here rather than stored on
    // `Part`, because that is one place instead of two.
    const parts = [...this.parts.values()].map((part) => ({
      name: part.name,
      shown: part.shown,
      color: part.color,
      bucket: part.bucket,
      batched: part.batched,
      geometry: part.mesh.geometry.uuid,
    }));
    return capsSignature(this.sectionCaps.showing, parts);
  }

  /**
   * Rebuild the caps if what they are drawn from has changed (Claude's defect 4).
   *
   * Caps were built in `setSection` and **nowhere else**, so a part that arrived, was hidden, was
   * unhidden, or had its colour changed while a section was on stayed uncapped — or kept the cap of
   * its previous look — until the slider was moved. Called from the flush (a part arrived or left) and
   * from `setVisibility` (the user hid one), which are the two ways the parts' drawn set changes.
   */
  private refreshSectionCaps(): void {
    if (!this.sectionCaps.showing) {
      this.sectionCapsKey = "";
      return;
    }
    const key = this.sectionCapsSignature();
    if (key === this.sectionCapsKey) return;
    this.sectionCapsKey = key;
    this.syncSectionCaps();
  }

  /** How many times the cap marks have been rebuilt, for the probe and the drag-cost test. */
  private capRebuilds = 0;

  private syncSectionCaps(): void {
    if (!this.sectionCaps.showing) return;
    this.capRebuilds += 1;
    const shown: { mesh: THREE.Mesh; colour: THREE.Color; opacity: number }[] = [];
    for (const part of this.parts.values()) {
      // A mesh patch is one piece of a closed surface, not a closed body: a cap of it would be wrong.
      if (!part.shown || part.batched !== null || this.isMeshPart(part)) continue;
      // The part's own drawn colour and opacity, so its cap matches its body. A translucent body
      // keeps its opacity rather than capping opaque.
      shown.push({
        mesh: part.mesh,
        colour: new THREE.Color().setRGB(part.color[0], part.color[1], part.color[2], THREE.SRGBColorSpace),
        opacity: part.bucket >= OPAQUE_BUCKET ? 1 : part.bucket / OPAQUE_BUCKET,
      });
    }
    this.sectionCaps.setParts(shown);
  }

  /**
   * The bodies that are a region's domain, from `region.list`.
   *
   * **Fit leaves them out** (Claude's decision (a), 2026-10-19): the demo's fluid spans the assembly
   * plus 80 mm at each end, so framing the union made the pipe look like a close-up of a flat face.
   * **They stay VISIBLE** - this decides framing only, never `shown`.
   */
  setRegionBodies(ids: ReadonlySet<string>): void {
    this.regionBodies = ids;
  }

  private regionBodies: ReadonlySet<string> = new Set();

  /**
   * The patches a box or a lasso must skip: the bodies left out of the simulation.
   *
   * **`source`, not `name`** - measured: a marquee reports `part.source`, so a set of names never
   * matched it and the rule silently did nothing (the box picked the excluded body, and no message
   * appeared because nothing had been skipped by the comparison).
   */
  excludedParts(): string[] {
    return [...this.parts.values()].filter((part) => part.simulate === false).map((part) => part.source);
  }

  /** Corners of the part boxes for framing; the union's corners instead once there are very many parts. */
  private fitCorners(onlyShown: boolean): THREE.Vector3[] {
    const corners: THREE.Vector3[] = [];
    const union = new THREE.Box3();
    let boxes = 0;
    /*
      **Which bodies a fit frames**, from the pure rule in `fitTargets.ts`: the solids, or EVERY body
      when the project has no solids (a fluid-only project would otherwise fit nothing, and a blank
      viewport is worse than a fluid-framed one).
    */
    const candidates = [...this.parts.values()].filter((part) => !part.box.isEmpty());
    // **`this.parts` is keyed by NAME**, and `region.list` reports ids - so `setRegionBodies` is given
    // the names the scene can match, resolved once by the caller that knows both.
    const targets = this.fitEverything
      ? new Set(candidates.map((part) => part.name))
      : (namesOfSources(candidates.filter((part) => !onlyShown || part.shown), this.fitOnly) ?? fitTargetIds(candidates.map((part) => ({ id: part.name, regionBody: this.regionBodies.has(part.name) }))));
    for (const part of this.parts.values()) {
      if ((onlyShown && !part.shown) || part.box.isEmpty()) continue;
      if (!targets.has(part.name)) continue;
      boxes++;
      union.union(part.box);
      if (boxes <= MAX_FIT_BOXES) boxCorners(part.box, corners);
    }
    if (boxes > MAX_FIT_BOXES) {
      corners.length = 0;
      boxCorners(union, corners);
    }
    return corners;
  }

  // ------------------------------------------------------------------ state that derives from the parts
  /** The isolated patch, unless it is gone or its layer is off (isolating a part must not blank what the view shows instead). */
  private isolatedName(): string | null {
    if (this.isolated === null) return null;
    const part = this.parts.get(this.isolated);
    return part !== undefined && this.layerOn(part) ? this.isolated : null;
  }

  private wantsShown(part: Part, isolated: string | null): boolean {
    return part.enabled && this.layerOn(part) && (isolated !== null ? part.name === isolated : !this.hidden.has(part.name));
  }

  /** True for a patch of a mesh (kind `mesh`, from `/api/mesh`), as opposed to a part of the geometry. */
  private isMeshPart(part: Part): boolean {
    return part.group.kind === MESH_KIND;
  }

  private layerOn(part: Part | undefined): boolean {
    return part === undefined || (this.isMeshPart(part) ? this.layers.mesh : this.layers.geometry);
  }

  private applyVisibility(): void {
    const isolated = this.isolatedName();
    for (const part of this.parts.values()) {
      part.shown = this.wantsShown(part, isolated);
      this.showPart(part);
      if (part.edges) part.edges.visible = part.shown && this.viewMode === "edges";
    }
    this.rig.setFitPoints(this.fitCorners(true));
    this.applyCellEdges();
    this.shadow.invalidate();
    // The merged edges are those of the shown parts.
    this.mergedStale = true;
    if (this.hovered !== null && !this.parts.get(this.hovered)?.shown) {
      // The part under the pointer went away: a cycle standing on one of its faces goes too.
      this.setHover(null);
      this.resetCycle();
    }
    const tinted = this.faces.owner as Part | null;
    if (tinted && !tinted.shown) this.clearFaceHover();
    // Parts that went hidden or isolated-away drop out of the pick overlay.
    this.selectionKey = null;
    this.updateSelection();
    // The id buffer draws what is shown, so a visibility change is one it has to make again (T-032).
    this.idDirty = true;
    this.needsRender = true;
  }

  private applyViewMode(): void {
    const wire = this.viewMode === "wireframe";
    const edges = this.viewMode === "edges";
    for (const part of this.parts.values()) {
      // A mesh patch keeps its surface and draws no feature edges, whatever the mode: its triangles are
      // not its cells (the wire would show the diagonals), and its own cell edges are `setSourceCellEdges`.
      const mesh = this.isMeshPart(part);
      // Every part's mesh, batched or not: a batched one draws with it as soon as it is highlighted.
      part.mesh.material = wire && !mesh ? part.look.wire : (part.look.glass ?? part.look.surface);
      // **The Edges switch overrides the view mode** (the owner's display toggles): `shaded with
      // edges` is a look, and the switch is a decision to stop drawing them at all - so a user who
      // turns them off keeps them off when the mode is unchanged.
      if (edges && this.edgesOn && !this.batched && !mesh) this.ensureEdges(part);
      if (part.edges) part.edges.visible = part.shown && edges;
    }
    this.batchPool.setWireframe(wire);
    if (edges && this.batched) this.mergedStale = true;
    else this.mergedEdges.hide();
    this.needsRender = true;
  }

  private edgeSegmentsOf(part: Part): Float32Array {
    part.edgeSegments ??= featureEdges(part.mesh.geometry, EDGE_ANGLE);
    return part.edgeSegments;
  }

  /** Feature edges are costly on large meshes, so they are found when first asked for. */
  private ensureEdges(part: Part): void {
    if (part.edges !== null) return;
    const lines = edgeLines(this.edgeSegmentsOf(part), this.edgeMaterial, `${part.name}:edges`);
    if (!lines) return;
    this.scene.add(lines);
    part.edges = lines;
  }

  /**
   * The edges of a batched scene: finds those of shown parts that have none yet, for at most
   * EDGE_SLICE ms, and redraws the merged lines when that finished or EDGE_MERGE_INTERVAL has passed.
   * True when the picture changed. Thousands of parts take seconds to go through, which is why it is
   * spread over frames rather than done in one go.
   */
  private stepEdges(): boolean {
    if (!this.batched || this.viewMode !== "edges" || !this.mergedStale) return false;
    const started = performance.now();
    let done = true;
    for (const part of this.parts.values()) {
      if (!part.shown || part.edgeSegments !== null || this.isMeshPart(part)) continue;
      if (performance.now() - started > EDGE_SLICE) {
        done = false;
        break;
      }
      this.edgeSegmentsOf(part);
    }
    if (!done && performance.now() - this.lastMerge < EDGE_MERGE_INTERVAL) return false;
    const chunks: Float32Array[] = [];
    for (const part of this.parts.values()) if (part.shown && part.edgeSegments !== null && !this.isMeshPart(part)) chunks.push(part.edgeSegments);
    this.mergedEdges.set(chunks);
    this.lastMerge = performance.now();
    this.mergedStale = !done;
    return true;
  }

  private applyHighlights(): void {
    for (const part of this.parts.values()) {
      part.liftTarget = part.name === this.selected ? 1 : part.name === this.hovered ? HOVER_LIFT : 0;
      if (part.liftTarget > 0) this.raise(part);
      else if (part.lift === 0) this.lower(part);
    }
    this.needsRender = true;
  }

  private paint(part: Part): void {
    paintLook(part.look, this.accent, this.theme.isDark, part.lift);
  }

  /** Fades the highlights towards their targets; true while any is still moving. */
  private stepHighlights(dt: number): boolean {
    const k = 1 - Math.exp(-dt / LIFT_TIME);
    let moving = false;
    for (const part of this.parts.values()) {
      if (part.lift === part.liftTarget) continue;
      part.lift += (part.liftTarget - part.lift) * k;
      if (Math.abs(part.liftTarget - part.lift) < 0.004) part.lift = part.liftTarget;
      this.paint(part);
      if (part.lift === 0) this.lower(part);
      moving = true;
    }
    return moving;
  }

  // ------------------------------------------------------------------ the face under the pointer
  /** Tints the face of `hit` (see `FaceHighlight`); a part without face ids has none to tint. */
  private setFaceHover(hit: Hit | null): void {
    const ids = hit?.part.faceIds ?? null;
    if (!hit || ids === null) {
      this.clearFaceHover();
      return;
    }
    const source = { mesh: hit.part.mesh, faceIds: ids, indices: hit.part.indices };
    if (this.faces.show(hit.part, source, hit.triangle)) this.needsRender = true;
  }

  private clearFaceHover(): void {
    if (this.faces.clear()) this.needsRender = true;
  }

  // ------------------------------------------------------------------ the id buffer (T-032)
  /**
   * Numbers the parts for the id buffer and keeps the way back: `idParts[index]` is the patch a pixel
   * names. Part numbers are 1-based, because 0 is the target's clear value (idbuffer.ts). A scene of
   * more patches than 16 bits can name leaves the ones past the limit unnumbered — and so unpickable
   * by a box or a lasso — with one warning, never one per frame.
   */
  private assignIdIndices(): void {
    if (!this.idIndicesDirty) return;
    this.idIndicesDirty = false;
    this.idParts = [];
    let next = 1;
    let over = false;
    for (const part of this.parts.values()) {
      if (next > MAX_ID_PARTS) {
        over = true;
        part.idIndex = PART_INDEX_NONE;
        continue;
      }
      part.idIndex = next;
      this.idParts[next] = part;
      next++;
      // A batched part is named by its instance, through the batch's texture (batch.ts).
      if (part.batched !== null) this.batchPool.setPartIndex(part.batched, part.idIndex);
    }
    if (over && !this.idOverflowLogged) {
      this.idOverflowLogged = true;
      console.warn(`more than ${MAX_ID_PARTS} patches: the ones past that have no id-buffer index and no box or lasso pick`);
    }
  }

  /** The patch an id-buffer pixel names, or null for a pixel no part covers. */
  private partById(index: number): Part | null {
    return this.idParts[index] ?? null;
  }

  /** The transport face id of an id-buffer face ordinal (T-032); -1 when there is none. */
  private faceOfOrdinal(part: Part, ordinal: number): number {
    if (ordinal === FACE_ID_NONE || ordinal >= part.faceTable.length) return -1;
    return part.faceTable[ordinal];
  }

  /**
   * Draws the id buffer when a camera, content or visibility change has marked it dirty. The pass is
   * one render of the part layer into an offscreen target; nothing is read back here, so a hover that
   * asks for one pixel pays for the pass only when the view has moved since the last one.
   */
  private renderIdPass(): void {
    if (!this.idDirty || this.disposed || this.contextLost) return;
    if (this.contentDirty) this.flushContent();
    this.assignIdIndices();
    this.idActive = true;
    this.idPass.render(this.renderer, this.scene, this.rig.camera);
    this.idActive = false;
    this.idDirty = false;
  }

  /** The render target's own pixels for a client position, y measured from the bottom as WebGL does. */
  private idPoint(clientX: number, clientY: number): { x: number; y: number } {
    const rect = this.canvas.getBoundingClientRect();
    const size = this.idPass.size;
    return {
      x: (clientX - rect.left) * (size.width / Math.max(1, rect.width)),
      y: size.height - (clientY - rect.top) * (size.height / Math.max(1, rect.height)),
    };
  }

  /**
   * The part and face under a client position, through the id buffer (T-032): one pixel read back,
   * so the probe and, when it is measured faster, hover and click cost one read instead of a raycast
   * through every patch. The `Alt` + wheel cycle still starts from the raycaster: a single pixel can
   * name what is in front, never everything behind it (ADR-0022 6.1).
   */
  idPickAt(clientX: number, clientY: number): PickHit | null {
    if (this.disposed || this.contextLost) return null;
    if (this.gizmo.contains(clientX, clientY)) return null;
    this.renderIdPass();
    const at = this.idPoint(clientX, clientY);
    const started = performance.now();
    const pixel = this.idPass.readPixel(this.renderer, Math.floor(at.x), Math.floor(at.y));
    this.lastIdReadMs = performance.now() - started;
    if (pixel === null || pixel.part === PART_INDEX_NONE) return null;
    const part = this.partById(pixel.part);
    return part === null ? null : { part: part.source, patch: part.name, face: this.faceOfOrdinal(part, pixel.face) };
  }

  /** The probe's switch: hover and click ask the id buffer instead of the raycaster (T-032). */
  setIdPickEnabled(on: boolean): void {
    this.idPickFromBuffer = on;
  }
  get idPickEnabled(): boolean {
    return this.idPickFromBuffer;
  }

  /**
   * The lasso toggle of the Modes island (T-032): the next drag on empty space draws a lasso instead
   * of a box. `Alt` asks for one whatever the toggle says, the toggle being what a user without a
   * keyboard reaches for.
   */
  setLassoMode(on: boolean): void {
    this.lassoMode = on;
  }

  /**
   * The mouse scheme (ADR-0021 3.4). The rig's button bindings come from `CameraRig.setNavMode`;
   * what belongs here is what a LEFT drag means, because the left button is also select and the
   * marquee:
   *
   * - `cad` and `blender`: a press on a patch orbits, a drag on empty space draws the box.
   * - `paraview`: a plain left drag orbits ANYWHERE; the box needs Shift (which also still means
   *   "add") or the Lasso toggle.
   * - `toggle`: `orbitMode` decides — on, a left drag orbits anywhere; off, it is the box.
   *
   * Every scheme keeps rotation reachable: the middle button rotates in all of them.
   */
  /** True while nothing in the view can be selected (Results): a left drag then always orbits. */
  setOrbitOnly(on: boolean): void {
    this.orbitOnly = on;
  }

  setNavMode(mode: NavMode, orbitMode: boolean): void {
    this.navMode = mode;
    this.orbitMode = orbitMode;
    this.rig.setNavMode(mode);
  }

  /**
   * Which mouse button does what now, by the names three uses, as a copy. The review harness reads
   * it (`tools/test_interactions.py`) to check that choosing a scheme really moved the bindings
   * rather than only the button's label.
   */
  navButtons(): { LEFT: string | null; MIDDLE: string | null; RIGHT: string | null } {
    const names: Record<number, string> = { 0: "ROTATE", 1: "DOLLY", 2: "PAN" };
    const buttons = this.rig.controls.mouseButtons as Record<string, number | null>;
    const named = (value: number | null | undefined): string | null =>
      value === null || value === undefined ? null : (names[value] ?? String(value));
    return {
      LEFT: named(buttons.LEFT),
      MIDDLE: named(buttons.MIDDLE),
      RIGHT: named(buttons.RIGHT),
    };
  }

  /**
   * Ask for one point in the view (T-029): the next click that meets a face answers `answer` with
   * where it met it, that face's outward normal and the part it belongs to, and the mode ends. A
   * click on empty space or on the view gizmo answers nothing and leaves the mode on, and a drag is
   * the camera's as it always was; `cancelPoint` leaves the mode without an answer. One mode at a
   * time: a second call replaces the first answer.
   */
  requestPoint(answer: PointPick): void {
    this.pointPick = answer;
    this.applyCursor();
  }

  /** Leave the point pick without answering it. */
  cancelPoint(): void {
    if (this.pointPick === null) return;
    this.pointPick = null;
    this.applyCursor();
  }

  /** True while a point is being waited for: the card's "Pick in view" state. */
  get pointPickOn(): boolean {
    return this.pointPick !== null;
  }

  /**
   * The next click on a face answers the point pick (T-029). A click that meets nothing, and the
   * view gizmo, leave the mode waiting: the pick is one-shot, not one-miss. The camera has already
   * ignored a drag that moved more than the slop, so an orbit never answers it.
   */
  private answerPoint(clientX: number, clientY: number): void {
    const answer = this.pointPick;
    if (answer === null) return;
    const hit = this.pointAt(clientX, clientY) ?? this.planeAt(clientX, clientY);
    if (hit === null) return;
    this.pointPick = null;
    this.applyCursor();
    answer(hit);
  }

  /** The plane a point pick falls back to when the click meets no part (null: none). */
  setPointPlane(plane: { axis: "x" | "y" | "z"; offset: number } | null): void {
    this.pointPlane = plane;
  }

  /**
   * Where the ray through a client position crosses `pointPlane`, as a hit with no part, patch or
   * face (`patch` empty, `face` -1): the plot over a line reads it on the slice. Null without a
   * plane, over the gizmo, for a ray along the plane or one crossing it behind the camera.
   */
  private planeAt(clientX: number, clientY: number): PointHit | null {
    const plane = this.pointPlane;
    if (plane === null || this.gizmo.contains(clientX, clientY)) return null;
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.rig.camera);
    const origin = this.raycaster.ray.origin.clone();
    const direction = this.raycaster.ray.direction.clone();
    const i = plane.axis === "x" ? 0 : plane.axis === "y" ? 1 : 2;
    const d = direction.getComponent(i);
    if (Math.abs(d) < 1e-6) return null;
    const t = (plane.offset - origin.getComponent(i)) / d;
    if (t <= 0) return null;
    const point = origin.clone().addScaledVector(direction, t);
    point.setComponent(i, plane.offset);
    const normal = new THREE.Vector3();
    normal.setComponent(i, d < 0 ? 1 : -1);
    return {
      point,
      normal,
      part: "",
      patch: "",
      face: -1,
      diagonal: 0,
      pointer: { x: clientX - rect.left, y: clientY - rect.top },
      ray: { origin, direction },
    };
  }

  /**
   * Where a ray through a client position meets the nearest shown face, with the outward normal of
   * that face (the raycaster's own normal is in the object's space) and the size of the part it is
   * on. The point pick needs the two; `pick` and `pickAll` keep their triangle alone.
   */
  private pointAt(clientX: number, clientY: number): PointHit | null {
    if (this.gizmo.contains(clientX, clientY)) return null;
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.rig.camera);
    const candidates: THREE.Mesh[] = [];
    for (const part of this.parts.values()) if (part.shown) candidates.push(part.mesh);
    // **The cut is respected: a hit on removed geometry is skipped and the next kept one is taken**
    // (Claude's defect 7). Returning the removed hit would name a face the user cannot see; returning
    // nothing would make the model behind the cut unpickable, which is the opposite of what cutting a
    // model open is for. The rule itself is pure and tested in `sectionPick.ts`.
    // **Front faces only, as `pickAll` does** (found by a verification audit of the defect-4 fix).
    // This is a SECOND raycast path, used by the Measure tool and the operation card's seed pick, and
    // it had no facing filter: after a cut those two still took the far wall's INNER back face, which
    // is the defect the `pickAll` fix was meant to remove. The two paths have to agree, or the same
    // pixel answers differently depending on which tool is asking.
    const intersection = this.firstKeptIntersection(
      this.raycaster
        .intersectObjects(candidates, false)
        .filter((hit) => isFrontFace(hit, this.raycaster.ray.origin)),
    );
    if (intersection === undefined) return null;
    const part = this.partOf.get(intersection.object);
    if (part === undefined) return null;
    const triangle = intersection.faceIndex ?? -1;
    const normal = new THREE.Vector3(0, 0, 1);
    if (intersection.face) {
      normal.copy(intersection.face.normal).applyMatrix3(new THREE.Matrix3().getNormalMatrix(intersection.object.matrixWorld)).normalize();
    }
    return {
      point: intersection.point.clone(),
      normal,
      part: part.source,
      patch: part.name,
      face: faceIdOf(part, triangle),
      diagonal: part.box.getSize(new THREE.Vector3()).length(),
      pointer: { x: clientX - rect.left, y: clientY - rect.top },
      ray: { origin: this.raycaster.ray.origin.clone(), direction: this.raycaster.ray.direction.clone() },
    };
  }

  // ------------------------------------------------------------------ the measure tool (T-098)
  /**
   * Measure mode on or off (`view.measure`, the `M` chord). While it is on, a click that meets a
   * face records a world point instead of picking a body; leaving the mode takes the measurement
   * with it, so what the label says and what the viewport draws can never be two different things.
   *
   * It rides the one-shot point pick (T-029), which is one mode at a time: arming this replaces
   * whatever was waiting. Leaving it cancels the wait only when the wait is this tool's own, so
   * leaving measure mode cannot eat an operation card's seed pick.
   */
  /**
   * The probe's point pick (results item 2): the callback the next click answers.
   *
   * **The same one-shot mechanism Measure uses** (`requestPoint`), because both ask "where did the
   * user click?" - and a second pick mechanism would be a second chance for them to disagree about
   * which gesture owns the left button.
   */
  onProbe: ((hit: PointHit) => void) | null = null;

  setProbePick(on: boolean): void {
    if (on === this.probing) return;
    this.probing = on;
    if (on) {
      this.requestPoint(this.takeProbePoint);
      return;
    }
    if (this.pointPick === this.takeProbePoint) this.cancelPoint();
  }

  private takeProbePoint = (hit: PointHit): void => {
    this.probing = false;
    this.onProbe?.(hit);
  };

  setMeasureMode(on: boolean): void {
    if (on === this.measuring) return;
    this.measuring = on;
    if (on) {
      this.requestPoint(this.takeMeasurePoint);
      this.publishMeasureDetails();
      return;
    }
    if (this.pointPick === this.takeMeasurePoint) this.cancelPoint();
    this.clearMeasure();
  }

  /** Starts over without leaving the tool: the panel's Clear. The next click is a first element again. */
  resetMeasure(): void {
    if (!this.measuring) return;
    this.clearMeasure();
    this.requestPoint(this.takeMeasurePoint);
  }

  /**
   * What the measurement is, as a copy, for the probe the review harness reads. Figures only: a
   * caller has no reference it could use to change what is drawn.
   */
  measureState(): { on: boolean; a: Vec3 | null; b: Vec3 | null; label: string | null; metres: number | null } {
    return {
      on: this.measuring,
      a: this.measureA === null ? null : { x: this.measureA.x, y: this.measureA.y, z: this.measureA.z },
      b: this.measureB === null ? null : { x: this.measureB.x, y: this.measureB.y, z: this.measureB.z },
      label: this.measurement === null ? null : this.measurement.label,
      metres: this.measurement === null ? null : this.measurement.metres,
    };
  }

  /**
   * Removes the measurement: the line, the end markers and the readout. The measurement is view
   * state, so there is nothing else to put back.
   */
  clearMeasure(): void {
    this.measureA = null;
    this.measureB = null;
    this.measurement = null;
    // The intrinsic too: leaving it set kept the label on screen after Esc or the mode toggle,
    // because `measureReadout` reads it before the distance and does not consult `measurement`.
    this.intrinsic = null;
    this.measureElementA = null;
    this.measureElementB = null;
    this.disposeMeasure();
    if (this.measureSent !== "") {
      this.measureSent = "";
      this.onMeasure(null);
    }
    this.publishMeasureDetails();
    this.needsRender = true;
  }

  /** Tells the panel what the tool now shows: nothing when it is off, else the state of the pair. */
  private publishMeasureDetails(): void {
    if (!this.measuring) {
      this.onMeasureDetails(null);
      return;
    }
    const a = this.measureA;
    this.onMeasureDetails(
      measureDetails({
        a: this.measureElementA,
        b: this.measureElementB,
        atA: a === null ? null : { x: a.x, y: a.y, z: a.z },
        measurement: this.measurement,
      }),
    );
  }

  /**
   * Records a point the user clicked: the first click of a measurement is A, the second is B and
   * completes it, and a click after that starts a NEW measurement at the point it picked.
   *
   * Chaining from B was the alternative (the next measurement running B -> C) and is rejected: it
   * reads well until two unrelated distances are wanted, because the first point of the second
   * measurement is then one the user never clicked and nothing on screen says so — the label can
   * describe a pair nobody chose. It also leaves no way out of the chain short of leaving the mode.
   * Restarting keeps every measurement a pair of clicked points, and the finished one leaves as the
   * new one begins, so the label on screen always belongs to the two markers beside it.
   */
  /**
   * The measure element a click landed on (T-113).
   *
   * The one-shot point pick is a raycast onto a *face*, so it always knows that face's plane: the
   * surface point and its outward normal are both in the hit. That is what makes a **face** measure
   * available with no extra data, and it is the case that matters most ("how far is this boss from
   * that wall" is a face-to-face, normal distance).
   *
   * An **edge** or a **vertex** is not reachable from this pick: a ray hits the shaded face between
   * them, so their ids never appear here. Selecting them needs the screen-space pick of T-057/T-058
   * over the part's topology record, which is not wired into the click path yet (ADR-0022 §15.3).
   * Until it is, this returns the face element and the caller falls back to a point measurement —
   * never a guess at an edge the user did not pick.
   *
   * A hit without face ids (the sample part, or a mesh-only import with no B-rep) has no plane to
   * name, so it too falls back to the point measurement.
   */
  private elementAt(hit: PointHit): MeasureElement | null {
    // **The pick mode decides what a click means**, which is the whole of "measure what is
    // selected": in Edge mode it is the edge, in Point mode the vertex, in Face and Body mode the
    // FACE behind the pointer. Testing edges and vertices unconditionally made a face area
    // unreachable — measured over 420 single clicks on the demo, every one returned an edge length,
    // because the 6 px edge tolerance catches a click almost anywhere on a curved body. A user in
    // Face mode asking for an area was silently given a neighbouring edge's length.
    //
    // The mode is only consulted for WHICH kind to prefer; a kind the click cannot actually land on
    // still falls through, so a click in Edge mode that misses every edge is not a dead click.
    if (this.measureWant === "edge" || this.measureWant === "vertex") {
      const nearest = this.nearestTopology(hit.pointer, this.measureWant);
      if (nearest !== null) return nearest;
    }
    if (hit.face < 0 || hit.normal.lengthSq() === 0) return null;
    const point: Vec3 = { x: hit.point.x, y: hit.point.y, z: hit.point.z };
    const normal: Vec3 = { x: hit.normal.x, y: hit.normal.y, z: hit.normal.z };
    // The face's identity, so two clicks on ONE face measure the distance between them rather than
    // the angle between their normals. `part:face` is unique within the scene, and both come from the
    // pick that already happened.
    //
    // The face's own AREA and centroid come from the part's record when it is loaded. They are absent
    // for a mesh-only part (no B-rep, so no true area) — the click then measures a distance, which is
    // still honest, rather than an area summed from triangles (§15.1 forbids that for measurement).
    // Body mode: the whole part's volume, not the face that was pointed at. A body has no id in the
    // record — the solids are the part's — so the FIRST solid is reported and the caption names it.
    // A part that is several bodies is a real case (a compound import); reporting the first is honest
    // and naming it in the caption is what keeps it from reading as the whole part's volume.
    if (this.measureWant === "body") {
      const solid = this.solidSizeAt(hit.part, 0);
      if (solid !== null) {
        return { kind: "body", part: hit.part, solidId: 0, volume: solid.volume, centre: solid.centroid };
      }
    }
    const size = this.faceSizeAt(hit.part, hit.face);
    const shape = this.faceShapeAt(hit.patch, hit.face);
    return {
      kind: "face",
      point,
      // A planar face's normal is its triangles' own, not the interpolated one at the click.
      normal: shape?.normal ?? normal,
      face: `${hit.part}:${hit.face}`,
      part: hit.part,
      faceId: hit.face,
      ...(size === null ? {} : { area: size.area, centre: size.centroid }),
      ...(shape === null ? {} : { planar: shape.planar, samples: shape.samples, triangles: shape.triangles }),
    };
  }

  /**
   * Whether one face is planar, and points on it, from the drawn triangles of its patch.
   *
   * The topology record has areas but no surface type, so planarity is read off the tessellation:
   * a face is planar when every one of its triangles faces the same way (within 0.5°), which a
   * cylinder's barrel never does. The samples are the face's vertices in world metres, strided to at
   * most `MEASURE_SAMPLES` so a fine face cannot make the closest-pair search slow.
   */
  private faceShapeAt(patch: string, face: number): { planar: boolean; normal: Vec3 | null; samples: Vec3[]; triangles: Vec3[] } | null {
    const part = this.parts.get(patch);
    if (part === undefined || part.faceIds === null || face < 0) return null;
    const position = part.mesh.geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (position === undefined) return null;
    const { indices, faceIds } = part;
    const used = new Set<number>();
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const c = new THREE.Vector3();
    const n = new THREE.Vector3();
    let first: THREE.Vector3 | null = null;
    let planar = true;
    for (let t = 0; t < faceIds.length; t++) {
      if (faceIds[t] !== face) continue;
      const i0 = indices[t * 3];
      const i1 = indices[t * 3 + 1];
      const i2 = indices[t * 3 + 2];
      used.add(i0).add(i1).add(i2);
      if (!planar) continue;
      a.fromBufferAttribute(position, i0);
      b.fromBufferAttribute(position, i1);
      c.fromBufferAttribute(position, i2);
      n.subVectors(c, b).cross(a.sub(b));
      if (n.lengthSq() === 0) continue;
      n.normalize();
      if (first === null) first = n.clone();
      else if (first.dot(n) < MEASURE_PLANAR_COS) planar = false;
    }
    if (used.size === 0) return null;
    const matrix = part.mesh.matrixWorld;
    const all = [...used];
    const stride = Math.max(1, Math.ceil(all.length / MEASURE_SAMPLES));
    const samples: Vec3[] = [];
    const v = new THREE.Vector3();
    for (let i = 0; i < all.length; i += stride) {
      v.fromBufferAttribute(position, all[i]).applyMatrix4(matrix);
      samples.push({ x: v.x, y: v.y, z: v.z });
    }
    let worldNormal: Vec3 | null = null;
    if (planar && first !== null) {
      const w = first.clone().applyMatrix3(new THREE.Matrix3().getNormalMatrix(matrix)).normalize();
      worldNormal = { x: w.x, y: w.y, z: w.z };
    }
    // The triangles themselves, for the exact minimum distance; strided past `MEASURE_TRIANGLES` (a very
    // fine face), where the figure becomes approximate rather than slow.
    const ofFace: number[] = [];
    for (let t = 0; t < faceIds.length; t++) if (faceIds[t] === face) ofFace.push(t);
    const triStride = Math.max(1, Math.ceil(ofFace.length / MEASURE_TRIANGLES));
    const triangles: Vec3[] = [];
    for (let k = 0; k < ofFace.length; k += triStride) {
      const t = ofFace[k];
      for (let corner = 0; corner < 3; corner++) {
        v.fromBufferAttribute(position, indices[t * 3 + corner]).applyMatrix4(matrix);
        triangles.push({ x: v.x, y: v.y, z: v.z });
      }
    }
    return { planar, normal: worldNormal, samples, triangles };
  }

  /** One of a part's solids, measured, or null when the record has none at that index. */
  private solidSizeAt(part: string, index: number): { volume: number; centroid: Vec3 } | null {
    const topology = this.topologies.get(part);
    if (topology === undefined) return null;
    const solid = topology.solids[index];
    if (solid === undefined) return null;
    return {
      volume: solid.volume,
      centroid: { x: solid.centroid[0], y: solid.centroid[1], z: solid.centroid[2] },
    };
  }

  /** A face's true area and centroid from the part's record, or null when it has none. */
  private faceSizeAt(part: string, face: number): { area: number; centroid: Vec3 } | null {
    const topology = this.topologies.get(part);
    if (topology === undefined) return null;
    const found = topology.faceSizes.find((item) => item.id === face);
    if (found === undefined) return null;
    return { area: found.area, centroid: { x: found.centroid[0], y: found.centroid[1], z: found.centroid[2] } };
  }

  /**
   * The edge or vertex under the pointer as a Measure element, or null when neither is close enough.
   *
   * Reuses the same projection and tolerance as `topologyPickAt` so measuring and picking agree about
   * what is under the pointer — two different tolerances would let a click pick one edge and measure
   * another. The vertices are tested first: a vertex sits on the edges that meet it, and an engineer
   * clicking a corner means the corner.
   */
  private nearestTopology(
    pointer: { x: number; y: number },
    want: "edge" | "vertex",
  ): MeasureElement | null {
    if (this.topologies.size === 0) return null;
    // Only the kind the caller asked for: in Point mode a vertex beats an edge even when an edge is
    // nearer, because the user said "point". In Edge mode a vertex is still preferred when it is under
    // the pointer — a corner IS where the user pointed — so the vertex test runs either way.
    const found = want === "vertex"
      ? this.topologyAt(pointer.x, pointer.y, "vertex")
      : this.topologyAt(pointer.x, pointer.y, "vertex") ?? this.topologyAt(pointer.x, pointer.y, "edge");
    if (found === null) return null;
    const topology = this.topologies.get(found.part);
    // The patch's OWN mesh transform: `this.parts` is keyed by patch name and a record by source id,
    // so `this.parts.get(found.part)` never matched and this function returned null for every edge
    // and vertex — the same key mismatch that emptied Edge-mode picking. Measure-of-edge therefore
    // never worked either, and fell back to a two-click distance on the face behind it.
    const mesh = this.partsOfSource(found.part).find((entry) => entry.shown)?.mesh;
    if (topology === undefined || mesh === undefined) return null;
    if (found.kind === "vertex") {
      const vertex = topology.vertices[found.id];
      if (vertex === undefined) return null;
      const world = new THREE.Vector3(...vertex.position).applyMatrix4(mesh.matrixWorld);
      return { kind: "vertex", point: { x: world.x, y: world.y, z: world.z } };
    }
    const edge = topology.edges[found.id];
    if (edge === undefined) return null;
    const points: Vec3[] = [];
    for (let i = 0; i < edge.pointsCount; i++) {
      const world = new THREE.Vector3(edge.points[i * 3], edge.points[i * 3 + 1], edge.points[i * 3 + 2]).applyMatrix4(mesh.matrixWorld);
      points.push({ x: world.x, y: world.y, z: world.z });
    }
    const centre = new THREE.Vector3(...edge.centre).applyMatrix4(mesh.matrixWorld);
    // The record's TRUE B-rep length, so the readout reports the edge rather than the chord sum of
    // its samples — a curve's polyline is always shorter than its arc (§15.1).
    return {
      kind: "edge",
      points,
      part: found.part,
      edgeId: found.id,
      length: edge.length,
      centre: { x: centre.x, y: centre.y, z: centre.z },
      straight: edge.curve === "GeomAbs_Line" || (edge.curve === "" && edge.pointsCount === 2),
    };
  }

  private takeMeasurePoint = (hit: PointHit): void => {
    if (!this.measuring) return;
    // The element the click landed on (T-113): the pick already carries the face and its normal,
    // so a face measure needs no extra work. Measuring an *edge* or a *vertex* needs the part's
    // topology record, which the scene fetches lazily (see `topologyOf`); until it is loaded the
    // click measures the surface point exactly as before, so the tool never becomes unusable.
    const element = this.elementAt(hit);
    // **The first click does double duty: it starts a distance AND shows the element's own size.**
    // An edge's length, a face's area and a body's volume are questions about one thing, so the
    // readout answers them at once; the SAME click is also point A of a distance, so the second click
    // completes `a -> b` without the user having to click A twice.
    //
    // The version before this one did not do both: a click that found an intrinsic RETURNED, so the
    // next click started a new measurement and a two-point distance could never complete. `measure()`
    // showed `b: null` after two clicks anywhere on the model — the harness caught it, and it was a
    // regression against the distances the tool was built for (node-to-node, edge-to-edge,
    // surface-to-surface). One click answers both questions; two clicks answer the second.
    const first = this.measureA === null || this.measureB !== null;
    if (first) {
      this.measureA = hit.point.clone();
      this.measureB = null;
      this.measureElementA = element;
      this.measureElementB = null;
      this.measurement = null;
      // Shown until the second click replaces it with the distance: the element's own size is what
      // one click can honestly report, and a vertex has none, so a vertex-only pair prints a distance
      // from the first click as before.
      this.intrinsic = element === null ? null : intrinsicOfElement(element);
    } else if (this.measureA !== null) {
      // The second click completes the distance. The intrinsic goes: the readout now describes the
      // pair, and leaving the element's size on screen would label a distance with an area.
      this.intrinsic = null;
      this.measureB = hit.point.clone();
      this.measureElementB = element;
      this.measurement =
        this.measureElementA !== null && element !== null
          ? measureElements(this.measureElementA, element)
          : measureBetween(this.measureA, this.measureB);
    }
    this.updateMeasure();
    this.publishMeasureDetails();
    // The pick is one shot, so it is armed again here: a measurement is two clicks, and the
    // measurement after it is one more.
    this.requestPoint(this.takeMeasurePoint);
    this.needsRender = true;
  };

  /**
   * Rebuilds the world-space drawing: a line between the two points once both are known, and a
   * marker at whichever point there is. World space is the whole point of it — orbiting, zooming and
   * a re-flush of the content need no work from this, the camera moves past a dimension that stays
   * where it was measured.
   */
  private updateMeasure(): void {
    this.disposeMeasure();
    // An element measurement is drawn between the two points its figure is measured between (the
    // closest points, the normal foot), as a CAD dimension is: drawing the two CLICKS instead put a
    // 4 mm figure on a line 90 mm long.
    const measured = this.measurement !== null && "from" in this.measurement ? this.measurement : null;
    const a = measured === null ? this.measureA : new THREE.Vector3(measured.from.x, measured.from.y, measured.from.z);
    const b = measured === null ? this.measureB : new THREE.Vector3(measured.to.x, measured.to.y, measured.to.z);
    if (a !== null && b !== null) {
      const line = edgeLines(new Float32Array([a.x, a.y, a.z, b.x, b.y, b.z]), this.measureLineMaterial, "measure:line");
      if (line !== null) {
        line.visible = true;
        line.renderOrder = MEASURE_ORDER;
        this.scene.add(line);
        this.measureLine = line;
      }
    }
    const ends: THREE.Vector3[] = [];
    if (a !== null) ends.push(a);
    if (b !== null) ends.push(b);
    if (ends.length === 0) return;
    const positions = new Float32Array(3 * ends.length);
    let at = 0;
    for (const point of ends) {
      positions[at] = point.x;
      positions[at + 1] = point.y;
      positions[at + 2] = point.z;
      at += 3;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const dots = new THREE.Points(geometry, this.measureDotMaterial);
    dots.name = "measure:ends";
    dots.renderOrder = MEASURE_ORDER + 1;
    // Two points have bounds, but a marker that vanishes because a stale bounding sphere said it was
    // off screen would be a bug nobody could explain, so it is never culled.
    dots.frustumCulled = false;
    this.scene.add(dots);
    this.measureDots = dots;
  }

  /** Takes the measurement's objects out of the scene and frees their geometries, not the materials. */
  private disposeMeasure(): void {
    if (this.measureLine !== null) {
      this.scene.remove(this.measureLine);
      this.measureLine.geometry.dispose();
      this.measureLine = null;
    }
    if (this.measureDots !== null) {
      this.scene.remove(this.measureDots);
      this.measureDots.geometry.dispose();
      this.measureDots = null;
    }
  }

  /**
   * The readout for the measurement, or null when there is not one, or its midpoint cannot be put
   * anywhere honest. The midpoint is projected with the camera in use and pulled inside the
   * viewport's edges, clear of the top strip the scale bar and the import line share; a point behind
   * the camera projects to a position that means nothing, so the label hides rather than cling to an
   * edge. Called from the frame that drew the scene, so the label follows an orbit.
   */
  /** What the readout's figure is, for the label's caption line. */
  private measureCaption(): string {
    if (this.intrinsic !== null) return this.intrinsic.caption;
    const relation = this.measurement !== null && "relation" in this.measurement
      ? (this.measurement as ElementMeasurement).relation
      : null;
    return relation === null ? "Distance" : RELATION_LABEL[relation];
  }

  private measureReadout(): MeasureReadout | null {
    // An intrinsic measurement has a point rather than a midpoint: it belongs at the element's own
    // centre, not halfway along a line to a second element there is not one of.
    const intrinsic = this.intrinsic;
    if (intrinsic !== null) {
      const projected = this.measureMid.set(intrinsic.at.x, intrinsic.at.y, intrinsic.at.z).project(this.rig.camera);
      if (!(projected.z >= -1 && projected.z <= 1)) return null;
      const at = this.clampLabel(projected);
      return { label: intrinsic.label, caption: intrinsic.caption, x: at.x, y: at.y };
    }
    const measurement = this.measurement;
    if (measurement === null) return null;
    const midpoint = measurement.midpoint;
    const projected = this.measureMid.set(midpoint.x, midpoint.y, midpoint.z).project(this.rig.camera);
    if (!(projected.z >= -1 && projected.z <= 1)) return null;
    const at = this.clampLabel(projected);
    // A distance says how the two elements related; an intrinsic says what was measured. Either way
    // the figure carries what it IS, not just the number.
    return { label: measurement.label, caption: this.measureCaption(), x: at.x, y: at.y };
  }

  /**
   * An NDC point as a CSS position inside the viewport, kept clear of the top strip.
   *
   * Shared by the intrinsic and the distance paths so the two cannot disagree about where a label is
   * allowed to sit — a second copy of these clamps is how one of them ends up under the scale bar.
   */
  private clampLabel(projected: { x: number; y: number }): { x: number; y: number } {
    const x = ((projected.x + 1) / 2) * this.width;
    const y = ((1 - projected.y) / 2) * this.height;
    return {
      x: THREE.MathUtils.clamp(x, MEASURE_LABEL_HALF, Math.max(MEASURE_LABEL_HALF, this.width - MEASURE_LABEL_HALF)),
      y: THREE.MathUtils.clamp(y, MEASURE_LABEL_TOP, Math.max(MEASURE_LABEL_TOP, this.height - MEASURE_LABEL_BOTTOM)),
    };
  }

  /**
   * Tells the store where the label belongs, when it has moved or changed. Called once per drawn
   * frame, like the selection anchor: a still view is not drawn at all, so this costs nothing while
   * nothing moves, and an orbit keeps the label on its line.
   */
  private reportMeasure(): void {
    const readout = this.measureReadout();
    const key = readout === null ? "" : `${readout.label}|${Math.round(readout.x * 2)}|${Math.round(readout.y * 2)}`;
    if (key === this.measureSent) return;
    this.measureSent = key;
    this.onMeasure(readout);
  }

  /** The client position of a pointer event, in the canvas's CSS pixels. */
  private marqueePoint(e: PointerEvent): { x: number; y: number } {
    const rect = this.canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  /** Extends the drag: a box keeps its two corners, a lasso appends the point it reached. */
  private extendMarquee(e: PointerEvent): void {
    const marquee = this.marquee;
    if (!marquee) return;
    const to = this.marqueePoint(e);
    marquee.to = to;
    if (marquee.shape !== "lasso") return;
    const n = marquee.points.length;
    const lastX = marquee.points[n - 2];
    const lastY = marquee.points[n - 1];
    // A path a pixel thick would be a path of a million points; a point every 2 px reads the same.
    if (n >= 2 && Math.hypot(to.x - lastX, to.y - lastY) < 2) return;
    marquee.points.push(to.x, to.y);
  }

  /** The id-space mask of a finished marquee, and the transport faces it names. */
  private marqueeItems(marquee: Marquee): BulkPickItem[] {
    const size = this.idPass.size;
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = size.width / Math.max(1, rect.width);
    const scaleY = size.height / Math.max(1, rect.height);
    const toId = (p: { x: number; y: number }): { x: number; y: number } => ({
      x: (p.x - rect.left) * scaleX,
      y: size.height - (p.y - rect.top) * scaleY,
    });
    const lasso: number[] = [];
    for (let i = 0; i + 1 < marquee.points.length; i += 2) {
      const at = toId({ x: marquee.points[i], y: marquee.points[i + 1] });
      lasso.push(at.x, at.y);
    }
    const from = toId(marquee.from);
    const to = toId(marquee.to);
    // A lasso reads its own bounds, which its path may take past the two ends of the drag.
    let x0 = Math.min(from.x, to.x);
    let x1 = Math.max(from.x, to.x);
    let y0 = Math.min(from.y, to.y);
    let y1 = Math.max(from.y, to.y);
    if (lasso.length >= 6) {
      for (let i = 0; i < lasso.length; i += 2) {
        if (i === 0) {
          x0 = x1 = lasso[0];
          y0 = y1 = lasso[1];
          continue;
        }
        x0 = Math.min(x0, lasso[i]);
        x1 = Math.max(x1, lasso[i]);
        y0 = Math.min(y0, lasso[i + 1]);
        y1 = Math.max(y1, lasso[i + 1]);
      }
    }
    const readX = Math.max(0, Math.floor(x0));
    const readY = Math.max(0, Math.floor(y0));
    const readW = Math.ceil(x1) - readX;
    const readH = Math.ceil(y1) - readY;
    if (readW <= 0 || readH <= 0) return [];
    const started = performance.now();
    this.renderIdPass();
    const pixels = this.idPass.read(this.renderer, readX, readY, readW, readH);
    this.lastIdReadMs = performance.now() - started;
    if (pixels.width <= 0 || pixels.height <= 0) return [];
    const inside =
      lasso.length >= 6
        ? polygonMask(lasso)
        : rectMask(readX, readY, readX + pixels.width, readY + pixels.height);
    const mask = scanIdPixels(pixels, inside);
    const items: BulkPickItem[] = [];
    if (this.pickMode === "face") {
      for (const [index, ordinals] of mask.faces) {
        const part = this.partById(index);
        if (part === null || part.faceTable.length === 0) continue;
        const faces: number[] = [];
        for (const ordinal of ordinals) {
          const face = this.faceOfOrdinal(part, ordinal);
          if (face >= 0) faces.push(face);
        }
        if (faces.length > 0) items.push({ part: part.source, faces });
      }
    } else {
      for (const index of mask.parts) {
        const part = this.partById(index);
        if (part !== null) items.push({ part: part.source });
      }
    }
    return items;
  }

  /** Applies a finished marquee through the store's one bulk path (T-032, ADR-0022 6.5). */
  private selectMarquee(marquee: Marquee, e: PointerEvent): void {
    const how = e.shiftKey ? "add" : e.ctrlKey || e.metaKey ? "remove" : "replace";
    const items = this.marqueeItems(marquee);
    // An empty box replaces the pick with nothing, as a click on empty space does; an empty Shift or
    // Ctrl drag leaves it alone.
    if (items.length === 0 && how !== "replace") return;
    this.onPickMany(items, how);
  }

  /** The overlay canvas the marquee is drawn on, made with the first drag and kept after. */
  private overlayCanvas(): HTMLCanvasElement | null {
    if (this.overlay !== null) return this.overlay;
    const host = this.canvas.parentElement;
    if (host === null) return null;
    const canvas = document.createElement("canvas");
    // Over the 3D canvas, out of the way of the pointer, sized by hand so it needs no stylesheet.
    canvas.style.position = "absolute";
    canvas.style.inset = "0";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.pointerEvents = "none";
    host.appendChild(canvas);
    this.overlay = canvas;
    this.sizeOverlay();
    return canvas;
  }

  private sizeOverlay(): void {
    const canvas = this.overlay;
    if (canvas === null) return;
    const rect = this.canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(rect.width * this.pixelRatio));
    canvas.height = Math.max(1, Math.floor(rect.height * this.pixelRatio));
  }

  /** Draws the drag: a dashed accent rectangle, or the dashed accent path of a lasso. */
  private drawMarquee(): void {
    const marquee = this.marquee;
    if (marquee === null) return;
    const canvas = this.overlayCanvas();
    const context = canvas?.getContext("2d") ?? null;
    if (context === null) return;
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, canvas?.width ?? 0, canvas?.height ?? 0);
    context.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0);
    const accent = `#${this.accent.getHexString()}`;
    context.lineWidth = 1;
    context.setLineDash([5, 4]);
    context.beginPath();
    if (marquee.shape === "lasso" && marquee.points.length >= 4) {
      context.moveTo(marquee.points[0], marquee.points[1]);
      for (let i = 2; i + 1 < marquee.points.length; i += 2) context.lineTo(marquee.points[i], marquee.points[i + 1]);
      context.closePath();
    } else {
      const x = Math.min(marquee.from.x, marquee.to.x);
      const y = Math.min(marquee.from.y, marquee.to.y);
      context.rect(x, y, Math.abs(marquee.to.x - marquee.from.x), Math.abs(marquee.to.y - marquee.from.y));
    }
    context.fillStyle = accent;
    context.globalAlpha = 0.08;
    context.fill();
    context.globalAlpha = 1;
    context.strokeStyle = accent;
    context.stroke();
  }

  /** Takes the drag overlay down: the next gesture draws a new one. */
  private clearOverlay(): void {
    const canvas = this.overlay;
    const context = canvas?.getContext("2d") ?? null;
    if (context === null || !canvas) return;
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, canvas.width, canvas.height);
  }

  // ------------------------------------------------------------------ the live pick
  /** Milliseconds the last pick-overlay rebuild took, and how many picks it drew (T-032 budget). */
  lastSelectionMs = 0;
  lastSelectionPicks = 0;
  /** How many times the pick overlay has been rebuilt; see the note in `updateSelection`. */
  selectionBuilds = 0;

  /** Rebuilds the pick overlay when the picks or their visibility changed. */
  private updateSelection(): void {
    const key = this.selectionSignature();
    if (key === this.selectionKey) return;
    this.selectionKey = key;
    // The overlay is rebuilt here, but a box release measures only the event handler: the store
    // update that follows reaches this through a React effect, usually in the NEXT frame. Counting
    // the calls makes that visible instead of letting a stale figure look like the release cost.
    this.selectionBuilds += 1;
    const started = performance.now();
    this.lastSelectionPicks = 0;
    const picks: FacePick[] = [];
    if (this.facePick.size > 0) {
      for (const [part, faces] of this.facePick) {
        const groups = this.sources.get(part);
        if (!groups) continue;
        for (const group of groups) {
          if (!group.shown || !group.faceIds) continue;
          picks.push({ source: { mesh: group.mesh, faceIds: group.faceIds, indices: group.indices }, faces });
        }
      }
    } else if (this.bodyPick.others.length > 0) {
      for (const name of this.bodyPick.others) {
        if (name === this.bodyPick.active) continue;
        const part = this.parts.get(name);
        if (!part?.shown || !part.faceIds) continue;
        picks.push({ source: { mesh: part.mesh, faceIds: part.faceIds, indices: part.indices }, faces: null });
      }
    }
    this.lastSelectionPicks = picks.length;
    if (this.selection.show(picks)) this.needsRender = true;
    this.lastSelectionMs = performance.now() - started;
  }

  /**
   * A signature of the drawn pick, so the same one is not rebuilt.
   *
   * A 32-bit rolling hash rather than the joined names: a wide box holds 3,624 body names, and
   * building and comparing that string cost a measurable share of the overlay rebuild. A collision
   * would only skip a rebuild that was not needed for the picture, and the same patch cannot be
   * added twice, so the hash is enough.
   */
  private selectionSignature(): string {
    if (this.facePick.size > 0) {
      let key = "f";
      for (const [part, faces] of this.facePick) key += `|${part}:${[...faces].join(",")}`;
      return key;
    }
    if (this.bodyPick.others.length > 0) {
      let hash = 2166136261;
      for (const name of this.bodyPick.others) {
        for (let i = 0; i < name.length; i++) {
          hash ^= name.charCodeAt(i);
          hash = Math.imul(hash, 16777619);
        }
        hash ^= 0x2c;
        hash = Math.imul(hash, 16777619);
      }
      return `b|${this.bodyPick.others.length}|${hash >>> 0}`;
    }
    return "";
  }

  /**
   * What the body pick's overlay would have to build, for the tooling that measures it: how many
   * bodies share one geometry, and the total triangles. `copy` runs once per pick, so a count of
   * distinct geometries well below the pick count is what makes an instanced overlay worth it.
   */
  selectionShape(): {
    picks: number;
    distinctGeometries: number;
    triangles: number;
    totalFaces: number;
  } {
    const geometries = new Set<unknown>();
    let triangles = 0;
    let totalFaces = 0;
    for (const name of this.bodyPick.others) {
      if (name === this.bodyPick.active) continue;
      const part = this.parts.get(name);
      if (!part?.shown || !part.faceIds) continue;
      geometries.add(part.mesh.geometry);
      totalFaces += part.faceIds.length;
      triangles += (part.mesh.geometry.getIndex()?.count ?? 0) / 3;
    }
    return {
      picks: this.bodyPick.others.length,
      distinctGeometries: geometries.size,
      triangles,
      totalFaces,
    };
  }

  // ------------------------------------------------------------------ sizes
  private fitFrame(): FitFrame {
    return {
      width: THREE.MathUtils.clamp(1 - (2 * FIT_RESERVE_X) / Math.max(1, this.width), 0.4, 0.92),
      height: THREE.MathUtils.clamp(1 - (2 * FIT_RESERVE_Y) / Math.max(1, this.height), 0.5, 0.92),
    };
  }

  private syncDrawingBuffer(): void {
    this.renderer.getDrawingBufferSize(this.drawingBuffer);
    this.outline.setSize(this.drawingBuffer.x, this.drawingBuffer.y, this.pixelRatio);
    this.grid.setPixelRatio(this.pixelRatio);
    this.idPass.setSize(this.drawingBuffer.x, this.drawingBuffer.y);
    this.sizeOverlay();
  }

  private watchPixelRatio(): void {
    this.pixelRatioQuery?.removeEventListener("change", this.onPixelRatioChange);
    this.pixelRatioQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    this.pixelRatioQuery.addEventListener("change", this.onPixelRatioChange, { once: true });
  }

  private onPixelRatioChange = (): void => {
    this.pixelRatio = Math.min(window.devicePixelRatio, 2);
    this.renderer.setPixelRatio(this.pixelRatio);
    this.setSize(this.width, this.height);
    this.watchPixelRatio();
  };

  // ------------------------------------------------------------------ environment
  private buildEnvironment(): void {
    this.environment?.dispose();
    const generator = new THREE.PMREMGenerator(this.renderer);
    const studio = createStudio();
    this.environment = generator.fromScene(studio, 0.02);
    disposeStudio(studio);
    generator.dispose();
    this.scene.environment = this.environment.texture;
  }

  private onContextLost = (event: Event): void => {
    event.preventDefault();
    this.contextLost = true;
  };

  private onContextRestored = (): void => {
    this.contextLost = false;
    this.buildEnvironment();
    this.shadow.invalidate();
    // The programs went with the context: the next flush compiles the looks ahead again.
    this.coolKinds();
    this.touch();
    this.needsRender = true;
  };

  // ------------------------------------------------------------------ input
  /** The pick mode the candidates and the hover text follow (ADR-0022 6.1, 6.3). */
  setPickMode(mode: PickMode): void {
    // What a measure click should prefer follows the pick mode, so "measure based on what is
    // selected" holds: Edge mode measures an edge, Point mode a vertex, Body mode the part's volume,
    // Face mode the area of the face pointed at.
    //
    // Set BEFORE the early return below. It used to be after, so calling `setPickMode` with the mode
    // the scene already had — the usual case, since React pushes the mode and the user then clicks
    // Measure — skipped the assignment entirely, and Body mode measured a face. "Is the mode
    // different" and "what should a click measure" are different questions, and the second must be
    // answered even when the first is no.
    this.measureWant =
      mode === "edge" ? "edge" : mode === "point" ? "vertex" : mode === "body" ? "body" : "face";
    if (mode === this.pickMode) return;
    this.pickMode = mode;
    this.resetCycle();
  }

  /**
   * Paint select on or off (T-043): the canvas shows a crosshair, and a left press paints instead of
   * orbiting. Leaving the mode ends a stroke still under the pointer, so the camera comes back.
   */
  setPaintMode(on: boolean): void {
    if (on === this.painting) return;
    this.painting = on;
    if (!on) this.endStroke();
    this.applyCursor();
  }

  /** The pointer's shape: a crosshair while painting or waiting for a point, a hand over a part, the arrow elsewhere. */
  private applyCursor(): void {
    this.canvas.style.cursor = this.painting || this.pointPick !== null ? "crosshair" : this.hovered === null ? "default" : "pointer";
  }

  /**
   * Ends the paint stroke: the camera takes the left button again (T-043). Called on pointerup, on
   * pointercancel and when the mode is left, so the controls can never be left disabled by a stroke.
   */
  private endStroke(): void {
    if (!this.stroke) return;
    this.stroke = false;
    this.strokeAt = null;
    this.strokeCtrl = false;
    this.rig.controls.enabled = true;
  }

  /**
   * Applies what is under the pointer to the paint stroke (T-043): the face in face mode, the body
   * in body mode. It rides the throttled hover pick, which has already spent the raycast, so this
   * adds no work of its own and none per face of a mesh. A pick on the same face — or the same body
   * — as the last one is dropped: a pointer moving inside one face must not touch the store again.
   * A mesh without face ids (the sample part) has no face to name in face mode, as it has none to
   * click (`pickFace` leaves a negative id alone).
   */
  private paintAt(hit: Hit): void {
    const face = this.pickMode === "face" ? faceIdOf(hit.part, hit.triangle) : -1;
    if (this.pickMode === "face" && face < 0) return;
    const part = hit.part.source;
    if (this.strokeAt !== null && this.strokeAt.part === part && this.strokeAt.face === face) return;
    this.strokeAt = { part, face };
    this.onPaint(this.publicHit(hit), this.strokeCtrl);
  }

  /**
   * The nearest triangle under a client position, or null over empty space or the view gizmo: the
   * first of `pickAll`, which is the nearest intersection. Times itself for the probe.
   */
  private pick(clientX: number, clientY: number): Hit | null {
    const started = performance.now();
    const hit = this.pickAll(clientX, clientY)[0] ?? null;
    this.lastPickMs = performance.now() - started;
    return hit;
  }

  /**
   * Everything under a client position, nearest first, reduced to distinct parts (body mode) or
   * distinct (part, face) pairs (face mode): the candidates `Alt` + wheel walks (ADR-0022 6.1).
   * The raycaster returns every intersection anyway, so this costs what one `pick` costs, and
   * nothing is done per face: the reduction is over the hits, not over the geometry.
   */
  private pickAll(clientX: number, clientY: number): Hit[] {
    if (this.gizmo.contains(clientX, clientY)) return [];
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.rig.camera);
    // Every shown part's own mesh, batched or not: they stay in step with what the batches draw.
    const candidates: THREE.Mesh[] = [];
    for (const part of this.parts.values()) if (part.shown) candidates.push(part.mesh);
    const hits: Hit[] = [];
    const seen = new Set<string>();
    // **The whole list obeys the cut, not just its first entry** (Claude's defect 7). `Alt`+wheel
    // walks this list, so a removed face left in it would be selectable after a wheel turn even though
    // the first click skipped it.
    const all = this.raycaster.intersectObjects(candidates, false);
    const epsilon = this.activeSection ? Math.max(this.sectionSpan() * 1e-5, 1e-9) : 0;
    for (const intersection of all) {
      // **Front faces only, so a click agrees with the id pass** (Claude's defect 4). The parts are
      // drawn with `side: THREE.DoubleSide` — an open shell would look hollow with `FrontSide` — and
      // three's raycast honours that by returning BOTH sides of every triangle (`Mesh.js`:
      // `intersectTriangle(..., material.side === FrontSide, ...)` culls back faces only for a
      // `FrontSide` material). The id pass, which box and lasso read, is `FrontSide` and so records
      // the near face alone. After a cut the two therefore disagreed: a click passed through the cut
      // face and took the far wall's INNER face, while a box over the same pixels took the near one.
      //
      // Filtered here rather than by changing the material, because the material's `side` is a
      // DRAWING decision and this is a PICKING one. `pickAll` is the one path a click, the `Alt`+wheel
      // cycle and the id-buffer fallback all share, so those three agree by construction.
      //
      // **Not the only raycast in this file, and the docstring used to say it was** — which is how
      // `pointAt` (Measure and the operation card's seed pick) kept the unfiltered behaviour until a
      // verification audit found it. Any new raycast against the part meshes needs this filter too.
      if (!isFrontFace(intersection, this.raycaster.ray.origin)) continue;
      if (
        this.activeSection &&
        !keptSide(
          { x: intersection.point.x, y: intersection.point.y, z: intersection.point.z },
          this.sectionPlane,
          epsilon,
        )
      ) {
        continue;
      }
      const part = this.partOf.get(intersection.object);
      if (!part) continue;
      const triangle = intersection.faceIndex ?? -1;
      const key = this.pickMode === "face" ? `${part.name}:${faceIdOf(part, triangle)}` : part.name;
      if (seen.has(key)) continue;
      seen.add(key);
      hits.push({ part, triangle });
    }
    return hits;
  }

  /** A hit as the store's pick: the source id, the patch name and the face id (ADR-0021 3.2). */
  private publicHit(hit: Hit | null): PickHit | null {
    if (!hit) return null;
    return { part: hit.part.source, patch: hit.part.name, face: faceIdOf(hit.part, hit.triangle) };
  }

  /**
   * The part whose cut face the ray meets, or null.
   *
   * The cap is a **plane crossing inside the model's bounds**, not a mesh the raycaster can hit: the
   * drawn quad is deliberately larger than the model, and the stencil that clips it back to the body is
   * a GPU state a CPU ray cannot consult. So the test is geometric and lives in `sectionPick.ts`, where
   * it is unit-tested.
   *
   * `face: -1` is the caller's "no face": a cap is not a face of any part, and inventing one would name
   * geometry the user did not click.
   */
  private capBodyAt(clientX: number, clientY: number): Part | null {
    if (!this.activeSection || !this.sectionCaps.showing) return null;
    const rect = this.canvas.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(pointer, this.rig.camera);
    const ray = this.raycaster.ray;
    const bounds = this.sceneBounds();
    if (bounds.isEmpty()) return null;
    const epsilon = Math.max(this.sectionSpan() * 1e-5, 1e-9);
    const hits = hitsCap(
      { x: ray.origin.x, y: ray.origin.y, z: ray.origin.z },
      { x: ray.direction.x, y: ray.direction.y, z: ray.direction.z },
      this.sectionPlane,
      { min: { x: bounds.min.x, y: bounds.min.y, z: bounds.min.z }, max: { x: bounds.max.x, y: bounds.max.y, z: bounds.max.z } },
      epsilon,
    );
    if (!hits) return null;
    // **Which body: the box the RAY ENTERS, not the box centre nearest the crossing** (Claude's defect
    // 5). Measuring to the nearest centre is wrong in three ways he named, and each is a real click the
    // user can make:
    //
    //  * a large part whose centre is far from the crossing loses to a small part whose centre happens
    //    to be near it, even when the ray passes through the first and nowhere near the second;
    //  * a ray that crosses the plane inside the OVERALL bounds but inside no part's own box still
    //    selected a body — clicking a gap in an assembly picked whichever part was closest to the gap;
    //  * a batched part has no per-part box in `this.parts`, so it was skipped entirely and a click on
    //    its cap picked some other body or nothing.
    //
    // `Ray.intersectBox` answers the question actually being asked: where the ray enters each part's
    // own box. The nearest entry is the body in front, which is what the user is looking at, and no
    // entry at all means the ray met only the model's empty space — so the answer is no body.
    let best: Part | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const part of this.parts.values()) {
      // A batched part IS pickable as a body even though it has no per-part mesh; `shown` and a box
      // are the only requirements. (Skipping `batched !== null` here was the third of the defects.)
      if (!part.shown || part.box.isEmpty()) continue;
      const entry = new THREE.Vector3();
      if (ray.intersectBox(part.box, entry) === null) continue;
      const distance = entry.distanceTo(ray.origin);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = part;
      }
    }
    return best;
  }

  // ------------------------------------------------------------------ the candidates under the pointer
  /** Forgets the cycle: the next wheel starts again at the nearest candidate. */
  private resetCycle(): void {
    this.cycle = 0;
    this.cycleAt = null;
    this.cycleHits = [];
  }

  /** The candidate the cycle stands on, or null while there is no cycle or the pointer has left it. */
  private cycleHit(clientX: number, clientY: number): Hit | null {
    if (this.cycleAt === null || this.cycleHits.length === 0) return null;
    if (Math.hypot(clientX - this.cycleAt.x, clientY - this.cycleAt.y) > POINTER_SLOP) return null;
    return this.cycleHits[this.cycle % this.cycleHits.length] ?? null;
  }

  /** Draws the candidate the cycle stands on and says what the pointer is over. */
  private showCandidate(): void {
    const index = this.cycleHits.length === 0 ? 0 : this.cycle % this.cycleHits.length;
    const hit = this.cycleHits[index] ?? null;
    this.setHover(hit ? hit.part.name : null);
    this.setFaceHover(hit);
    this.reportHover(hit, index, this.cycleHits.length);
    if (hit && this.stroke) this.paintAt(hit);
  }

  /** Tells the store what the pointer is over, once per change (ADR-0022 6.1, 11.2); T-049 draws it. */
  private reportHover(hit: Hit | null, index: number, count: number): void {
    const next: HoverInfo | null = hit === null ? null : { label: this.hoverLabel(hit), index, count };
    const last = this.lastHoverInfo;
    if (next === null ? last === null : last !== null && last.label === next.label && last.index === next.index && last.count === next.count) return;
    this.lastHoverInfo = next;
    this.onHoverInfo(next);
  }

  /** The pointer's text: the patch, or "face 12 of part_3" in face mode (ADR-0022 11.2). */
  private hoverLabel(hit: Hit): string {
    const face = faceIdOf(hit.part, hit.triangle);
    return this.pickMode === "face" && face >= 0 ? `face ${face} of ${hit.part.name}` : hit.part.name;
  }

  /**
   * `Alt` + wheel walks the candidates under the pointer, and the wheel is taken away from the
   * camera so it never zooms (ADR-0022 6.1). A wheel without `Alt` is left entirely alone.
   */
  private onWheel = (e: WheelEvent): void => {
    if (!e.altKey) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const at = this.pointerAt ?? { x: e.clientX, y: e.clientY };
    const step = e.deltaY < 0 ? -1 : 1;
    // **Edge and Point mode cycle their own candidates** (T-058/T-060). The geometry pass is shared
    // with the click (`topologyCandidates`), so the wheel walks exactly what a click would take the
    // nearest of. `cycleCandidate` (T-057) is the step arithmetic, and this is its caller — it was
    // written and tested with nothing calling it, which is the "module + test is not a feature"
    // lesson in this repository's own notes.
    if (this.topologyMode !== null) {
      // **Canvas-local, because that is what `topologyCandidates` projects against.** `pointerAt` (and
      // the face cycle, which compares it straight to a client position) are PAGE coordinates: the two
      // differ by the canvas's offset, and passing page coords here found no candidates at all, so the
      // wheel moved a choice that a click then could not resolve. `canvasCssPoint` is the app's own
      // conversion and is used by the click and the hover for the same reason.
      const local = canvasCssPoint(at.x, at.y, this.canvas.getBoundingClientRect());
      if (
        this.topologyCycleAt === null ||
        Math.hypot(local.x - this.topologyCycleAt.x, local.y - this.topologyCycleAt.y) > POINTER_SLOP
      ) {
        // A new spot: the candidates are found again and the wheel starts from the nearest, which is
        // what `cycleCandidate` does with a `current` it cannot find in the list.
        this.topologyCycle = this.topologyCandidates(local.x, local.y);
        this.topologyCycleAt = local;
        this.topologyPicked = null;
      }
      const currentTopology = this.topologyCycle.find(
        (c) => c.id === this.topologyPicked?.id && c.kind === this.topologyPicked?.kind,
      ) ?? null;
      this.topologyPicked = cycleCandidate(this.topologyCycle, currentTopology ?? null, step);
      // **The candidate is shown as the HOVER**, through the scene's own overlay, so the user sees
      // what the wheel is standing on before clicking. A later real pointer move re-runs the hover
      // and clears it, which is correct: the wheel's choice lasts until the pointer says otherwise.
      this.onTopologyHover(this.topologyPicked);
      const one = this.topologyPicked;
      this.setTopologyPick(
        one === null
          ? {}
          : one.kind === "edge"
            ? { edges: [{ part: one.part, id: one.id }] }
            : { vertices: [{ part: one.part, id: one.id }] },
        {},
      );
      return;
    }
    if (this.cycleAt === null || Math.hypot(at.x - this.cycleAt.x, at.y - this.cycleAt.y) > POINTER_SLOP) {
      // A new spot: the candidates are found again, and the cycle starts at the nearest.
      this.cycleHits = this.pickAll(at.x, at.y);
      this.cycle = 0;
      this.cycleAt = at;
    }
    const count = this.cycleHits.length;
    this.cycle = count === 0 ? 0 : (this.cycle + step + count) % count;
    this.showCandidate();
  };

  private updateHover(clientX: number, clientY: number): void {
    // Inside a cycle the candidates of the wheel are still the ones under the pointer: showing the
    // chosen one costs no raycast at all, and a pointer that sits still keeps it on screen.
    if (this.cycleHit(clientX, clientY) !== null) {
      this.showCandidate();
      return;
    }
    const started = performance.now();
    const hits = this.pickAll(clientX, clientY);
    this.lastPickMs = performance.now() - started;
    const hit = hits[0] ?? null;

    // A5: in a topology mode the hover that matters is the edge or vertex, which the mesh raycast
    // cannot see. It is asked for with the device-pixel pointer, the same space the hit test uses.
    if (this.topologyMode !== null) {
      // A5: `topologyAt` takes **canvas-relative CSS pixels** and multiplies by the device pixel
      // ratio itself (see its own comment: "the conversion happens in exactly one place"). This call
      // used to multiply by `getPixelRatio()` first, so on any scaled display — this laptop is 1.25 —
      // the hover sampled 1.25x too far from the origin and simply missed the edge under the pointer.
      // The click path above resolves it the same way, with `clientX - rect.left`.
      const rect = this.renderer.domElement.getBoundingClientRect();
      const point = canvasCssPoint(clientX, clientY, rect);
      const found = this.topologyAt(point.x, point.y, this.topologyMode);
      this.onTopologyHover(
        found === null ? null : { part: found.part, kind: found.kind === "vertex" ? "vertex" : "edge", id: found.id },
      );
    } else {
      this.onTopologyHover(null);
    }

    this.setHover(hit ? hit.part.name : null);
    this.setFaceHover(hit);
    this.reportHover(hit, 0, hits.length);
    // A paint stroke takes every new face (or body) this hover pick lands on (T-043).
    if (hit && this.stroke) this.paintAt(hit);
  }

  /** Picks at the last pointer position. A move that fell inside the interval is picked when it runs out. */
  private checkHover = (): void => {
    this.hoverTimer = 0;
    this.lastHoverCheck = performance.now();
    const at = this.pointerAt;
    // While the left button is down the pointer is dragging the camera, not hovering — except in a
    // paint stroke, which is exactly the drag that never moves the camera (T-043).
    if (!at || (this.downAt && !this.stroke)) return;
    this.updateHover(at.x, at.y);
  };

  private onPointerMove = (e: PointerEvent): void => {
    this.pointerAt = { x: e.clientX, y: e.clientY };
    if (this.handleDrag !== null) {
      this.dragSectionHandle(e);
      return;
    }
    // Ctrl at the move decides whether the stroke adds or removes (T-043); the throttled pick runs later.
    this.strokeCtrl = e.ctrlKey || e.metaKey;
    // A drag that started on empty space draws its box or lasso and touches nothing else (T-032):
    // no hover pick, no candidate cycle, no camera — the controls are off for the length of the drag.
    if (this.marqueeArmed) {
      if (this.marquee === null) {
        const from = this.marqueePoint(e);
        this.marquee = { from, to: from, shape: this.marqueeShape, points: this.marqueeShape === "lasso" ? [from.x, from.y] : [] };
      }
      this.extendMarquee(e);
      // Under the slop the press is still a click, and a click draws nothing.
      const down = this.downAt;
      if (down !== null && Math.hypot(e.clientX - down.x, e.clientY - down.y) > POINTER_SLOP) this.drawMarquee();
      return;
    }
    // The pointer left the spot the cycle was started at: it goes back to the nearest candidate.
    if (this.cycleAt !== null && Math.hypot(e.clientX - this.cycleAt.x, e.clientY - this.cycleAt.y) > POINTER_SLOP) this.resetCycle();
    // The topology cycle follows the same rule, so a wheel choice cannot outlive the pointer leaving.
    // **Converted, not compared raw**: `topologyCycleAt` is canvas-local and `e.clientX` is a page
    // coordinate, so the raw comparison measured the canvas's own offset as pointer movement and
    // dropped the choice on the very next move — which is what made a click after the wheel pick
    // nothing. `cycleAt` above is page-space, so it stays as it is.
    if (this.topologyCycleAt !== null) {
      const here = canvasCssPoint(e.clientX, e.clientY, this.canvas.getBoundingClientRect());
      if (Math.hypot(here.x - this.topologyCycleAt.x, here.y - this.topologyCycleAt.y) > POINTER_SLOP) {
        this.topologyCycleAt = null;
        this.topologyPicked = null;
      }
    }
    const wait = HOVER_INTERVAL - (performance.now() - this.lastHoverCheck);
    if (wait <= 0) {
      window.clearTimeout(this.hoverTimer);
      this.checkHover();
    } else if (this.hoverTimer === 0) {
      this.hoverTimer = window.setTimeout(this.checkHover, wait);
    }
  };

  private onPointerDown = (e: PointerEvent): void => {
    this.downAt = { x: e.clientX, y: e.clientY };
    if (e.button !== 0) return;
    // The section's handle first: a press on it slides the cut, and nothing else may see this pointer.
    // Checked before paint and before orbit so the handle wins wherever it is — it is drawn over the
    // model, so a press that reaches it must never also start a camera drag underneath.
    // **A point pick comes before the section's handle** (R5). Measured on pipe3 looking along the
    // pipe at the mid-plane disc: the handle's generous pick sphere covered the whole disc from
    // that direction, so every press of the line pick began a section drag and its `pointerup`
    // ended one, and the two clicks never answered. The pick is one-shot and the user asked for a
    // point, so the handle waits for the next press.
    if (this.pointPick === null && this.beginSectionDrag(e)) return;
    if (this.painting) {
      // The stroke owns the left button: the camera is held still until pointerup, and a press on
      // empty space paints nothing and orbits nothing either (T-043). OrbitControls has already seen
      // this pointerdown; its move handler ignores an event while `enabled` is false, so no rotation
      // reaches the camera, and it has taken the pointer capture that brings the pointerup here.
      this.stroke = true;
      this.strokeAt = null;
      this.strokeCtrl = e.ctrlKey || e.metaKey;
      this.rig.controls.enabled = false;
      // The first face is applied at once: a stroke that never moves still paints what it started on.
      const hit = this.cycleHit(e.clientX, e.clientY) ?? this.pick(e.clientX, e.clientY);
      if (hit) this.paintAt(hit);
      return;
    }
    // ADR-0021 decision 3.4, amended 2026-10-06: what a LEFT drag means depends on the mouse
    // scheme, because the left button is also select and the marquee, and rotation must stay
    // reachable in every scheme (the middle button rotates in all of them).
    //
    // - cad, blender: a press on a patch orbits, a drag on empty space draws the band. `onPatch`
    //   also covers the Alt+wheel candidate: a press on the face the wheel stands on is a press on
    //   a patch, so it orbits rather than starting a box.
    // - paraview: a plain left drag orbits anywhere, so the band needs Shift; Alt, or the island's
    //   toggle, is the lasso. Shift still means "add" when the band is drawn.
    // - toggle: `orbitMode` decides, and the middle and right buttons are unchanged.
    //
    // The controls go off here, before any move: OrbitControls has already seen this pointerdown
    // and ignores every move while it is disabled.
    const onPatch = this.cycleHit(e.clientX, e.clientY) !== null || this.pick(e.clientX, e.clientY) !== null;
    const orbits = leftPressOrbits({ orbitOnly: this.orbitOnly, navMode: this.navMode, orbitMode: this.orbitMode, lassoMode: this.lassoMode, shift: e.shiftKey, alt: e.altKey, onPatch });
    if (orbits) return;
    // A topology mode selects edges and vertices, not faces, so the box and the lasso are not armed
    // there: the press is a click, and `pointerup` resolves it through the topology pick.
    if (this.topologyMode !== null) {
      this.marqueeArmed = false;
      this.marquee = null;
      this.rig.controls.enabled = true;
      return;
    }
    // **A point pick takes a press on empty space as a click, not as the start of a box** (R5).
    // Measured on pipe3: the line pick on the mid-plane disc met no part, the press armed the
    // marquee, and the marquee's `pointerup` returned before `answerPoint` could try the slice's
    // plane - so the two clicks never answered. A drag still orbits, as in the topology modes.
    if (this.pointPick !== null) {
      this.marqueeArmed = false;
      this.marquee = null;
      this.rig.controls.enabled = true;
      return;
    }
    this.marqueeArmed = true;
    this.marqueeShape = e.altKey || this.lassoMode ? "lasso" : "box";
    this.marquee = null;
    this.rig.controls.enabled = false;
    this.resetCycle();
    this.setHover(null);
    this.clearFaceHover();
  };

  private onPointerCancel = (): void => {
    this.downAt = null;
    this.endStroke();
    this.endMarquee();
  };

  private onPointerLeave = (): void => {
    window.clearTimeout(this.hoverTimer);
    this.hoverTimer = 0;
    this.pointerAt = null;
    this.resetCycle();
    this.setHover(null);
    this.clearFaceHover();
    this.reportHover(null, 0, 0);
    // A5: the topology hover too. Leaving the canvas used to leave the last edge or vertex
    // highlighted, so in Edge mode a marker stayed lit under a pointer that was no longer there.
    this.onTopologyHover(null);
  };

  /** Ends the marquee without applying it: the camera takes the left button again (T-032). */
  private endMarquee(): void {
    if (!this.marqueeArmed) return;
    this.marqueeArmed = false;
    this.marquee = null;
    this.rig.controls.enabled = true;
    this.clearOverlay();
  }

  /**
   * Resolve a click to a B-rep edge or vertex, in screen space (ADR-0022 §15.3, T-058).
   *
   * The candidates are the loaded topology records of the parts that are visible, projected to
   * **device** pixels through the same view-projection matrix the renderer just used; the pointer is
   * converted to device pixels too, and the tolerance is doubled for touch-size comfort but stays a
   * device-pixel figure so HiDPI is honest. `hitEdges`/`hitVertices` (T-057) do the arithmetic and
   * return nearest-first, and the first candidate is taken: Alt + wheel cycling reuses the same list
   * when it is wired (T-057's `cycleCandidate`).
   *
   * Returns null when the mode is a face/body one, when nothing is loaded, or when nothing is within
   * the tolerance. A part with no topology record is simply not a candidate — it is still loading, or
   * it has no B-rep — which is why this never falls back to picking the face behind.
   */
  /**
   * Every edge or vertex within the tolerance, **nearest first**.
   *
   * Returning the whole list rather than only the nearest is what lets `Alt` + wheel walk the
   * candidates (ADR-0022 6.1): the same geometry pass answers both a click (take the first) and a
   * cycle (step through them), so there is one definition of what is under the pointer. `topologyAt`
   * is the click's view of this.
   */
  private topologyCandidates(
    localX: number,
    localY: number,
    mode: "edge" | "vertex" | null = this.topologyMode,
  ): { part: string; kind: "edge" | "vertex"; id: number }[] {
    if (mode === null || this.topologies.size === 0) return [];
    const ratio = this.pixelRatio;
    const width = this.drawingBuffer.x;
    const height = this.drawingBuffer.y;
    // **There is no occlusion test, and that is the owner's decision (2026-10-12).** ADR-0022 §15.3
    // is unchanged: the nearest candidate within the tolerance wins, with ties broken by depth, so a
    // BACK edge stays pickable. An earlier version hid candidates behind the nearest opaque surface;
    // the owner declined the amendment that would have made that correct and asked for the code to be
    // removed rather than left switched off, because dead code with tests is what his own audit
    // lesson warns about. The two filters that DO apply are about what the user chose to hide — the
    // part's `shown` flag and the tag-hidden-face rule below — and they are not about occlusion.
    const matrix = this.camera.projectionMatrix.clone().multiply(this.camera.matrixWorldInverse);
    // The cut, for this pick: a candidate on the removed side is not a candidate (defect 7). Computed
    // once per pick rather than per candidate, and `sectionOn` is false when no section is shown, so
    // the whole test costs one boolean in the ordinary case.
    const sectionOn = this.activeSection;
    const sectionEpsilon = sectionOn ? Math.max(this.sectionSpan() * 1e-5, 1e-9) : 0;
    // Canvas-relative CSS pixels in, device pixels out; the caller subtracts the canvas rect, so the
    // conversion happens in exactly one place and the two callers cannot disagree about the origin.
    const pointer = { x: localX * ratio, y: localY * ratio, depth: 0 };

    if (mode === "edge") {
      const candidates: EdgeCandidate[] = [];
      for (const [part, topology] of this.topologies) {
        // A source can draw several patches (a multi-solid part); every drawn one is a candidate.
        // A2: only a part that is actually DRAWN may be picked. §15.3: "Hidden/tag-hidden faces
        // must not make a hidden edge or point pickable". The raycaster filters the same way
        // (`if (part.shown)`), so the two paths agree about what is on screen.
        for (const entry of this.partsOfSource(part)) {
        const mesh = entry.mesh;
        const world = mesh.matrixWorld.clone().premultiply(matrix);
        // Per-patch, not per-edge: `hiddenFacesOf` builds a Set, and calling it inside the edge loop
        // rebuilt the same Set once for every edge of the part.
        const hidden = this.hiddenFacesOf(part);
        for (const edge of topology.edges) {
          if (!edge.pickable || edge.pointsCount < 2) continue;
          // A1: runs, so an edge that leaves and re-enters the frustum is two visible pieces, not
          // one piece with an invented segment joining them.
          const { runs, visible } = projectPolyline(edge.points, edge.pointsCount, world.elements, width, height);
          if (!visible) continue;
          // **An edge the cut removed is not a candidate** (Claude's defect 7). The screen-space test
          // would otherwise find it, because a clipped-away edge still projects: the projection knows
          // nothing about the section. This is clipping by the user's own cut, not the occlusion the
          // owner had removed — ADR-0022 §15.3 is unchanged. The edge's OWN vertices are tested, in
          // world space, against the same plane the renderer clips by.
          if (sectionOn && !this.edgeKept(edge.points, edge.pointsCount, mesh.matrixWorld.elements))
            continue;
          // A2 (T-124a): an edge whose EVERY bordering face is hidden is hidden. One visible face
          // keeps it pickable — it is on that face's boundary.
          if (hiddenByFaces(edge.faces, hidden)) continue;
          candidates.push({ part, id: edge.id, runs });
        }
        }
      }
      return hitEdges(pointer, candidates, EDGE_PICK_TOLERANCE_PX * ratio).map((hit) => ({
        part: hit.candidate.part,
        kind: "edge" as const,
        id: hit.candidate.id,
      }));
    }

    const candidates: VertexCandidate[] = [];
    for (const [part, topology] of this.topologies) {
      // Every drawn patch of the source, as for edges: `this.parts` is keyed by patch name, and the
      // record's key is the source id, so a direct lookup never matched.
      for (const entry of this.partsOfSource(part)) {
      const mesh = entry.mesh;
      // **Both of these are per-PATCH, not per-vertex** (Claude's defect 6: "a matrix clone per
      // vertex, now repeated per patch"). This used to build a fresh NDC matrix for every vertex of
      // every patch, and re-find each bordering edge with `edges.find` inside the vertex loop — a
      // linear scan of the whole edge list, once per edge at every vertex. On the cold plate that is
      // tens of thousands of allocations and comparisons per pointer move.
      //
      // `edge.id` IS its index in `topology.edges` (`topology.ts`: `id: index`), which the draw path
      // already relies on (`scene.ts` uses `topology.edges[found.id]`). So the lookup is direct.
      const world = mesh.matrixWorld.clone().premultiply(matrix).elements;
      const hidden = this.hiddenFacesOf(part);
      const hidesFaces = hidden !== undefined && hidden.size > 0;
      for (const vertex of topology.vertices) {
        const p = projectPoint(
          { x: vertex.position[0], y: vertex.position[1], z: vertex.position[2] },
          world,
          width,
          height,
        );
        if (!p.visible) continue;
        // **A vertex the cut removed is not a candidate** (Claude's defect 7), for the same reason as
        // an edge: it still projects, and the pick must not reach through the cut to it.
        if (
          sectionOn &&
          !keptSide(
            worldPoint(
              { x: vertex.position[0], y: vertex.position[1], z: vertex.position[2] },
              mesh.matrixWorld.elements,
            ),
            this.sectionPlane,
            sectionEpsilon,
          )
        ) {
          continue;
        }
        // A vertex is hidden when every face around it is hidden (the edges at it, unioned).
        if (hidesFaces && hidden !== undefined) {
          const around = new Set<number>();
          for (const edgeId of vertex.edges) {
            const edge = topology.edges[edgeId];
            if (edge !== undefined) for (const face of edge.faces) around.add(face);
          }
          if (around.size > 0 && hiddenByFaces([...around], hidden)) continue;
        }
        candidates.push({ part, id: vertex.id, point: { x: p.x, y: p.y, depth: p.depth } });
      }
      }
    }
    return hitVertices(pointer, candidates, EDGE_PICK_TOLERANCE_PX * ratio).map((hit) => ({
      part: hit.candidate.part,
      kind: "vertex" as const,
      id: hit.candidate.id,
    }));
  }

  /** The nearest edge or vertex under a point, or null: the click's view of the candidates. */
  private topologyAt(
    localX: number,
    localY: number,
    mode: "edge" | "vertex" | null = this.topologyMode,
  ): { part: string; kind: "edge" | "vertex"; id: number } | null {
    return this.topologyCandidates(localX, localY, mode)[0] ?? null;
  }

  private onPointerUp = (e: PointerEvent): void => {
    const down = this.downAt;
    this.downAt = null;
    // A section drag is not a click either: the cut moved as the pointer moved, and the store already
    // has the offset. The camera comes back with the button, as it does after a stroke.
    if (this.endSectionDrag()) return;
    // A paint stroke is not a click: what it painted is already in the store, and the camera comes
    // back with the button (T-043).
    if (this.stroke) {
      this.endStroke();
      return;
    }
    // **Edge and Point mode own the click; the box does not run there** (T-058). A press in those
    // modes still armed the marquee below, and this branch RETURNS, so `pointerup` never reached the
    // topology pick: a click in Edge mode selected nothing at all, and the `Alt` + wheel choice it had
    // just made was never consumed. Measured: with the wheel standing on edge id 15 and a press and
    // release at that same spot, `picked.edges` came back `{}` while the choice was still standing.
    // The marquee is a face-picking gesture (T-032), so it is left off in the topology modes.
    if (this.marqueeArmed && this.topologyMode === null) {
      const marquee = this.marquee;
      this.endMarquee();
      const dragged = down !== null && Math.hypot(e.clientX - down.x, e.clientY - down.y) > POINTER_SLOP;
      if (dragged && marquee !== null && e.button === 0) this.selectMarquee(marquee, e);
      return;
    }
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > POINTER_SLOP) return;
    if (e.button !== 0) return;
    // The view gizmo owns its corner: a click there never reaches the patches behind it.
    if (this.gizmo.contains(e.clientX, e.clientY)) {
      const dir = this.gizmo.pickDirection(e, this.rig.camera);
      if (dir) this.rig.fit(dir);
      return;
    }
    // A point pick owns the click it is waiting for (T-029): it answers with the face it met and
    // picks nothing. A click that meets nothing leaves it waiting, and a drag never arrives here.
    if (this.pointPick !== null) {
      this.answerPoint(e.clientX, e.clientY);
      return;
    }
    // Edge and Point mode resolve the click in screen space over the loaded topology records, before
    // the face raycast (ADR-0022 §15.3). A click that meets no edge or vertex within the tolerance
    // picks nothing rather than falling through to the face behind it: in Edge mode the user asked
    // for edges, and silently picking a face would be the wrong answer.
    const local = this.canvas.getBoundingClientRect();
    const at = canvasCssPoint(e.clientX, e.clientY, local);
    // **A click takes what the wheel stands on** (ADR-0022 6.1: "a click takes the candidate
    // `Alt` + wheel stands on, else the nearest one"). The topology cycle holds that choice, and it
    // is only valid while the pointer has not moved off the spot it was gathered at — the same rule
    // the face cycle uses, so the two behave alike.
    const standing =
      this.topologyPicked !== null &&
      this.topologyCycleAt !== null &&
      Math.hypot(at.x - this.topologyCycleAt.x, at.y - this.topologyCycleAt.y) <= POINTER_SLOP
        ? this.topologyPicked
        : null;
    // **The run of clicks is counted ONCE, here, above every branch.** It used to be counted only on
    // the face/body path, which is BELOW the Edge/Point branch — and that branch `return`s, so in Edge
    // or Point mode `this.clickCount` was never incremented and every click read a stale value. The
    // double-click's wire and the triple-click's body therefore never fired: measured with the wheel
    // standing on edge 23 (wire `[23, 19]`), two presses 120 ms apart both selected `[23]`.
    //
    // Hoisting it fixes the whole class rather than the symptom: any branch added below now sees the
    // count, and none can forget to compute it.
    const now = performance.now();
    const near =
      now - this.clickAt <= CLICK_INTERVAL &&
      Math.hypot(e.clientX - this.clickFrom.x, e.clientY - this.clickFrom.y) <= POINTER_SLOP;
    // The second counts 2, the third 3, and the one after starts again at 1.
    this.clickCount = near && this.clickCount < 3 ? this.clickCount + 1 : 1;
    this.clickAt = now;
    this.clickFrom = { x: e.clientX, y: e.clientY };

    const topologyHit = standing ?? (this.topologyMode === null ? null : this.topologyAt(at.x, at.y));
    if (topologyHit !== null) {
      this.topologyPicked = null;
      this.onTopologyPick(topologyHit.part, topologyHit.kind, topologyHit.id, {
        shift: e.shiftKey,
        ctrl: e.ctrlKey || e.metaKey,
        clicks: this.clickCount,
      });
      return;
    }
    // A click takes the candidate `Alt` + wheel stands on, else the nearest one (ADR-0022 6.1), which
    // the id buffer answers when the probe has switched it on (T-032).
    const hit = this.cycleHit(e.clientX, e.clientY) ?? (this.idPickFromBuffer ? this.idHit(e.clientX, e.clientY) : this.pick(e.clientX, e.clientY));
    // **A click on the cut face selects the BODY whose cap it is** (Claude's defect 7b). The cap has no
    // face id — it is a stencil-filled quad, not a face of any part — so there is nothing to name but
    // the body, which is what the user is looking at. Checked only when the ordinary pick found nothing
    // on the kept side: `pick` already returns the geometry BEHIND the cut, and the cap lies behind
    // none of it, so a cap can never be picked through a body in front of it.
    if (hit === null) {
      const capped = this.capBodyAt(e.clientX, e.clientY);
      if (capped !== null) {
        this.clickCount = 1;
        this.resetCycle();
        this.onPick({ part: capped.source, patch: capped.name, face: -1 }, {
          shift: e.shiftKey,
          ctrl: e.ctrlKey || e.metaKey,
          clicks: 1,
        });
        return;
      }
    }
    this.resetCycle();
    this.onPick(this.publicHit(hit), { shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey, clicks: this.clickCount });
  };

  /**
   * The hit under a client position through the id buffer (T-032), as a `Hit`: the patch the pixel
   * names, and the triangle that draws the face it names, so everything downstream (the face tint,
   * the hover text, the click count) takes the same shape it takes from the raycaster.
   */
  private idHit(clientX: number, clientY: number): Hit | null {
    const picked = this.idPickAt(clientX, clientY);
    if (picked === null) return null;
    const part = this.parts.get(picked.patch) ?? null;
    if (part === null) return null;
    const triangle = picked.face < 0 || part.faceIds === null ? -1 : part.faceIds.indexOf(picked.face);
    return { part, triangle };
  }

  // ------------------------------------------------------------------ frame loop
  private animate = (timestamp: number): void => {
    this.frame = requestAnimationFrame(this.animate);
    this.timer.update(timestamp);
    // A stalled frame must not swallow part of an animation.
    const dt = Math.min(this.timer.getDelta(), 0.05);
    if (this.contentDirty && !this.contextLost && performance.now() - this.lastFlush >= this.flushGap()) this.flushContent();
    if (this.glassDue !== 0 && !this.contentDirty && !this.contextLost && performance.now() >= this.glassDue) this.warmGlass();
    if (this.pickSelectDue !== 0 && !this.contentDirty && !this.contextLost && performance.now() >= this.pickSelectDue) this.warmPickSelect();
    const moved = this.rig.update(dt) || this.viewChanged;
    this.viewChanged = false;
    this.viewStill = !moved;
    if (moved) {
      this.needsRender = true;
      this.sortedView = false;
      // The id buffer holds a picture of the view: a moved camera makes it one to draw again (T-032).
      this.idDirty = true;
    }
    if (this.stepHighlights(dt)) this.needsRender = true;
    if (this.stepEdges()) this.needsRender = true;
    // The view has come to rest on lists sorted for an earlier one: one more frame sorts them for it.
    if (this.viewStill && this.batched && !this.sortedView) this.needsRender = true;
    if (!this.needsRender || this.contextLost) return;
    this.needsRender = false;
    this.renderFrame();
  };

  /**
   * Counters for the measurement hook. `frameCostMs` is an exponential average of the time actually
   * spent inside a frame, so it reports render work rather than the display's refresh interval: a
   * display that caps at 30 Hz makes consecutive animation frames 33.3 ms apart however little work
   * there is, which hides both headroom and cost.
   */
  perf(): SceneStats {
    const info = this.renderer.info;
    let raised = 0;
    let tagTriangles = 0;
    let hiddenTriangles = 0;
    let unlitParts = 0;
    for (const part of this.parts.values()) {
      if (part.raised) raised++;
      if (part.unlit) unlitParts++;
      tagTriangles += part.tinted;
      if (part.indices !== part.group.indices) hiddenTriangles += (part.group.indices.length - part.indices.length) / 3;
    }
    return {
      frameCostMs: this.frameCost,
      parts: this.parts.size,
      sources: this.sources.size,
      batched: this.batched ? 1 : 0,
      batches: this.batchPool.batchCount,
      translucentBatches: this.batchPool.translucentCount,
      batchInstances: this.batchPool.instanceCount,
      raised,
      drawCalls: info.render.calls,
      triangles: info.render.triangles,
      tagTriangles,
      unlitParts,
      hiddenTriangles,
      geometries: info.memory.geometries,
      textures: info.memory.textures,
      width: this.width,
      height: this.height,
      pixelRatio: this.renderer.getPixelRatio(),
    };
  }

  /**
   * Draws `frames` forced frames, one per animation frame so the page stays responsive between them,
   * and times each (see `timings`). The view does not move, so this is the cost of a still frame.
   */
  async measureRender(frames = 30): Promise<FrameTiming> {
    const record: FrameRecord = { cpuMs: [], queries: [] };
    for (let i = 0; i < frames; i++) {
      await nextFrame();
      if (this.disposed || this.contextLost) break;
      this.timedFrame(record);
    }
    return this.timings(record);
  }

  /** Times every frame the scene draws from now on, as it draws it (an orbit, an import), until `stopFrames`. */
  startFrames(): void {
    this.recording = { cpuMs: [], queries: [] };
  }

  async stopFrames(): Promise<FrameTiming> {
    const record = this.recording ?? { cpuMs: [], queries: [] };
    this.recording = null;
    return this.timings(record);
  }

  private gpuTimer(): TimerQuery | null {
    if (this.timerQuery === undefined) {
      this.timerQuery = this.renderer.getContext().getExtension("EXT_disjoint_timer_query_webgl2") as TimerQuery | null;
    }
    return this.timerQuery;
  }

  /** Draws a frame inside a GPU timer query when there is one, noting its CPU time into `record`. */
  private timedFrame(record: FrameRecord): void {
    const gl = this.renderer.getContext() as WebGL2RenderingContext;
    const timer = this.gpuTimer();
    const query = timer ? gl.createQuery() : null;
    if (timer && query) gl.beginQuery(timer.TIME_ELAPSED_EXT, query);
    const started = performance.now();
    this.drawFrame();
    record.cpuMs.push(performance.now() - started);
    if (timer && query) {
      gl.endQuery(timer.TIME_ELAPSED_EXT);
      record.queries.push(query);
    }
  }

  /**
   * The times of a record. With EXT_disjoint_timer_query_webgl2 they are the GPU's own; without it
   * they are CPU submission times and say so (`method`). A frame the GPU reports as disjoint (a clock
   * change, a context switch) is left out.
   */
  private async timings(record: FrameRecord): Promise<FrameTiming> {
    const gl = this.renderer.getContext() as WebGL2RenderingContext;
    const timer = this.gpuTimer();
    const ms: number[] = [];
    for (const query of record.queries) {
      // Results arrive a few frames later; a minute's worth of frames is long enough to give up.
      for (let wait = 0; wait < 3600 && !this.disposed && !gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE); wait++) await nextFrame();
      const ready = !this.disposed && gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE) === true;
      if (ready && timer && !gl.getParameter(timer.GPU_DISJOINT_EXT)) ms.push(Number(gl.getQueryParameter(query, gl.QUERY_RESULT)) / 1e6);
      gl.deleteQuery(query);
    }
    return ms.length > 0 ? { method: "gpu", ms, cpuMs: record.cpuMs } : { method: "cpu", ms: record.cpuMs, cpuMs: record.cpuMs };
  }

  /** Milliseconds one `pick` call takes at the centre of the viewport. */
  /**
   * Milliseconds one topology pick costs, averaged over ``samples`` (Claude's defect 6).
   *
   * `measurePick` above times the FACE pick, which does not touch the loop that had the per-vertex
   * matrix clone and the `edges.find` scan — so it could not show whether those were fixed. This
   * calls the topology path directly, at the canvas centre, in the given mode.
   */
  measureTopologyPick(mode: "edge" | "vertex", samples = 20): number {
    const rect = this.canvas.getBoundingClientRect();
    const started = performance.now();
    for (let i = 0; i < samples; i++) {
      this.topologyAt(rect.width / 2, rect.height / 2, mode);
    }
    return (performance.now() - started) / samples;
  }

  measurePick(samples = 20): number {
    const x = this.canvas.getBoundingClientRect();
    const cx = x.left + x.width / 2;
    const cy = x.top + x.height / 2;
    const started = performance.now();
    for (let i = 0; i < samples; i++) this.pick(cx, cy);
    return (performance.now() - started) / samples;
  }

  /** Screen-space rectangles of every shown part, for the rubber-band selection. */
  projectAllPartBounds(): Map<string, { x0: number; y0: number; x1: number; y1: number }> {
    const out = new Map<string, { x0: number; y0: number; x1: number; y1: number }>();
    for (const part of this.parts.values()) {
      if (!part.shown || part.box.isEmpty()) continue;
      const rect = this.projectRect(part.box);
      if (rect) out.set(part.name, rect);
    }
    return out;
  }

  private renderFrame(): void {
    if (this.contextLost || this.disposed) return;
    if (this.recording) this.timedFrame(this.recording);
    else this.drawFrame();
  }

  private drawFrame(): void {
    if (this.contextLost || this.disposed) return;
    const started = performance.now();
    if (this.contentDirty) this.flushContent();
    const renderer = this.renderer;
    const camera = this.rig.camera;
    renderer.info.reset();
    this.rig.updateClipping();
    if (this.shadow.stale) this.shadow.render(renderer, this.scene);
    renderer.setRenderTarget(null);
    renderer.clear();
    // The batches' kept lists are sorted front to back in the first frame of a still view, and every
    // other pass and frame reuses that order (see batch.ts): an orbit costs no sorting at all.
    if (this.batchPool.takeOrderChange()) this.sortedView = false;
    const sort = this.batched && this.viewStill && !this.sortedView;
    if (sort) this.batchPool.setSorting(true);
    renderer.render(this.scene, camera);
    if (sort) {
      this.batchPool.setSorting(false);
      this.sortedView = true;
    }
    // The section's cut faces (T-065b), after the model so the marks see it in the depth buffer and
    // before the outline, so a selected part's outline is drawn over the cap rather than under it.
    // `showing` is false with no section on, so a frame with no cut pays one boolean.
    if (this.sectionCaps.showing) {
      const bounds = this.sceneBounds();
      this.sectionCaps.place(bounds);
      this.sectionCaps.render(renderer, this.scene, camera);
      // The drag handle rides the same plane, so it is placed in the same frame the cut is drawn.
      this.placeSectionHandle(bounds);
    }
    const selected = this.selected !== null ? this.parts.get(this.selected) : undefined;
    // An export frame is a one-off at another size: no helpers, no anchors, and no weight in the frame-cost average.
    if (this.exporting) return;
    if (selected?.shown) this.outline.render(renderer, this.scene, camera, selected.mesh, selected.box);
    this.gizmo.render(renderer);
    this.updateAnchor();
    // The measurement's label is at a projected 3D point, so it moves with the camera too (T-098).
    this.reportMeasure();
    this.frameCost = this.frameCost === 0 ? performance.now() - started : 0.7 * this.frameCost + 0.3 * (performance.now() - started);
  }

  // ------------------------------------------------------------------ anchor of the selection
  /**
   * Where the context toolbar attaches: the horizontal middle and the top and bottom edges of the
   * selected part's bounding box on screen, so the toolbar can sit above the part instead of on
   * it. The anchor exists while the box's centre is on screen. When a corner of the box is behind
   * the camera its projection means nothing, and the anchor is the projected centre alone.
   */
  private updateAnchor(): void {
    // In face mode the anchor is the union of the picked faces' projected bounds (ADR-0021 3.1);
    // otherwise it is the active member's box, as before.
    let box: THREE.Box3 | null = null;
    if (this.facePick.size > 0 && this.selection.visible && !this.selection.bounds.isEmpty()) {
      box = this.selection.bounds;
    } else {
      const part = this.selected !== null ? this.parts.get(this.selected) : undefined;
      if (part?.shown && !part.box.isEmpty()) box = part.box;
    }
    let next: ScreenAnchor | null = null;
    if (box) {
      const c = box.getCenter(this.scratch).project(this.rig.camera);
      if (c.z > -1 && c.z < 1 && Math.abs(c.x) <= 1 && Math.abs(c.y) <= 1) {
        // Read the centre before projecting the corners: both use the same scratch vector.
        const centre: ScreenAnchor = { x: ((c.x + 1) / 2) * this.width, y: ((1 - c.y) / 2) * this.height };
        next = this.projectBounds(box) ?? centre;
      }
    }
    this.setAnchor(next);
  }

  /** The box's screen rectangle in CSS pixels, or null when a corner is behind the camera. */
  private projectRect(box: THREE.Box3): { x0: number; y0: number; x1: number; y1: number } | null {
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (let i = 0; i < 8; i++) {
      const p = this.scratch.set(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z).project(this.rig.camera);
      if (!(p.z > -1 && p.z < 1)) return null;
      const x = ((p.x + 1) / 2) * this.width;
      const y = ((1 - p.y) / 2) * this.height;
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    }
    return { x0: minX, y0: minY, x1: maxX, y1: maxY };
  }

  /** The box's eight corners on screen as a horizontal middle and a top and bottom edge, or null when one is behind the camera. */
  private projectBounds(box: THREE.Box3): ScreenAnchor | null {
    const rect = this.projectRect(box);
    return rect === null ? null : { x: (rect.x0 + rect.x1) / 2, y: rect.y0, bottom: rect.y1 };
  }

  /** Reports the anchor when an edge moved by a quarter pixel or it appeared or vanished. */
  private setAnchor(next: ScreenAnchor | null): void {
    const last = this.lastAnchor;
    if (next === null && last === null) return;
    if (
      next !== null &&
      last !== null &&
      Math.abs(next.x - last.x) < 0.25 &&
      Math.abs(next.y - last.y) < 0.25 &&
      Math.abs((next.bottom ?? next.y) - (last.bottom ?? last.y)) < 0.25
    ) {
      return;
    }
    this.lastAnchor = next;
    this.onAnchor(next);
  }
}

/**
 * The scene the viewport is drawing with, while it is mounted. It is here rather than in the store
 * because it is a live three.js object and not state a renderer reads: the operation card (T-029)
 * asks it for one point in the view, and the viewport sets it when it makes the scene and clears it
 * when it disposes. One window, one scene.
 */
let live: SceneManager | null = null;

/** The mounted scene, or null (no WebGL, or the viewport is not up). */
export function currentScene(): SceneManager | null {
  return live;
}

/** The viewport reports the scene it made (or null when it goes away). */
export function setCurrentScene(scene: SceneManager | null): void {
  live = scene;
}
