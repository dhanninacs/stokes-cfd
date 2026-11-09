/**
 * The orientation gizmo (the view cube): three.js's ViewHelper with labelled axes in the scene's own
 * axis colours and ghost dots for the negative axes that stay visible on both themes. It is drawn in
 * the corner of the canvas the workspace puts it in, bottom right by default, or not at all when the
 * workspace hides it (`setPlacement`).
 *
 * ViewHelper swings the camera round at a fixed turn rate when an axis is clicked. The rig animates
 * every framing move with the same eased fit, so a click here only reports where the camera should
 * look from; the helper's own animation is run to its end in one step to learn that direction, and
 * the camera is put back.
 */

import * as THREE from "three";
import { ViewHelper } from "three/addons/helpers/ViewHelper.js";
import { DEFAULT_GIZMO, type GizmoPlacement } from "../workspace/gizmoSlot";
import type { ProjectionCamera } from "./camera";
import type { SceneTheme } from "./theme";

/** Size of the helper in CSS pixels; fixed inside ViewHelper. */
export const GIZMO_SIZE = 128;
const FONT = '600 22px "Inter Variable", system-ui, "Segoe UI", sans-serif';
/** Letter colour and disc radius of the axis labels, in the helper's 64 px sprite textures. */
const LABEL_INK = "#0b0d10";
const LABEL_RADIUS = 15;

type Axis = "X" | "Y" | "Z";

export class ViewGizmo {
  private helper: ViewHelper;
  private ghost: THREE.SpriteMaterial[] = [];
  private disposed = false;
  /** A corner of the canvas and the distances from its two edges, in CSS px; null: not drawn. */
  private placement: GizmoPlacement | null = DEFAULT_GIZMO;

