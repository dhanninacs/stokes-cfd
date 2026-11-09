/**
 * Camera rig: one perspective and one orthographic camera behind a single OrbitControls, with
 * animated framing.
 *
 * A view is described independently of the projection by a `ViewState`: the point looked at,
 * the unit direction from that point to the camera, and the `span`, the height of the scene
 * visible at the target plane in world units. A perspective camera derives its distance from the
 * span, an orthographic one its zoom, so switching projection keeps direction and framing.
 */

import { asViewOffsetOrigin, centringShift } from "./centring";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { prefersReducedMotion } from "../app/prefs";
import type { NavMode } from "../state/store";

export const FOV = 36;
const TAN_HALF_FOV = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
const UP = new THREE.Vector3(0, 1, 0);
const IDENTITY = new THREE.Quaternion();
const FIT_DURATION = 0.35;

export interface ViewState {
  target: THREE.Vector3;
  /** Unit vector from the target towards the camera. */
  dir: THREE.Vector3;
  /** Height of the scene visible at the target plane, world units. */
  span: number;
  /** The camera's up vector (R4e: a face-on view may turn it so the figure's long axis lies across the screen); absent: leave it. */
  up?: THREE.Vector3;
}

/** Share of the canvas the framed part may occupy, as fractions of its width and height. */

/**
 * The fit frame for a free rectangle, as fractions of the canvas.
 *
 * **Pure, so the rule can be tested without a renderer** - and the rule is the point: a frame computed
 * for the whole canvas framed the model behind the panels (claude-win2's GUI review item 1). Returns
 * null when the inputs cannot say anything, so the caller keeps the frame it had rather than taking a
 * zero.
 */
export function fitFrameFromFree(
  rect: { x: number; y: number; width: number; height: number } | null,
  canvas: { width: number; height: number },
): FitFrame | null {
  if (rect === null || rect.width <= 0 || rect.height <= 0 || canvas.width <= 0 || canvas.height <= 0) return null;
  /*
    **`frameView` divides `x` by `aspect * frame.width`**, where `aspect` is the CANVAS's - so
    `frame.width` has to be the free area's share of the canvas WIDTH for the horizontal term to mean
    "the free area's width". That was right. **The vertical term is what was wrong**: `frame.height`
    multiplies `TAN_HALF_FOV`, which is the canvas's full-height half-angle, so a height share of
    `freeHeight / canvasHeight` frames the model to `freeHeight`, not to the canvas - correct - but the
    MARGIN then applies to a space the model may not have if the free area is offset.

    Measured with a 1600×816 canvas and the tree open: the model came out 1451 px wide and 818 tall
    against 816 - it **very nearly fitted, and spilled by pixels**. `FIT_MARGIN` is what absorbed
    that, and it is now applied once, to the smaller of the two shares, so neither axis can spill.
  */
  const width = (rect.width / canvas.width) * FIT_MARGIN;
  const height = (rect.height / canvas.height) * FIT_MARGIN;
  return { width: Math.max(0.1, width), height: Math.max(0.1, height) };
}

/**
 * How much of the FREE area a fit may fill on each axis.
 *
 * **The old numbers were 0.7 × 0.8 of the whole canvas**, which was the same idea measured against the
 * wrong rectangle. 0.92 of the free area keeps the air around the model that a fit is for, without
 * spending a fifth of the space on nothing.
 */
const FIT_MARGIN = 0.92;

export interface FitFrame {
  width: number;
  height: number;
}

interface Tween {
  from: ViewState;
  to: ViewState;
  /** Progress 0..1 in linear time. */
  t: number;
  duration: number;
  /** Direction of a framing animation, which a resize re-aims at the new fit; null for any other. */
  refit: THREE.Vector3 | null;
}

export type ProjectionCamera = THREE.PerspectiveCamera | THREE.OrthographicCamera;

/** Right, up and forward axes of a camera that looks at the origin from `dir`, with world Y up. */
export function viewBasis(dir: THREE.Vector3, upAxis: THREE.Vector3 = UP): { right: THREE.Vector3; up: THREE.Vector3; forward: THREE.Vector3 } {
  const forward = dir.clone().negate();
  const right = new THREE.Vector3().crossVectors(forward, upAxis);
  // Looking straight along the up axis: same choice as OrbitControls makes at its pole limit.
  if (right.lengthSq() < 1e-10) right.set(1, 0, 0);
  right.normalize();
  const up = new THREE.Vector3().crossVectors(right, forward).normalize();
  return { right, up, forward };
}

