/**
 * Contact shadow: a blurred, height-weighted silhouette of the part on the ground.
 *
 * The part is drawn from below the ground with an orthographic camera into a small render
 * target. Each pixel keeps the lowest surface above it and stores how close it is to the ground,
 * so parts that touch the floor cast a dark, tight shadow and high ones a faint, wide one. Two
 * separable blurs soften it and a plane on the ground shows the result. The target only changes
 * when the part or its visibility does, never while the camera moves.
 *
 * What casts the shadow is whatever the scene has on the part layer (see scene.ts), drawn with the
 * height material as an override: the parts' own meshes, or the batches that draw thousands of them
 * at once (ADR-0019), each shown part once either way. Nothing is kept per part here.
 */

import * as THREE from "three";
import { FullScreenQuad } from "three/addons/postprocessing/Pass.js";
import type { SceneTheme } from "./theme";

const SIZE = 512;
/** Blur width as a share of the footprint; two passes of sigma/sqrt(2) each. */
const BLUR_SHARE = 0.05;
/** Margin around the footprint, in blur sigmas, so the shadow fades out inside the plane. */
const MARGIN_SIGMAS = 4;

// A batch places each of its parts by a matrix of its own (the identity today), which the chunks fetch.
const HEIGHT_VERTEX = /* glsl */ `
  #include <batching_pars_vertex>
  uniform float uGroundY;
  varying float vHeight;
  void main() {
    vec4 local = vec4(position, 1.0);
    #include <batching_vertex>
    #ifdef USE_BATCHING
      local = batchingMatrix * local;
    #endif
    vec4 world = modelMatrix * local;
    vHeight = world.y - uGroundY;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const HEIGHT_FRAGMENT = /* glsl */ `
  uniform float uFade;
  varying float vHeight;
  void main() {
    float k = 1.0 - smoothstep(0.0, uFade, vHeight);
    gl_FragColor = vec4(k, k, k, 1.0);
  }
`;

const BLUR_VERTEX = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const BLUR_FRAGMENT = /* glsl */ `
  uniform sampler2D tInput;
  uniform vec2 uStep;      // uv distance between taps
  varying vec2 vUv;
  void main() {
    float sum = 0.0;
    float weights = 0.0;
    for (int i = -6; i <= 6; i++) {
      float w = exp(-0.5 * float(i * i) / 4.0);   // sigma of two taps, cut off at three sigma
      sum += w * texture2D(tInput, vUv + uStep * float(i)).r;
      weights += w;
    }
    float v = sum / weights;
    gl_FragColor = vec4(v, v, v, 1.0);
  }
`;

const DISPLAY_VERTEX = /* glsl */ `
  varying vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const DISPLAY_FRAGMENT = /* glsl */ `
  uniform sampler2D tShadow;
  uniform mat4 uShadowMatrix;
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec3 vWorld;

  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
  }

  void main() {
    vec4 clip = uShadowMatrix * vec4(vWorld, 1.0);
    vec2 uv = clip.xy / clip.w * 0.5 + 0.5;
    float edge = min(min(uv.x, uv.y), min(1.0 - uv.x, 1.0 - uv.y));
    float a = texture2D(tShadow, uv).r * uOpacity * smoothstep(0.0, 0.08, edge);
    a += (hash(gl_FragCoord.xy) - 0.5) / 255.0;   // breaks up 8-bit banding in the gradient
    if (a < 0.003) discard;
    gl_FragColor = vec4(uColor, a);
    #include <colorspace_fragment>
  }
`;

export class ContactShadow {
  /** The plane that shows the shadow; add it to the scene. */
  readonly mesh: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;
  private readonly rtA: THREE.WebGLRenderTarget;
  private readonly rtB: THREE.WebGLRenderTarget;
  private readonly camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly heightMaterial: THREE.ShaderMaterial;
  private readonly blurMaterial: THREE.ShaderMaterial;
  private readonly quad: FullScreenQuad;
  private readonly display: THREE.ShaderMaterial;
  private readonly oldClear = new THREE.Color();
  private blurStep = 0.01;
  private ready = false;

