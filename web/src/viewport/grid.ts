/**
 * Ground grid: a shader on one large plane just under the part.
 *
 * Minor and major lines are antialiased with the screen-space derivative of the world
 * coordinate, so they stay one pixel wide at any distance and angle, and a level fades out when
 * its cells shrink to a few pixels instead of turning into moire. Everything fades radially from
 * the centre of the part, so the grid reads as a pool of floor, not as an infinite sheet.
 * Line spacing follows the size of the part (1-2-5 steps), the major lines are every tenth.
 */

import * as THREE from "three";
import type { LineStyle, SceneTheme } from "./theme";

const VERTEX = /* glsl */ `
  varying vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const FRAGMENT = /* glsl */ `
  uniform vec3 uMinor;
  uniform vec3 uMajor;
  uniform vec3 uAxisX;
  uniform vec3 uAxisZ;
  uniform vec3 uFloor;
  uniform vec4 uAlpha;      // minor, major, axis x, axis z
  uniform float uFloorAlpha;
  uniform float uSpacing;   // minor spacing in world units
  uniform vec2 uCenter;     // centre of the falloff, world xz
  uniform float uRadius;    // distance at which the grid has faded out
  uniform float uPixelRatio;
  varying vec3 vWorld;

  // Coverage of the lines of a regular grid, antialiased over one pixel. The two line families
  // fade out on their own when their spacing on screen drops below a few pixels, so a grid seen
  // at a grazing angle keeps the lines that still have room and loses only the crowded ones.
  float gridLines(vec2 p, float spacing, float widthPx) {
    vec2 coord = p / spacing;
    vec2 d = max(fwidth(coord), vec2(1e-7));
    vec2 dist = abs(fract(coord - 0.5) - 0.5) / d;   // pixels to the nearest line, per family
    vec2 cover = clamp(0.5 * widthPx + 0.5 - dist, 0.0, 1.0);
    vec2 room = clamp((1.0 / d - 4.0) / 8.0, 0.0, 1.0);   // pixels per cell, per family
    return max(cover.x * room.x, cover.y * room.y);
  }

  float axisLine(float coord, float widthPx) {
    float d = max(fwidth(coord), 1e-7);
    return clamp(0.5 * widthPx + 0.5 - abs(coord) / d, 0.0, 1.0);
  }

  // Straight-alpha "over" so the layers combine without a render pass per layer.
  vec4 over(vec4 dst, vec3 color, float alpha) {
    float a = alpha + dst.a * (1.0 - alpha);
    vec3 c = a > 1e-5 ? (color * alpha + dst.rgb * dst.a * (1.0 - alpha)) / a : vec3(0.0);
    return vec4(c, a);
  }

  void main() {
    vec2 p = vWorld.xz;
    float px = uPixelRatio;
    float r = length(p - uCenter);
    float fall = 1.0 - smoothstep(0.1 * uRadius, uRadius, r);
    float floorFall = 1.0 - smoothstep(0.0, 0.75 * uRadius, r);
    floorFall *= floorFall;

    vec4 color = vec4(uFloor, uFloorAlpha * floorFall);
    color = over(color, uMinor, uAlpha.x * fall * gridLines(p, uSpacing, 1.0 * px));
    color = over(color, uMajor, uAlpha.y * fall * gridLines(p, uSpacing * 10.0, 1.3 * px));
    color = over(color, uAxisX, uAlpha.z * fall * axisLine(p.y, 1.8 * px));
    color = over(color, uAxisZ, uAlpha.w * fall * axisLine(p.x, 1.8 * px));
    if (color.a < 0.002) discard;
    gl_FragColor = color;
    #include <colorspace_fragment>
  }