/**
 * The view that frames `points` from direction `dir` inside `frame`.
 * Exact for the given points (the corners of the part boxes), for either projection.
 */
export function frameView(
  points: readonly THREE.Vector3[],
  center: THREE.Vector3,
  dir: THREE.Vector3,
  options: { aspect: number; orthographic: boolean; frame: FitFrame; up?: THREE.Vector3 },
): ViewState {
  const { right, up, forward } = viewBasis(dir, options.up);
  const { aspect, orthographic, frame } = options;
  const v = new THREE.Vector3();
  let span = 0;
  let distance = 0;
  let behind = 0;
  for (const p of points) {
    v.subVectors(p, center);
    const x = Math.abs(v.dot(right));
    const y = Math.abs(v.dot(up));
    const z = v.dot(forward);
    if (orthographic) {
      span = Math.max(span, (2 * y) / frame.height, (2 * x) / (aspect * frame.width));
    } else {
      distance = Math.max(
        distance,
        y / (TAN_HALF_FOV * frame.height) - z,
        x / (TAN_HALF_FOV * aspect * frame.width) - z,
      );
      behind = Math.max(behind, -z);
    }
  }
  if (!orthographic) span = 2 * Math.max(distance, behind * 1.05) * TAN_HALF_FOV;
  return { target: center.clone(), dir: dir.clone().normalize(), span: Math.max(span, 1e-9), ...(options.up === undefined ? {} : { up: options.up.clone().normalize() }) };
}

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

