/**
 * Selection outline: a crisp accent line around the visible part of the selected patch.
 *
 * The selected patch is drawn in white into a multisampled mask after the other patches have
 * written their depth, so what other patches hide stays out of the mask. A fullscreen pass then
 * finds the pixels just outside the mask and draws them in the accent colour with straight alpha
 * straight onto the canvas.
 *
 * The other patches are whatever the scene has on its occluder layer (see scene.ts), drawn depth-only
 * through an override material: the opaque parts' own meshes, or the batches that draw thousands of
 * them (ADR-0019) in a few calls. Translucent parts and batches stay off that layer (ADR-0021 4.4),
 * so they hide nothing: the outline of a part behind glass is whole, drawn over the glass. Hidden
 * parts are left out by the scene's own visibility, a hidden instance by its batch. The selected
 * patch is taken off the layer for that pass and drawn into the mask by one mesh that borrows its
 * geometry, so the outline keeps nothing per part.
 *
 * This replaces the stock OutlinePass on purpose. OutlinePass works at half resolution with a
 * blur, so its line is soft; it adds its result, which cannot be seen over a light background;
 * and it renders and hides every object of the scene it is given, which the ground and the
 * shadow plane would corrupt. Compositing onto the canvas also keeps the main image untouched:
 * hardware antialiasing, exact alpha over the CSS background, no tone-mapping detour through a
 * premultiplied float target.
 *
 * Cost control: the mask has one 8-bit channel, its depth is not resolved, it drops to half
 * resolution on very large canvases, and every pass is clipped to the screen rectangle of the
 * selected patch. The shaders are compiled by `warmUp` when the part loads, and again when the scene
 * starts drawing batches, so the first selection does not stall on the shader compiler.
 */

import * as THREE from "three";
import { FullScreenQuad } from "three/addons/postprocessing/Pass.js";
import type { ProjectionCamera } from "./camera";

const VERTEX = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const FRAGMENT = /* glsl */ `
  uniform sampler2D tMask;
  uniform vec2 uTexel;      // 1 / mask size in pixels
  uniform float uWidth;     // outline width in mask pixels
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec2 vUv;

  float tap(vec2 offsetPx) {
    return texture2D(tMask, vUv + offsetPx * uTexel).r;
  }

  void main() {
    float inside = texture2D(tMask, vUv).r;
    if (inside > 0.999) discard;                 // the surface itself carries the emissive lift
    float w = uWidth;
    float d = w * 0.70710678;
    // Eight taps on the outer ring reject almost the whole screen cheaply.
    float reach = max(
      max(max(tap(vec2(w, 0.0)), tap(vec2(-w, 0.0))), max(tap(vec2(0.0, w)), tap(vec2(0.0, -w)))),
      max(max(tap(vec2(d, d)), tap(vec2(-d, d))), max(tap(vec2(d, -d)), tap(vec2(-d, -d))))
    );
    if (reach <= 0.0 && inside <= 0.0) discard;
    for (int i = 0; i < 16; i++) {
      float a = float(i) * 0.39269908;
      vec2 o = vec2(cos(a), sin(a)) * w;
      reach = max(reach, max(tap(o), tap(o * 0.5)));
    }
    float edge = clamp(reach - inside, 0.0, 1.0);
    if (edge <= 0.0) discard;
    gl_FragColor = vec4(uColor, edge * uOpacity);
    #include <colorspace_fragment>
  }
`;

/** Outline width in CSS pixels. */
const WIDTH = 2.5;
/** Canvases with more device pixels than this get a half-resolution mask, to bound its memory. */
const MAX_FULL_RES_PIXELS = 4_500_000;

const MASK_OPTIONS = {
  type: THREE.UnsignedByteType,
  format: THREE.RedFormat,
  minFilter: THREE.LinearFilter,
  magFilter: THREE.LinearFilter,
  generateMipmaps: false,
  depthBuffer: true,
  // Only the colour is read back; resolving the depth as well would cost memory and bandwidth.
  resolveDepthBuffer: false,
  samples: 4,
} as const;