`;

/** 1-2-5 series value at or just below `target`. */
export function niceSpacing(target: number): number {
  if (!(target > 0)) return 1;
  const exponent = Math.floor(Math.log10(target));
  const base = Math.pow(10, exponent);
  const fraction = target / base;
  const step = fraction >= 5 ? 5 : fraction >= 2 ? 2 : 1;
  return step * base;
}

export interface GroundPlacement {
  /** World Y of the ground plane. */
  y: number;
  center: THREE.Vector2;
  extent: number;
}

function setStyle(color: THREE.Color, style: LineStyle): number {
  color.set(style.color);
  return style.alpha;
}

export class GroundGrid {
  readonly mesh: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;
  /** The workspace's `viewport.grid`: off hides the whole ground (lines, axes and the floor tint). */
  private enabled = true;
  /** Whether the red/green/blue axis lines draw; `setAxes`. */
  private axes = true;
  /** The ground has been put under some content (`place`) and not taken away since (`hide`). */
  private placed = false;
  private readonly uniforms = {
    uMinor: { value: new THREE.Color() },
    uMajor: { value: new THREE.Color() },
    uAxisX: { value: new THREE.Color() },
    uAxisZ: { value: new THREE.Color() },
    uFloor: { value: new THREE.Color() },
    uAlpha: { value: new THREE.Vector4() },
    uFloorAlpha: { value: 0 },
    uSpacing: { value: 0.01 },
    uCenter: { value: new THREE.Vector2() },
    uRadius: { value: 1 },
    uPixelRatio: { value: 1 },
  };

  constructor(theme: SceneTheme) {
    const material = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
    });
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2), material);
    this.mesh.name = "ground-grid";
    this.mesh.renderOrder = -2;
    this.mesh.frustumCulled = false;
    this.mesh.visible = false;
    this.setTheme(theme);
  }

  setTheme(theme: SceneTheme): void {
    const u = this.uniforms;
    u.uAlpha.value.set(
      setStyle(u.uMinor.value, theme.gridMinor),
      setStyle(u.uMajor.value, theme.gridMajor),
      setStyle(u.uAxisX.value, theme.axisX),
      setStyle(u.uAxisZ.value, theme.axisZ),
    );
    // **The axis lines' visibility is an OPACITY, not a mesh** (the owner's "Axis lines" switch):
    // `uAlpha.z` and `.w` are what the shader multiplies the red and blue lines by, so zeroing them
    // is how the lines go without a second geometry. Re-applied here because `setTheme` rebuilds all
    // four components on every theme change, which would otherwise turn them back on.
    this.applyAxes();
    u.uFloorAlpha.value = setStyle(u.uFloor.value, theme.floor);
  }

  setPixelRatio(ratio: number): void {
    this.uniforms.uPixelRatio.value = ratio;
  }

  /**
   * Show or hide the red/green/blue axis lines, leaving the ground grid as it is.
   *
   * **A separate switch from the grid** because they answer different questions - "where is the
   * origin" and "how big is this" - and a user who wants a clean ground usually still wants to know
   * which way is X.
   */
  setAxes(on: boolean): void {
    this.axes = on;
    this.applyAxes();
  }

  /** The axis components of `uAlpha`, from the current flag. Called after every `setTheme`. */
  private applyAxes(): void {
    // The colours were set by `setTheme` just before this; only the two axis opacities are zeroed.
    const alpha = this.uniforms.uAlpha.value;
    if (this.axes) return;
    alpha.setZ(0);
    alpha.setW(0);
  }

  setEnabled(on: boolean): void {
    this.enabled = on;
    this.mesh.visible = on && this.placed;
  }

  /** Puts the ground under `box`, sizes the falloff to the part and picks the line spacing. */
  place(box: THREE.Box3): GroundPlacement {
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const extent = Math.max(size.x, size.y, size.z, 1e-6);
    // Just below the lowest point: far enough that depth precision never makes it fight the part.
    const y = box.min.y - extent * 0.004;
    const radius = extent * 2.6;
    this.mesh.position.set(centre.x, y, centre.z);
    this.mesh.scale.set(radius * 2, 1, radius * 2);
    this.uniforms.uCenter.value.set(centre.x, centre.z);
    this.uniforms.uRadius.value = radius;
    this.uniforms.uSpacing.value = niceSpacing(extent / 24);
    this.placed = true;
    this.mesh.visible = this.enabled;
    return { y, center: new THREE.Vector2(centre.x, centre.z), extent };
  }

  hide(): void {
    this.placed = false;
    this.mesh.visible = false;
  }

  dispose(): void {
    this.mesh.geometry.dispose();
    this.mesh.material.dispose();
  }
}
