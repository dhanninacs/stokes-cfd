/**
 * What the front end knows about the boundary patches of a mesh (ADR-0024, T-211), as pure functions
 * over the node tree. The tree is the server's: a finished `mesh_result` (`/mesh/results/<mesher>`) has
 * one `mesh_patch` child per boundary patch, with `visible`, `color` and `opacity` for how it is drawn
 * and `faces` for how many cell faces it holds. The viewport asks for each patch's surface by the node's
 * id (`MeshPatchesController`) and draws it as the scene source `mesh:<id>`.
 *
 * Import rule: no React, no store, no DOM. Types only from other modules, so this loads in Node.
 */

import type { NodeSummary } from "../api/types";
import type { Stage } from "./store";

/** The scene source of a patch is this prefix and the node id; a part's source is its bare node id. */
export const MESH_SOURCE_PREFIX = "mesh:";

/** What the Display menu offers for a mesh: its surface alone, or the surface with the cell edges on it. */
export type MeshDisplay = "surface" | "cells";

export const DEFAULT_PATCH_COLOR = "#9aa3ad";

/** One `mesh_patch` node as the viewport needs it. */
export interface MeshPatchEntry {
  /** The node id: what the server's mesh routes take. */
  id: string;
  /** The `mesh_result` it belongs to. */
  resultId: string;
  name: string;
  visible: boolean;
  /** `#rrggbb`. */
  color: string;
  /** 0 transparent to 1 opaque. */
  opacity: number;
  /** The real count of cell faces (the surface itself carries one face id for the whole patch). */
  faces: number;
}

const HEX = /^#[0-9a-f]{6}$/i;

export function meshSourceOf(id: string): string {
  return MESH_SOURCE_PREFIX + id;
}

/** The node id behind a scene source, or null when the source is not a mesh patch. */
export function meshIdOfSource(source: string | null): string | null {
  return source !== null && source.startsWith(MESH_SOURCE_PREFIX) && source.length > MESH_SOURCE_PREFIX.length ? source.slice(MESH_SOURCE_PREFIX.length) : null;
}

/** True for a scene source that draws a mesh patch rather than a part. */
export function isMeshSource(source: string | null): boolean {
  return meshIdOfSource(source) !== null;
}

function entryOf(node: NodeSummary, resultId: string): MeshPatchEntry {
  const p = node.params;
  const color = typeof p.color === "string" && HEX.test(p.color) ? p.color : DEFAULT_PATCH_COLOR;
  const opacity = typeof p.opacity === "number" && Number.isFinite(p.opacity) ? Math.min(1, Math.max(0, p.opacity)) : 1;
  const faces = typeof p.faces === "number" && Number.isFinite(p.faces) ? Math.max(0, Math.trunc(p.faces)) : 0;
  return { id: node.id, resultId, name: node.name, visible: p.visible !== false, color, opacity, faces };
}

/** The `mesh_result` nodes under `/mesh/results`, in tree order: one per mesher (ADR-0034). */
export function meshResultsOf(tree: NodeSummary | null): NodeSummary[] {
  const mesh = tree?.children?.find((child) => child.path === "/mesh");
  const results = mesh?.children?.find((child) => child.path === "/mesh/results");
  return (results?.children ?? []).filter((child) => child.type === "mesh_result");
}

/**
 * The result in use - the mesh the viewport shows and a run solves on (`mesh.use`): the one marked
 * `active`, else the latest, as `active_result` in `stokes/mesh/model.py` decides it. A project saved
 * before one result per mesher was kept holds one result and no mark.
 */
export function activeResultOf(tree: NodeSummary | null): NodeSummary | null {
  const results = meshResultsOf(tree);
  const marked = results.filter((child) => child.params.active === true);
  return marked[marked.length - 1] ?? results[results.length - 1] ?? null;
}

/**
 * The `mesh_patch` nodes of the result in use, in tree order. The other meshers' results are kept
 * (ADR-0034) but not drawn: two meshes of one part on top of each other would be one unreadable picture.
 * Any other node type is ignored, and so is a patch outside a result: only a result's surfaces can be served.
 */
