/**
 * A small photo studio used as the image-based light of the viewport.
 *
 * A pipe along X mirrors long, thin lights as bright lines running down its length, and the dark
 * gaps between them as shaded bands. That contrast is what makes a surface read as metal. The
 * studio therefore has strip lights parallel to X at three heights and sides over a dark floor
 * and a dim dome, which gives a gradient on any shape, not just on pipes. Colours are linear and
 * go above 1, the cube map it is rendered into is floating point.
 */

import * as THREE from "three";

export type Rgb = [number, number, number];

export interface Panel {
  /** Width along X and height across, in studio units. */
  size: [number, number];
  position: [number, number, number];
  /** Linear RGB, may exceed 1. */
  color: Rgb;
}

export interface StudioConfig {
  dome: { top: Rgb; horizon: Rgb; bottom: Rgb };
  panels: Panel[];
}

export const STUDIO: StudioConfig = {
  dome: { top: [0.6, 0.64, 0.7], horizon: [0.22, 0.24, 0.27], bottom: [0.03, 0.032, 0.036] },
  panels: [
    // Big overhead softbox, the main light.
    { size: [44, 14], position: [0, 34, 6], color: [2.4, 2.45, 2.55] },
    // Strip high in front: the bright line along the upper front of a pipe.
    { size: [48, 3.2], position: [0, 24, 20], color: [7, 7, 7.2] },
    // Strip low in front: a second, softer line below it.
    { size: [48, 2.4], position: [0, 4, 28], color: [2.4, 2.4, 2.5] },
    // Cool rim strip behind and above: separates the far edge from the background.
    { size: [48, 3], position: [0, 14, -28], color: [2.2, 3.2, 4.8] },
    // Warm low strip at the back, so shadowed sides still hold a hint of colour.
    { size: [48, 2], position: [0, 1, -30], color: [1.3, 1.0, 0.8] },
    // End lights: flat faces that look along X would otherwise reflect only the floor.
    { size: [10, 14], position: [-32, 10, 0], color: [3.2, 3.3, 3.5] },
    { size: [10, 14], position: [32, 10, 0], color: [3.0, 3.1, 3.4] },
  ],
};

const DOME_VERTEX = /* glsl */ `
  varying vec3 vDir;
  void main() {
    vDir = normalize(position);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const DOME_FRAGMENT = /* glsl */ `
  uniform vec3 uTop;
  uniform vec3 uHorizon;
  uniform vec3 uBottom;
  varying vec3 vDir;
  void main() {
    float h = vDir.y;
    vec3 color = h > 0.0
      ? mix(uHorizon, uTop, pow(h, 0.55))
      : mix(uHorizon, uBottom, pow(-h, 0.4));
    gl_FragColor = vec4(color, 1.0);
  }
`;

export function createStudio(config: StudioConfig = STUDIO): THREE.Scene {
  const scene = new THREE.Scene();

  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(60, 48, 24),
    new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      uniforms: {
        uTop: { value: new THREE.Color(...config.dome.top) },
        uHorizon: { value: new THREE.Color(...config.dome.horizon) },
        uBottom: { value: new THREE.Color(...config.dome.bottom) },
      },
      vertexShader: DOME_VERTEX,
      fragmentShader: DOME_FRAGMENT,
    }),
  );
  scene.add(dome);

  for (const panel of config.panels) {
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(panel.size[0], panel.size[1]),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color(...panel.color),
        side: THREE.DoubleSide,
        toneMapped: false,
      }),
    );
    mesh.position.set(...panel.position);
    mesh.lookAt(0, 0, 0);
    scene.add(mesh);
  }
  return scene;
}

/** Frees the geometries and materials of a studio made by `createStudio`. */
export function disposeStudio(scene: THREE.Scene): void {
  scene.traverse((object) => {
    if (object instanceof THREE.Mesh) {
      object.geometry.dispose();
      (object.material as THREE.Material).dispose();
    }
  });
}
