/**
 * The 2D mode as the viewport reads it (ADR-0031): which plane the project is meshed in, and the camera that looks at
 * it face-on. Pure: the tree in, numbers out. The mode is the mesh settings' `two_d` and `plane`, which the server
 * owns (`mesh.configure`); nothing here decides it.
 *
 * In Results a 2D project is shown in an orthographic view along the plane's normal with rotation off, so the
 * picture is the 2D field and a stray drag cannot turn it into a view of the one-cell-thick slab's edge.
 */
import type { NodeSummary } from "../api/types";

export type TwoDMode = "planar" | "axisymmetric";
export type TwoDPlane = "xy" | "xz" | "yz";

export interface TwoD {
  mode: TwoDMode;
  plane: TwoDPlane;
}

export const PLANES: readonly TwoDPlane[] = ["xy", "xz", "yz"];

type Vec = readonly [number, number, number];

function find(node: NodeSummary | null, path: string): NodeSummary | null {
  if (node === null) return null;
  if (node.path === path) return node;
  for (const child of node.children ?? []) {
    if (path === child.path || path.startsWith(`${child.path}/`)) return find(child, path);
  }
  return null;
}

/** The mesh settings' 2D mode, or null when the project is 3D (or has no mesh settings). */
export function twoDOf(settings: NodeSummary | null | undefined): TwoD | null {
  const mode = settings?.params.two_d;
  if (mode !== "planar" && mode !== "axisymmetric") return null;
  const raw = settings?.params.plane;
  const plane: TwoDPlane = raw === "xz" || raw === "yz" ? raw : "xy";
  return { mode, plane };
}

/** The project's 2D mode, read from `/mesh`'s settings node in the tree. */
export function twoDOfTree(tree: NodeSummary | null): TwoD | null {
  const settings = find(tree, "/mesh")?.children?.find((c) => c.type === "mesh_settings") ?? null;
  return twoDOf(settings);
}

/**
 * The camera for a plane: `from` is the direction the camera sits in (the view looks back along it), `up` the screen's
 * up. The plane's first axis runs across the screen and its second up it, as a 2D drawing of that plane would be
 * laid out (x right and y up for `xy`).
 */
export function faceOnView(plane: TwoDPlane): { from: Vec; up: Vec } {
  switch (plane) {
    case "xz":
      // Looking along +y from -y keeps x to the right with z up.
      return { from: [0, -1, 0], up: [0, 0, 1] };
    case "yz":
      return { from: [1, 0, 0], up: [0, 0, 1] };
    case "xy":
    default:
      return { from: [0, 0, 1], up: [0, 1, 0] };
  }
}

/** The plane's label for a control: "XY", "XZ", "YZ". */
export function planeLabel(plane: TwoDPlane): string {
  return plane.toUpperCase();
}