export function meshPatchesOf(tree: NodeSummary | null): MeshPatchEntry[] {
  const active = activeResultOf(tree);
  if (active === null) return [];
  const out: MeshPatchEntry[] = [];
  const walk = (node: NodeSummary): void => {
    for (const child of node.children ?? []) {
      if (child.type === "mesh_patch") out.push(entryOf(child, active.id));
      else walk(child);
    }
  };
  walk(active);
  return out;
}

/**
 * Whether the mesh is on screen: in the Mesh stage, and in the Results stage while a field colours it
 * (`resultsOn`, T-218), and only once there is a patch to draw. This is also when the geometry parts step
 * aside (a view filter in the scene, never a part parameter): with no mesh, the stage keeps showing the
 * geometry.
 */
export function meshShown(stage: Stage, hasPatches: boolean, resultsOn = false): boolean {
  return hasPatches && (stage === "Mesh" || (stage === "Results" && resultsOn));
}

/**
 * Whether the project's mesh is drawn as its results slice alone: an imported OpenFOAM case (a tutorial)
 * has no `mesh_patch` nodes, and the only surface its runs serve is the slice (`run.fields` `slice`).
 */
export function bareMeshOf(patches: number, hasSlice: boolean, isCase = true): boolean {
  return isCase && patches === 0 && hasSlice;
}

/** Whether the project holds an imported OpenFOAM case (a `foam_case` under `/cases`). */
export function hasImportedCase(tree: NodeSummary | null): boolean {
  const group = tree?.children?.find((child) => child.path === "/cases");
  return (group?.children ?? []).some((child) => child.type === "foam_case");
}

/** The two flags the scene's layer filter takes for a stage: what is drawn of the geometry and of the mesh. */
export function layerFilter(stage: Stage, hasPatches: boolean, resultsOn = false, bareMesh = false): { geometry: boolean; mesh: boolean } {
  // An imported case's mesh is its results slice (`bareMesh`): it has no patches and no geometry, so the
  // mesh layer stays on in every stage - otherwise the slice, a mesh source, was never drawn.
  if (bareMesh) return { geometry: true, mesh: true };
  const mesh = meshShown(stage, hasPatches, resultsOn);
  return { geometry: !mesh, mesh };
}

/**
 * Whether selecting a node leaves the stage where it is. A `mesh_patch` row belongs to the Mesh stage, but the
 * Results stage draws the same patches (coloured by a field, T-218): a click on one there selects its row and
 * stays, so the colours are not lost by looking at a patch.
 */
export function selectKeepsStage(stage: Stage, nodeType: string): boolean {
  return stage === "Results" && nodeType === "mesh_patch";
}

/** The name a mesh patch is drawn under: it cannot be a part's, so the selection never confuses the two. */
export function meshPatchName(entry: MeshPatchEntry): string {
  return `${entry.name} (mesh)`;
}

/** What a patch is drawn with, as one string, to tell when it changed without comparing four fields. */
export function patchStyleKey(entry: MeshPatchEntry): string {
  return `${entry.color}|${entry.visible}|${entry.opacity}|${entry.name}`;
}

/** What the controller has to do to bring the scene in step with the tree. */
export interface MeshSyncPlan {
  /** Patches the scene has not asked for yet: fetch them. */
  fetch: string[];
  /** Patches the scene holds that the tree no longer has: take them out. */
  remove: string[];
  /** Patches held whose colour, visibility, opacity or name changed: restyle them, no refetch. */
  restyle: string[];
}

/**
 * Compares the tree's patches with what the scene holds (`held`: node id to the style key it was drawn
 * with). Nothing is refetched for a style change, and a patch already asked for is not asked for twice.
 */
export function planMeshSync(entries: readonly MeshPatchEntry[], held: ReadonlyMap<string, string>): MeshSyncPlan {
  const plan: MeshSyncPlan = { fetch: [], remove: [], restyle: [] };
  const wanted = new Set<string>();
  for (const entry of entries) {
    wanted.add(entry.id);
    const was = held.get(entry.id);
    if (was === undefined) plan.fetch.push(entry.id);
    else if (was !== patchStyleKey(entry)) plan.restyle.push(entry.id);
  }
  for (const id of held.keys()) if (!wanted.has(id)) plan.remove.push(id);
  return plan;
}