  /**
   * @param center the point the camera orbits, shared with the controls so the helper turns round it
   */
  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly center: THREE.Vector3,
    camera: ProjectionCamera,
    private theme: SceneTheme,
  ) {
    this.helper = this.build(camera);
    // The labels are drawn into a texture, so they need the page font to be there already.
    void document.fonts?.ready.then(() => {
      if (!this.disposed) this.styleLabels(this.helper);
    });
  }

  /** The helper is bound to one camera, so a projection switch makes a new one. */
  setCamera(camera: ProjectionCamera): void {
    this.helper.dispose();
    this.ghost = []; // disposed together with the helper's own sprites
    this.helper = this.build(camera);
  }

  setTheme(theme: SceneTheme): void {
    this.theme = theme;
    this.styleAxes(this.helper);
    this.styleGhostDots(this.helper);
  }

  /** Where the 128 px square goes, or null to hide the gizmo. */
  setPlacement(placement: GizmoPlacement | null): void {
    this.placement = placement;
    this.locate(this.helper);
  }

  /** Whether a client position lies on the gizmo's square (never while it is hidden). */
  contains(clientX: number, clientY: number): boolean {
    const place = this.placement;
    if (!place) return false;
    const rect = this.canvas.getBoundingClientRect();
    const left = place.corner[1] === "l" ? rect.left + place.x : rect.right - GIZMO_SIZE - place.x;
    const top = place.corner[0] === "t" ? rect.top + place.y : rect.bottom - GIZMO_SIZE - place.y;
    return clientX >= left && clientX <= left + GIZMO_SIZE && clientY >= top && clientY <= top + GIZMO_SIZE;
  }

  render(renderer: THREE.WebGLRenderer): void {
    if (!this.placement) return;
    // **The section plane is the renderer's, so it cut the cube too**: with a Results slice on (which turns
    // the section on to expose it) half the axes vanished and only X and -Y were left to click (elbow
    // walkthrough, 2026-11-08). The cube is drawn without it and the plane is put back.
    const planes = renderer.clippingPlanes;
    renderer.clippingPlanes = [];
    try {
      this.helper.render(renderer);
    } finally {
      renderer.clippingPlanes = planes;
    }
  }

  /**
   * The direction from the orbit centre to the camera that the clicked axis dot stands for, or
   * null when the click missed every dot. The camera is left as it was.
   */
  pickDirection(event: PointerEvent, camera: ProjectionCamera): THREE.Vector3 | null {
    const helper = this.helper;
    const position = camera.position.clone();
    const quaternion = camera.quaternion.clone();
    if (!helper.handleClick(event)) return null;
    for (let i = 0; i < 4 && helper.animating; i++) helper.update(1e3);
    helper.animating = false;
    const dir = camera.position.clone().sub(this.center).normalize();
    camera.position.copy(position);
    camera.quaternion.copy(quaternion);
    return dir;
  }

  dispose(): void {
    this.disposed = true;
    this.helper.dispose();
  }

  private build(camera: ProjectionCamera): ViewHelper {
    const helper = new ViewHelper(camera, this.canvas);
    helper.center = this.center;
    this.locate(helper);
    this.styleLabels(helper);
    this.styleGhostDots(helper);
    return helper;
  }

  /** The helper measures its square from the canvas edges of the corner: left or right, top or bottom. */
  private locate(helper: ViewHelper): void {
    const place = this.placement;
    if (!place) return;
    helper.location = {
      left: place.corner[1] === "l" ? place.x : null,
      right: place.corner[1] === "r" ? place.x : 0,
      top: place.corner[0] === "t" ? place.y : null,
      bottom: place.corner[0] === "b" ? place.y : 0,
    };
  }

  /** Sets the label font and letters. Setting them makes the helper redraw its sprites, so the axis colours follow. */
  private styleLabels(helper: ViewHelper): void {
    helper.setLabelStyle(FONT, LABEL_INK, LABEL_RADIUS);
    helper.setLabels("X", "Y", "Z");
    this.styleAxes(helper);
  }

  /**
   * The helper paints its axes in a fixed pink, lime and blue that match nothing else on screen.
   * Here X and Z take the colours of the floor's axis lines and Y the green of the status dots,
   * from the theme. The helper keeps its colours in closures, so the three axis shafts are
   * recoloured through their materials and the three positive discs are drawn again, with the
   * same radius, font and letter as the helper's own, in the new colour.
   */
  private styleAxes(helper: ViewHelper): void {
    const colour: Record<Axis, string> = { X: this.theme.axisX.color, Y: this.theme.axisY, Z: this.theme.axisZ.color };
    for (const child of helper.children) {
      if (child instanceof THREE.Mesh) {
        // The helper turns Y's shaft about z and Z's about y; X's is not turned.
        const axis: Axis = child.rotation.z !== 0 ? "Y" : child.rotation.y !== 0 ? "Z" : "X";
        (child.material as THREE.MeshBasicMaterial).color.set(colour[axis]);
      } else if (child instanceof THREE.Sprite) {
        const type = String(child.userData.type);
        if (!type.startsWith("pos")) continue;
        const axis = type.slice(3) as Axis;
        const old = child.material;
        child.material = this.axisDisc(colour[axis], axis);
        old.map?.dispose();
        old.dispose();
      }
    }
  }

  /** A filled disc with the axis letter on it, as ViewHelper draws one, in the given colour. */
  private axisDisc(colour: string, letter: Axis): THREE.SpriteMaterial {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 64;
    const context = canvas.getContext("2d");
    if (context) {
      context.beginPath();
      context.arc(32, 32, LABEL_RADIUS, 0, Math.PI * 2);
      context.closePath();
      context.fillStyle = colour;
      context.fill();
      context.font = FONT;
      context.textAlign = "center";
      context.fillStyle = LABEL_INK;
      context.fillText(letter, 32, 41);
    }
    const map = new THREE.CanvasTexture(canvas);
    map.colorSpace = THREE.SRGBColorSpace;
    return new THREE.SpriteMaterial({ map, toneMapped: false });
  }

  /**
   * The helper draws its negative axes as black dots at 20 %, which vanish on a dark background, and
   * leaves them unlabelled, so the back, left and bottom views were guesses. Each one is drawn here as
   * a ring in its axis colour with "-X", "-Y" or "-Z" on it: every face of the box is one labelled click.
   */
  private styleGhostDots(helper: ViewHelper): void {
    const colour: Record<Axis, string> = { X: this.theme.axisX.color, Y: this.theme.axisY, Z: this.theme.axisZ.color };
    const fill = this.theme.isDark ? "rgba(20, 24, 30, 0.82)" : "rgba(250, 251, 253, 0.9)";
    const ink = this.theme.isDark ? "#e6ebf2" : "#1c2633";
    const made: THREE.SpriteMaterial[] = [];
    const previous = this.ghost;
    for (const child of helper.children) {
      if (!(child instanceof THREE.Sprite)) continue;
      const type = String(child.userData.type);
      if (!type.startsWith("neg")) continue;
      const axis = type.slice(3) as Axis;
      const canvas = document.createElement("canvas");
      canvas.width = 64;
      canvas.height = 64;
      const context = canvas.getContext("2d");
      if (!context) continue;
      context.beginPath();
      context.arc(32, 32, LABEL_RADIUS - 1, 0, Math.PI * 2);
      context.fillStyle = fill;
      context.fill();
      context.lineWidth = 3;
      context.strokeStyle = colour[axis];
      context.stroke();
      context.font = '600 15px "Inter Variable", system-ui, "Segoe UI", sans-serif';
      context.textAlign = "center";
      context.fillStyle = ink;
      context.fillText(`-${axis}`, 32, 37);
      const map = new THREE.CanvasTexture(canvas);
      map.colorSpace = THREE.SRGBColorSpace;
      const material = new THREE.SpriteMaterial({ map, toneMapped: false });
      made.push(material);
      const old = child.material;
      child.material = material;
      if (!previous.includes(old)) {
        // The helper's own material: nothing refers to it any more.
        old.map?.dispose();
        old.dispose();
      }
    }
    for (const old of previous) {
      old.map?.dispose();
      old.dispose();
    }
    this.ghost = made;
  }
}