/** A pixel rectangle with its origin at the bottom left, as WebGL wants it. */
interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export class SelectionOutline {
  /** Holds only `mask`, which borrows the selected patch's geometry while the mask is drawn. */
  private readonly maskScene = new THREE.Scene();
  private readonly mask: THREE.Mesh;
  private readonly empty = new THREE.BufferGeometry();
  private readonly occluderMaterial = new THREE.MeshBasicMaterial({
    colorWrite: false,
    side: THREE.DoubleSide,
    // Pushed back a hair so the selected patch wins where it touches a neighbour.
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  });
  private readonly maskMaterial = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    side: THREE.DoubleSide,
    toneMapped: false,
  });
  private readonly target = new THREE.WebGLRenderTarget(1, 1, MASK_OPTIONS);
  private readonly composite: THREE.ShaderMaterial;
  private readonly quad: FullScreenQuad;
  private readonly oldClear = new THREE.Color();
  private readonly corner = new THREE.Vector3();
  private readonly oldScissor = new THREE.Vector4();
  private pixelRatio = 1;
  private canvasWidth = 1;
  private canvasHeight = 1;
  private maskScale = 1;
  /** What `warmUp` compiled the shaders for; it runs again when that changes. */
  private warmed = "";

  /** `layer` is the scene's occluder layer: the occluders are what is on it. */
  constructor(
    accent: string,
    private readonly layer: number,
  ) {
    this.mask = new THREE.Mesh(this.empty, this.maskMaterial);
    this.mask.matrixAutoUpdate = false;
    this.mask.frustumCulled = false;
    this.maskScene.add(this.mask);
    this.composite = new THREE.ShaderMaterial({
      uniforms: {
        tMask: { value: this.target.texture },
        uTexel: { value: new THREE.Vector2(1, 1) },
        uWidth: { value: WIDTH },
        uColor: { value: new THREE.Color(accent) },
        uOpacity: { value: 0.96 },
      },
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    this.quad = new FullScreenQuad(this.composite);
  }

  setAccent(accent: string): void {
    this.composite.uniforms.uColor.value.set(accent);
  }

  /** Canvas size in device pixels and the ratio to CSS pixels, so the line has a constant CSS width. */
  setSize(width: number, height: number, pixelRatio: number): void {
    this.pixelRatio = pixelRatio;
    this.canvasWidth = Math.max(1, width);
    this.canvasHeight = Math.max(1, height);
    this.maskScale = this.canvasWidth * this.canvasHeight > MAX_FULL_RES_PIXELS ? 0.5 : 1;
    const w = Math.max(1, Math.ceil(this.canvasWidth * this.maskScale));
    const h = Math.max(1, Math.ceil(this.canvasHeight * this.maskScale));
    this.target.setSize(w, h);
    this.composite.uniforms.uTexel.value.set(1 / w, 1 / h);
    this.composite.uniforms.uWidth.value = WIDTH * this.pixelRatio * this.maskScale;
  }

  /**
   * Draws everything on the occluder layer except `selected` into the current target's depth, with
   * the occluder material: the opaque patches, which can hide part of the selection.
   */
  private drawOccluders(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: ProjectionCamera, selected: THREE.Object3D | null): void {
    const layers = camera.layers.mask;
    const override = scene.overrideMaterial;
    const own = selected !== null && selected.layers.isEnabled(this.layer);
    if (own) selected.layers.disable(this.layer);
    camera.layers.set(this.layer);
    scene.overrideMaterial = this.occluderMaterial;
    try {
      renderer.render(scene, camera);
    } finally {
      scene.overrideMaterial = override;
      camera.layers.mask = layers;
      if (own) selected.layers.enable(this.layer);
    }
  }

  /** Draws `selected` in white into the current target, over the occluders' depth. */
  private drawMask(renderer: THREE.WebGLRenderer, camera: ProjectionCamera, selected: THREE.Mesh): void {
    this.mask.geometry = selected.geometry;
    this.mask.matrix.copy(selected.matrixWorld);
    this.mask.matrixWorldNeedsUpdate = true;
    renderer.render(this.maskScene, camera);
    // Not kept: the part may be freed before the next selection.
    this.mask.geometry = this.empty;
  }

  /**
   * Compiles the occluder, mask and composite shaders now, into a 4 x 4 target and one pixel of the
   * canvas, so the first selection has nothing left to compile. Draws nothing visible. `sample` is any
   * part's mesh; `key` names what the scene draws (its own meshes, or batches as well), whose shaders
   * differ, so a new key compiles again.
   */
  warmUp(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: ProjectionCamera, sample: THREE.Mesh, key: string): void {
    if (this.warmed === key) return;
    this.warmed = key;
    const probe = new THREE.WebGLRenderTarget(4, 4, MASK_OPTIONS);
    const previousTarget = renderer.getRenderTarget();
    renderer.setRenderTarget(probe);
    this.drawOccluders(renderer, scene, camera, null);
    this.drawMask(renderer, camera, sample);
    renderer.setRenderTarget(previousTarget);

    const mask = this.composite.uniforms.tMask.value as THREE.Texture;
    this.composite.uniforms.tMask.value = probe.texture;
    const previousTest = renderer.getScissorTest();
    renderer.getScissor(this.oldScissor);
    renderer.setScissorTest(true);
    renderer.setScissor(0, 0, 1, 1);
    this.quad.render(renderer);
    renderer.setScissor(this.oldScissor.x, this.oldScissor.y, this.oldScissor.z, this.oldScissor.w);
    renderer.setScissorTest(previousTest);
    this.composite.uniforms.tMask.value = mask;
    probe.dispose();
  }

  /**
   * The canvas pixels `box` can cover, grown by `pad`, or null when it is entirely off screen.
   * A box that reaches behind the camera has no usable projection, so it gets the whole canvas.
   */
  private screenRect(box: THREE.Box3, camera: ProjectionCamera, pad: number): Rect | null {
    const width = this.canvasWidth;
    const height = this.canvasHeight;
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (let i = 0; i < 8; i++) {
      const c = this.corner.set(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z);
      c.applyMatrix4(camera.matrixWorldInverse);
      if (-c.z <= camera.near) return { x: 0, y: 0, w: width, h: height };
      c.applyMatrix4(camera.projectionMatrix);
      x0 = Math.min(x0, (c.x * 0.5 + 0.5) * width);
      x1 = Math.max(x1, (c.x * 0.5 + 0.5) * width);
      y0 = Math.min(y0, (c.y * 0.5 + 0.5) * height);
      y1 = Math.max(y1, (c.y * 0.5 + 0.5) * height);
    }
    x0 = Math.max(0, Math.floor(x0 - pad));
    y0 = Math.max(0, Math.floor(y0 - pad));
    x1 = Math.min(width, Math.ceil(x1 + pad));
    y1 = Math.min(height, Math.ceil(y1 + pad));
    return x1 > x0 && y1 > y0 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
  }

  /**
   * Draws the outline of `selected` over the canvas. Call after the scene, before the view helper.
   * Only the pixels around the projected `box` of the patch are drawn and resolved: the mask is
   * cleared and read inside that rectangle, so an outline costs in proportion to the patch.
   */
  render(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: ProjectionCamera, selected: THREE.Mesh, box: THREE.Box3): void {
    // Taps reach as far as the outline is wide, so the mask is cleared that much beyond the pixels read.
    const reach = Math.ceil(WIDTH * this.pixelRatio) + 2;
    const inner = this.screenRect(box, camera, reach);
    if (!inner) return;
    const x = Math.max(0, inner.x - reach);
    const y = Math.max(0, inner.y - reach);
    const right = Math.min(this.canvasWidth, inner.x + inner.w + reach);
    const top = Math.min(this.canvasHeight, inner.y + inner.h + reach);

    const previousTarget = renderer.getRenderTarget();
    const previousAlpha = renderer.getClearAlpha();
    renderer.getClearColor(this.oldClear);
    renderer.setClearColor(0x000000, 0);
    // The mask scissor is in mask pixels; one extra pixel absorbs the rounding.
    const s = this.maskScale;
    this.target.scissor.set(Math.floor(x * s), Math.floor(y * s), Math.ceil((right - x) * s) + 1, Math.ceil((top - y) * s) + 1);
    this.target.scissorTest = true;
    renderer.setRenderTarget(this.target);
    renderer.clear();
    this.drawOccluders(renderer, scene, camera, selected);
    this.drawMask(renderer, camera, selected);
    renderer.setRenderTarget(previousTarget);
    renderer.setClearColor(this.oldClear, previousAlpha);

    // The canvas scissor is in CSS pixels; the extra pixel absorbs the rounding.
    const previousTest = renderer.getScissorTest();
    renderer.getScissor(this.oldScissor);
    const ratio = this.pixelRatio;
    renderer.setScissorTest(true);
    renderer.setScissor((inner.x - 1) / ratio, (inner.y - 1) / ratio, (inner.w + 2) / ratio, (inner.h + 2) / ratio);
    this.quad.render(renderer);
    renderer.setScissor(this.oldScissor.x, this.oldScissor.y, this.oldScissor.z, this.oldScissor.w);
    renderer.setScissorTest(previousTest);
  }

  dispose(): void {
    this.target.dispose();
    this.quad.dispose();
    this.composite.dispose();
    this.occluderMaterial.dispose();
    this.maskMaterial.dispose();
    this.empty.dispose();
  }
}