export class CameraRig {
  readonly perspective = new THREE.PerspectiveCamera(FOV, 1, 0.001, 100);
  readonly orthographic = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.001, 100);
  readonly controls: OrbitControls<THREE.Camera>;
  camera: ProjectionCamera = this.perspective;

  /** Called when something changed that needs a new frame. */
  onChange: () => void = () => undefined;
  /** Called after the projection was switched, so dependents can rebind to the new camera. */
  onProjectionChange: (camera: ProjectionCamera) => void = () => undefined;

  private aspect = 1;
  private points: THREE.Vector3[] = [];
  private center = new THREE.Vector3();
  private radius = 0.2;
  private orthoHeight = 0.4;
  private orthoDistance = 0.6;
  private tween: Tween | null = null;
  private frame: FitFrame = { width: 0.7, height: 0.8 };

  private clipDistance = -1;
  private userTouched = false;
  /**
   * The direction of the last fit, while the camera still shows it: null once the user moved the camera or a
   * saved view was put back. **A fit stays a fit when the canvas or its free area changes afterwards**
   * (`keepFitted`). Only a fit still animating used to follow, so a free area that arrived after the opening
   * move had ended (it comes on a later animation frame, and a hidden window holds those back) left the model
   * framed for the wrong rectangle: measured on the elbow, it opened spilling 16 % past the canvas, and once
   * opened zoomed into the part, until Fit was pressed.
   */
  private fitted: THREE.Vector3 | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.perspective.position.set(-0.42, 0.26, 0.5);
    this.controls = new OrbitControls<THREE.Camera>(this.perspective, canvas);
    const controls = this.controls;
    controls.enableDamping = true;
    controls.dampingFactor = 0.09;
    controls.rotateSpeed = 0.75;
    controls.zoomToCursor = true;
    controls.minZoom = 0.02;
    controls.maxZoom = 400;
    controls.addEventListener("change", () => this.onChange());
    // The user takes over: an interrupted framing animation must not fight the drag or wheel.
    controls.addEventListener("start", () => {
      this.userTouched = true;
      this.fitted = null;
      this.endTween();
    });
    this.setNavMode("paraview");
  }

  /**
   * The mouse scheme's button bindings (ADR-0021 3.4), applied to the controls.
   *
   * three's own defaults are the web ones — left rotates, middle dolly, right pan — which is not
   * what a CAD or CFD user reaches for, and left cannot rotate here anyway when a left drag is the
   * marquee. **LEFT is ROTATE in every scheme**: `OrbitControls` ignores a button bound to nothing, so a
   * left drag that the scene hands to the camera (a press on a part under `cad`) could not turn it. The scene
   * switches the controls off for a press that starts the box, the lasso, a stroke or a handle drag.
   * Each scheme names the tool whose hands it suits; `viewport/scene.ts` owns what a LEFT
   * drag means, because that is also the select and marquee button.
   */
  setNavMode(mode: NavMode): void {
    const buttons = this.controls.mouseButtons as { LEFT: THREE.MOUSE | null; MIDDLE: THREE.MOUSE; RIGHT: THREE.MOUSE };
    switch (mode) {
      case "paraview":
        // Left rotates anywhere; the marquee moves to Shift (scene.ts).
        buttons.LEFT = THREE.MOUSE.ROTATE;
        buttons.MIDDLE = THREE.MOUSE.PAN;
        buttons.RIGHT = THREE.MOUSE.PAN;
        break;
      case "blender":
        // Middle rotates, Shift+middle pans (OrbitControls pans on middle with Shift held).
        buttons.LEFT = THREE.MOUSE.ROTATE;
        buttons.MIDDLE = THREE.MOUSE.ROTATE;
        buttons.RIGHT = THREE.MOUSE.PAN;
        break;
      case "toggle":
        // The Select/Orbit switch lives in the store; scene.ts reads it for the left button. The
        // middle and right buttons rotate and pan the same in both switch positions.
        buttons.LEFT = THREE.MOUSE.ROTATE;
        buttons.MIDDLE = THREE.MOUSE.ROTATE;
        buttons.RIGHT = THREE.MOUSE.PAN;
        break;
      case "cad":
      default:
        // FreeCAD's default, and the CAD norm: middle rotates, right pans, left selects.
        buttons.LEFT = THREE.MOUSE.ROTATE;
        buttons.MIDDLE = THREE.MOUSE.ROTATE;
        buttons.RIGHT = THREE.MOUSE.PAN;
        break;
    }
  }

  get isOrthographic(): boolean {
    return this.camera === this.orthographic;
  }

  /**
   * True once the user has orbited, panned or zoomed since the view was last framed. Content that
   * arrives later (the next solid of an import) re-frames the view only while this is false, so it
   * never pulls the camera away from where the user put it.
   */
  get touched(): boolean {
    return this.userTouched;
  }

  get target(): THREE.Vector3 {
    return this.controls.target;
  }

  /**
   * The free rectangle last given to `setFreeRect` (null: none). The image export clears it for its frame,
   * so the model is centred on the picture and not on the part of the window the cards leave open, and
   * puts it back; it cannot do that without reading it.
   */
  get freeArea(): { x: number; y: number; width: number; height: number } | null {
    return this.free;
  }

  // ------------------------------------------------------------------ configuration
  /** The unobstructed area the model should centre in, or null for the whole canvas. */
  private free: { x: number; y: number; width: number; height: number } | null = null;
  /** The canvas in CSS pixels, kept so the fit frame can be a fraction of it. */
  private canvasWidth = 0;
  private canvasHeight = 0;

  setSize(width: number, height: number): void {
    this.canvasWidth = width;
    this.canvasHeight = height;
    this.aspect = width / Math.max(1, height);
    // A resize before the first camera move re-frames at the new size, which is the other half of
    // "opens already fitted" (GUI review item 1).
    if (this.free !== null) this.setFrameFromFreeRect(this.free, { width, height });
    this.perspective.aspect = this.aspect;
    this.updateViewOffset();
    this.updateOrthoFrustum();
    // A fit follows the available area; ordinary resize leaves manual framing alone.
    this.keepFitted();
    this.onChange();
  }

  /** Re-aim a fit at the current size and free area: the one still animating, or the one still on screen. */
  private keepFitted(): void {
    if (this.tween?.refit) {
      this.tween.to = this.fitState(this.tween.refit, this.tween.to.up) ?? this.tween.to;
      return;
    }
    if (this.tween !== null || this.fitted === null || this.userTouched) return;
    const to = this.fitState(this.fitted, this.camera.up.clone());
    if (to === null) return;
    this.applyState(to);
    this.controls.update();
    this.updateViewOffset();
  }

  /**
   * Tell the camera where the FREE area of its image is, so the model centres there.
   *
   * **The owner's third layout request** (claude-win2, 1930): the canvas is full-window behind the
   * cards, so the camera centred the model on the whole window and it sat under the tree and the
   * pane. `setViewOffset` renders a WINDOW of a larger virtual image; the way to move the principal
   * point is to make the virtual image bigger by the obstructions and shift the window into it, which
   * is what this does - the offset is in pixels, and the second pair is the sub-window's size.
   *
   * **Recalled rather than stored twice**: the free rectangle is what the caller knows, so it passes
   * it in and this keeps it for the projection rebuilds `setSize` and the ortho frustum do.
   *
   * **Picking goes through the same camera**, so a raycast follows the shift automatically - which is
   * why this is the right mechanism rather than moving the model.
   */
  setFreeRect(rect: { x: number; y: number; width: number; height: number } | null): void {
    this.free = rect;
    this.updateViewOffset();
    this.updateOrthoFrustum();
    // **The FIT FRAME follows the same rectangle** (GUI review item 1). A frame computed for the whole
    // canvas is what framed the model behind the panels; deriving both from one rectangle is what
    // keeps "where the camera looks" and "how much of it a part may fill" from disagreeing.
    this.setFrameFromFreeRect(rect, { width: this.canvasWidth, height: this.canvasHeight });
    this.keepFitted();
    this.onChange();
  }

  /**
   * Shift the image so the model centres in the free rectangle, **without changing the field of view**.
   *
   * **`setViewOffset` was the wrong tool, and it broke the scale** (the owner: *"resizing window
   * breaks the CAD display scale"*). It renders a SUB-WINDOW of a larger virtual image, so passing the
   * free rectangle as that window **magnifies the model by `canvas / freeWidth`** - measured: with the
   * tree open the pipe grew to run off both edges while the scale bar still read `20 mm`.
   *
   * What a principal-point shift needs is a change to the projection's OFF-CENTRE terms. An
   * asymmetric frustum moves what is centred without touching the width of the frustum, so a metre
   * stays the same number of pixels - which is also what makes `ScaleBar` honest, because it measures
   * pixels against the canvas, and the canvas is no longer being reinterpreted.
   */
  private updateViewOffset(): void {
    const rect = this.free;
    if (rect === null || rect.width <= 0 || rect.height <= 0 || this.canvasWidth <= 0 || this.canvasHeight <= 0) {
      this.shift = { dx: 0, dy: 0 };
    } else {
      this.shift = this.centringFor(rect);
    }
    this.applyProjection();
  }


  /** The principal-point shift, as a fraction of the canvas: +x moves the model right on screen. */
  /**
   * How far the IMAGE must move, in canvas PIXELS: +x moves the model right, +y moves it down.
   *
   * **Pixels, not fractions**, because the measurement that produces it is in pixels (the
   * projected box's centre) - and a fraction that is silently divided by the canvas in one
   * place and not another is how my first attempts went wrong. The one place that turns it
   * into three.js's negated origin is `asViewOffsetOrigin`.
   */
  private shift = { dx: 0, dy: 0 };

  /**
   * Build both projection matrices from the aspect, the free rectangle's share and the shift.
   *
   * One place, because the two cameras must not disagree: an asymmetric frustum is what makes the
   * model appear in the free area, and an off-centre term applied to one camera only would move the
   * model sideways when `O` switches projection.
   */
  private applyProjection(): void {
    const perspective = this.perspective;
    if (this.shift.dx === 0 && this.shift.dy === 0) {
      perspective.clearViewOffset();
      perspective.aspect = this.aspect;
      perspective.updateProjectionMatrix();
      this.updateOrthoFrustum();
      return;
    }
    /*
      **`view` with a full-canvas window and a shifted ORIGIN** is how three.js expresses an
      off-centre frustum: the whole image is drawn, and the window is what the camera sees. With the
      window's size equal to the full image the scale is unchanged; only the origin moves, which is
      exactly a principal-point shift.
    */
    perspective.aspect = this.aspect;
    perspective.updateProjectionMatrix();
    // **The sign, from the tested helper**: three.js moves the content by MINUS the origin.
    const origin = asViewOffsetOrigin(this.shift.dx, this.shift.dy);
    perspective.setViewOffset(
      this.canvasWidth,
      this.canvasHeight,
      origin.x,
      origin.y,
      this.canvasWidth,
      this.canvasHeight,
    );
    /*
      **AND fold it into the matrix.** three.js stores the offset in `camera.view` and only applies it on
      the next `updateProjectionMatrix()` - which this branch called BEFORE `setViewOffset`, so the shift
      was stored and never reached the matrix. **That was the whole bug**: the value was computed
      correctly every time and silently discarded here, which is why four attempts measured a camera that
      never moved.

      `seam.test.ts` proves the three.js behaviour (a shifted point moves by exactly the shift) and would
      catch this branch losing it; the comment records why the order matters, because the call above it
      looks sufficient and is not.
    */
    perspective.updateProjectionMatrix();
    this.updateOrthoFrustum();
  }

  /**
   * Where to move the image so the PROJECTED model centres on the free area (Claude's closed form).
   *
   * **Clear first, then measure, then subtract once** - the step my failed attempts skipped. The offset
   * is cleared on a CLONE so the measured projection is what the camera shows with no shift in place;
   * measuring with a previous offset applied is what left a permanent residual.
   *
   * **A principal-point shift translates every projected point by the same vector**, so this needs no
   * iteration: `centringShift` is pure and tested, and the seam to the matrix is tested in `seam.test.ts`.
   */
  private centringFor(free: { x: number; y: number; width: number; height: number }): { dx: number; dy: number } {
    if (this.points.length === 0) return { dx: 0, dy: 0 };
    const camera = this.camera.clone();
    camera.clearViewOffset();
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();
    const project = (point: unknown): { x: number; y: number } => {
      const v = (point as THREE.Vector3).clone().project(camera);
      return { x: ((v.x + 1) / 2) * this.canvasWidth, y: ((1 - v.y) / 2) * this.canvasHeight };
    };
    return centringShift(this.points, project, free);
  }

  /**
   * The share of the canvas the framed part may fill.
   *
   * **Derived from the FREE rectangle, not fixed** (claude-win2's GUI review item 1: a project opened
   * with the camera too near the model and F fixed it). The old constant - 0.7 by 0.8 of the whole
   * canvas - was computed as if no panel covered anything, so with the tree and the pane open the
   * model was framed into a space largely hidden behind them.
   *
   * The margin is kept: framing a part exactly to the edge of the free area leaves no air around it,
   * which reads as a crop rather than as a fit.
   */
  setFrame(frame: FitFrame): void {
    this.frame = frame;
  }

  /** The fit frame for a free rectangle; the arithmetic is `fitFrameFromFree`, which is pure. */
  setFrameFromFreeRect(rect: { x: number; y: number; width: number; height: number } | null, canvas: { width: number; height: number }): void {
    const frame = fitFrameFromFree(rect, canvas);
    if (frame !== null) this.frame = frame;
  }

  /** The part to frame: its box corners, the centre of its bounds and the radius of those bounds. */
  setContent(points: THREE.Vector3[], bounds: THREE.Sphere): void {
    this.points = points;
    this.center.copy(bounds.center);
    this.radius = Math.max(bounds.radius, 1e-6);
    this.orthoHeight = this.radius * 2;
    this.orthoDistance = this.radius * 3;
    this.controls.minDistance = this.radius * 0.05;
    this.controls.maxDistance = this.radius * 120;
    this.clipDistance = -1;
    this.updateOrthoFrustum();
  }

  /** Replaces only the points that fit uses, e.g. after patches were hidden; `center` also moves what a fit aims at (until the next `setContent`). */
  setFitPoints(points: THREE.Vector3[], center?: THREE.Vector3): void {
    if (points.length === 0) return;
    this.points = points;
    if (center !== undefined) this.center.copy(center);
  }

  private updateOrthoFrustum(): void {
    const half = this.orthoHeight / 2;
    const cam = this.orthographic;
    cam.left = -half * this.aspect;
    cam.right = half * this.aspect;
    cam.top = half;
    cam.bottom = -half;
    /*
      **The ortho camera gets the same principal-point shift**, and it needs it: the two cameras are
      switched by `O`, so an offset applied to one only would move the model sideways when the
      projection changes - the failure a user reads as "the view moves when I press O".

      Same construction as the perspective one: a full-canvas window whose ORIGIN moves, so the scale
      is untouched and only what is centred changes.
    */
    if ((this.shift.dx === 0 && this.shift.dy === 0) || this.canvasWidth <= 0 || this.canvasHeight <= 0) {
      cam.clearViewOffset();
    } else {
      const origin = asViewOffsetOrigin(this.shift.dx, this.shift.dy);
      cam.setViewOffset(
        this.canvasWidth,
        this.canvasHeight,
        origin.x,
        origin.y,
        this.canvasWidth,
        this.canvasHeight,
      );
    }
    cam.updateProjectionMatrix();
  }

  // ------------------------------------------------------------------ state
  getState(): ViewState {
    const target = this.controls.target.clone();
    const offset = this.camera.position.clone().sub(target);
    const distance = offset.length();
    const dir = distance > 1e-12 ? offset.divideScalar(distance) : new THREE.Vector3(-0.6, 0.4, 0.7).normalize();
    const span =
      this.camera === this.perspective
        ? 2 * distance * TAN_HALF_FOV
        : this.orthoHeight / Math.max(this.orthographic.zoom, 1e-9);
    return { target, dir, span, up: this.camera.up.clone() };
  }

  private applyState(state: ViewState): void {
    const cam = this.camera;
    this.controls.target.copy(state.target);
    if (cam === this.perspective) {
      const distance = THREE.MathUtils.clamp(
        state.span / (2 * TAN_HALF_FOV),
        this.controls.minDistance,
        this.controls.maxDistance,
      );
      cam.position.copy(state.target).addScaledVector(state.dir, distance);
    } else {
      cam.position.copy(state.target).addScaledVector(state.dir, this.orthoDistance);
      cam.zoom = THREE.MathUtils.clamp(this.orthoHeight / state.span, this.controls.minZoom, this.controls.maxZoom);
      cam.updateProjectionMatrix();
    }
    if (state.up !== undefined) this.setUp(state.up);
    cam.lookAt(state.target);
    cam.updateMatrixWorld();
  }

  /**
   * Turns the camera's up vector (both cameras, so a projection switch keeps it) and the orbit controls' own frame with it,
   * so a drag from a face-on view turns about the figure's up axis and does not jump.
   */
  private setUp(up: THREE.Vector3): void {
    this.perspective.up.copy(up);
    this.orthographic.up.copy(up);
    const frame = this.controls as unknown as { _quat?: THREE.Quaternion; _quatInverse?: THREE.Quaternion };
    if (frame._quat !== undefined && frame._quatInverse !== undefined) {
      frame._quat.setFromUnitVectors(up, UP);
      frame._quatInverse.copy(frame._quat).invert();
    }
  }

  // ------------------------------------------------------------------ framing
  /** Where the camera looks from: the end of a running animation, else where it is now. */
  private get heading(): THREE.Vector3 {
    return this.tween ? this.tween.to.dir : this.getState().dir;
  }

  /** The view that frames the current content from `dir` (default: the current heading). */
  fitState(dir?: THREE.Vector3, up?: THREE.Vector3): ViewState | null {
    if (this.points.length === 0) return null;
    const orthographic = this.camera !== this.perspective;
    // A fit towards a new direction is world-Y up unless it says otherwise; a fit that keeps the heading keeps the roll.
    const upAxis = up ?? (dir === undefined ? this.tween?.to.up ?? this.camera.up : UP);
    const state = frameView(this.points, this.center, dir ?? this.heading, {
      aspect: this.aspect,
      orthographic,
      frame: this.frame,
      up: upAxis,
    });
    // Projection centring is measured after applying the fitted camera pose.
    return state;
  }





  /** Frames the content, animated unless the user prefers reduced motion. */
  fit(dir?: THREE.Vector3, up?: THREE.Vector3): void {
    const to = this.fitState(dir, up);
    if (!to) return;
    this.userTouched = false;
    this.fitted = to.dir.clone();
    this.animateTo(to, FIT_DURATION, true);
  }

  /** Starts from a pulled-back, slightly turned view and settles into the fit. Used on load. */
  intro(): void {
    const to = this.fitState();
    if (!to) return;
    this.userTouched = false;
    this.fitted = to.dir.clone();
    const turn = new THREE.Quaternion().setFromAxisAngle(UP, THREE.MathUtils.degToRad(-24));
    this.applyState({ target: to.target, dir: to.dir.clone().applyQuaternion(turn), span: to.span * 1.7 });
    this.controls.update();
    this.animateTo(to, FIT_DURATION, true);
  }

  /**
   * Puts the camera at `state` at once and keeps it there: a saved view's camera (R4). Unlike `animateTo` it takes the camera
   * as the user's (`touched`), so content that arrives afterwards (a patch, a cut) does not re-frame the picture that was
   * restored. A state with no `up` leaves the up vector as it is.
   */
  restore(state: ViewState): void {
    this.endTween();
    this.applyState(state);
    this.controls.update();
    this.userTouched = true;
    this.fitted = null;
    this.onChange();
  }

  /** Moves to `to`; with `refit` a later resize re-aims the move at the fit for the new size. */
  animateTo(to: ViewState, duration = FIT_DURATION, refit = false): void {
    if (prefersReducedMotion() || duration <= 0) {
      this.endTween();
      this.applyState(to);
      this.controls.update();
      if (refit) this.updateViewOffset();
      this.onChange();
      return;
    }
    this.tween = { from: this.getState(), to, t: 0, duration, refit: refit ? to.dir.clone() : null };
    // Leftover drag inertia would keep turning the camera against the animation.
    this.controls.enableDamping = false;
    this.onChange();
  }

  get animating(): boolean {
    return this.tween !== null;
  }

  private endTween(): void {
    this.tween = null;
    this.controls.enableDamping = true;
  }

  /** The up vector part-way through a framing move: it turns with the heading, and the last frame lands exactly on the target's. */
  private tweenUp(tween: Tween, e: number, done: boolean): THREE.Vector3 | undefined {
    const to = tween.to.up;
    if (to === undefined) return undefined;
    if (done || tween.from.up === undefined) return to.clone();
    const mid = tween.from.up.clone().lerp(to, e);
    return mid.lengthSq() < 1e-8 ? to.clone() : mid.normalize();
  }

  /** Advances the animation and the controls; true when the camera moved. */
  update(dt: number): boolean {
    let moved = false;
    const tween = this.tween;
    if (tween) {
      tween.t = Math.min(1, tween.t + dt / tween.duration);
      const e = easeOutCubic(tween.t);
      const delta = new THREE.Quaternion().setFromUnitVectors(tween.from.dir, tween.to.dir);
      const turn = new THREE.Quaternion().slerpQuaternions(IDENTITY, delta, e);
      const done = tween.t >= 1;
      this.applyState({
        target: tween.from.target.clone().lerp(tween.to.target, e),
        // The last frame lands exactly on the target direction. Rounding noise next to a pole
        // (the top and bottom views) would otherwise pick a random roll about the view axis.
        dir: done ? tween.to.dir.clone() : tween.from.dir.clone().applyQuaternion(turn).normalize(),
        span: tween.from.span * Math.pow(tween.to.span / tween.from.span, e),
        up: this.tweenUp(tween, e, done),
      });
      if (tween.refit) this.updateViewOffset();
      if (done) this.endTween();
      moved = true;
    }
    // Controls keep the damping going and re-derive the orientation from the new position.
    if (this.controls.update()) moved = true;
    return moved;
  }

  // ------------------------------------------------------------------ projection
  setOrthographic(on: boolean): void {
    if (on === this.isOrthographic) return;
    const state = this.getState();
    this.endTween();
    this.camera = on ? this.orthographic : this.perspective;
    this.controls.object = this.camera;
    this.applyState(state);
    this.controls.update();
    this.clipDistance = -1;
    this.updateClipping();
    this.onProjectionChange(this.camera);
    this.onChange();
  }

  /** Keeps the clipping planes proportional to the distance so depth precision holds at any zoom. */
  updateClipping(): void {
    const cam = this.camera;
    if (cam === this.perspective) {
      const distance = cam.position.distanceTo(this.controls.target);
      if (Math.abs(distance - this.clipDistance) < this.clipDistance * 0.02) return;
      this.clipDistance = distance;
      cam.near = Math.max(distance * 0.004, this.radius * 1e-4);
      cam.far = Math.max(distance, this.radius) * 40;
    } else {
      const near = this.radius * 0.02;
      const far = this.orthoDistance + this.radius * 40;
      if (cam.near === near && cam.far === far) return;
      cam.near = near;
      cam.far = far;
    }
    cam.updateProjectionMatrix();
  }

  dispose(): void {
    this.controls.dispose();
  }
}