  /** `layer` is the scene's part layer: the camera that draws the shadow sees only it. */
  constructor(theme: SceneTheme, layer: number) {
    this.camera.layers.set(layer);
    const options = {
      type: THREE.HalfFloatType,
      format: THREE.RGBAFormat,
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
      generateMipmaps: false,
    };
    this.rtA = new THREE.WebGLRenderTarget(SIZE, SIZE, { ...options, depthBuffer: true });
    this.rtB = new THREE.WebGLRenderTarget(SIZE, SIZE, { ...options, depthBuffer: false });

    this.heightMaterial = new THREE.ShaderMaterial({
      uniforms: { uGroundY: { value: 0 }, uFade: { value: 1 } },
      vertexShader: HEIGHT_VERTEX,
      fragmentShader: HEIGHT_FRAGMENT,
      side: THREE.DoubleSide,
    });
    this.blurMaterial = new THREE.ShaderMaterial({
      uniforms: { tInput: { value: null }, uStep: { value: new THREE.Vector2() } },
      vertexShader: BLUR_VERTEX,
      fragmentShader: BLUR_FRAGMENT,
      depthTest: false,
      depthWrite: false,
    });
    this.quad = new FullScreenQuad(this.blurMaterial);

    this.display = new THREE.ShaderMaterial({
      uniforms: {
        tShadow: { value: this.rtA.texture },
        uShadowMatrix: { value: new THREE.Matrix4() },
        uColor: { value: new THREE.Color() },
        uOpacity: { value: 0.5 },
      },
      vertexShader: DISPLAY_VERTEX,
      fragmentShader: DISPLAY_FRAGMENT,
      transparent: true,
      depthWrite: false,
      // One-sided: seen from below the ground it would smear a dark patch over the part.
      side: THREE.FrontSide,
      toneMapped: false,
    });
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2), this.display);
    this.mesh.name = "contact-shadow";
    this.mesh.renderOrder = -1;
    this.mesh.frustumCulled = false;
    this.mesh.visible = false;
    this.setTheme(theme);
  }

  setTheme(theme: SceneTheme): void {
    this.display.uniforms.uColor.value.set(theme.shadow.color);
    this.display.uniforms.uOpacity.value = theme.shadow.alpha;
  }

  /** Fits the shadow camera and plane to the footprint of `box`, above ground level `groundY`. */
  place(box: THREE.Box3, groundY: number): void {
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const extent = Math.max(size.x, size.y, size.z, 1e-6);
    const sigma = Math.max(size.x, size.z) * BLUR_SHARE;
    const side = Math.max(size.x, size.z) + 2 * MARGIN_SIGMAS * sigma;
    const half = side / 2;

    const cam = this.camera;
    cam.left = -half;
    cam.right = half;
    cam.top = half;
    cam.bottom = -half;
    cam.near = 0;
    cam.far = box.max.y - groundY + extent * 0.05;
    cam.up.set(0, 0, -1);
    cam.position.set(centre.x, groundY, centre.z);
    cam.lookAt(centre.x, groundY + 1, centre.z);
    cam.updateProjectionMatrix();
    cam.updateMatrixWorld();

    this.heightMaterial.uniforms.uGroundY.value = groundY;
    this.heightMaterial.uniforms.uFade.value = extent * 0.25;
    this.display.uniforms.uShadowMatrix.value.multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse);
    this.mesh.position.set(centre.x, groundY, centre.z);
    this.mesh.scale.set(side, 1, side);
    // Two passes per axis add their variances, so each takes sigma / sqrt(2). The kernel has a
    // sigma of two taps; a tap step is a distance in uv units of the target.
    this.blurStep = sigma / Math.SQRT2 / 2 / side;
    this.mesh.visible = true;
    this.ready = false;
  }

  hide(): void {
    this.mesh.visible = false;
  }

  /** True when the shadow has to be redrawn. */
  get stale(): boolean {
    return !this.ready && this.mesh.visible;
  }

  invalidate(): void {
    this.ready = false;
  }

  /** Redraws the shadow from what `scene` shows on the part layer. */
  render(renderer: THREE.WebGLRenderer, scene: THREE.Scene): void {
    const previousTarget = renderer.getRenderTarget();
    const previousAlpha = renderer.getClearAlpha();
    renderer.getClearColor(this.oldClear);
    renderer.setClearColor(0x000000, 0);

    renderer.setRenderTarget(this.rtA);
    renderer.clear();
    const override = scene.overrideMaterial;
    scene.overrideMaterial = this.heightMaterial;
    try {
      renderer.render(scene, this.camera);
    } finally {
      scene.overrideMaterial = override;
    }

    // Two separable passes, horizontal then vertical, A -> B -> A, twice.
    const step = this.blurMaterial.uniforms.uStep.value as THREE.Vector2;
    for (let i = 0; i < 2; i++) {
      this.blurMaterial.uniforms.tInput.value = this.rtA.texture;
      step.set(this.blurStep, 0);
      renderer.setRenderTarget(this.rtB);
      this.quad.render(renderer);
      this.blurMaterial.uniforms.tInput.value = this.rtB.texture;
      step.set(0, this.blurStep);
      renderer.setRenderTarget(this.rtA);
      this.quad.render(renderer);
    }

    renderer.setRenderTarget(previousTarget);
    renderer.setClearColor(this.oldClear, previousAlpha);
    this.ready = true;
  }

  dispose(): void {
    this.rtA.dispose();
    this.rtB.dispose();
    this.quad.dispose();
    this.heightMaterial.dispose();
    this.blurMaterial.dispose();
    this.display.dispose();
    this.mesh.geometry.dispose();
  }
}
