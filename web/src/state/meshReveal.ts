/**
 * Finding a mesh that has been made, as pure functions over the node tree and the stage.
 *
 * Two things a finished mesh needs and did not get. The tree: `mesh/results` shows the count of its
 * results, but it is folded, and so is the result under it that holds the `mesh_patch` rows, so a person
 * (or a script) that has just meshed sees a number and no row to look at. And the viewport: the mesh is
 * drawn only in the Mesh stage, and only for patches that are visible, so a mesh can exist with nothing
 * showing it. `newMeshResults` and `revealIds` open the tree to the patches when a result first gets
 * them; `meshOnScreen` says whether the viewport is drawing the mesh at all, which the Mesh panel's
 * "Show mesh" action answers.
 *
 * Import rule: no React, no store, no DOM. Types only from other modules, so this loads in Node.
 */

import type { NodeSummary } from "../api/types";
import { meshPatchesOf, meshShown, type MeshPatchEntry } from "./meshPatches";
import type { Stage } from "./store";

/** The ids of the `mesh_result` nodes that have at least one patch, in tree order. */
export function resultsWithPatches(tree: NodeSummary | null): string[] {
  const ids: string[] = [];
  for (const entry of meshPatchesOf(tree)) if (!ids.includes(entry.resultId)) ids.push(entry.resultId);
  return ids;
}

/**
 * The results that have patches now and had none when `seen` was taken. `seen` is null before the first
 * tree has been looked at: a project that opens with its mesh already there is not a mesh that has just
 * been made, and its tree is left as the stage opened it.
 */
export function newMeshResults(tree: NodeSummary | null, seen: ReadonlySet<string> | null): string[] {
  if (seen === null) return [];
  return resultsWithPatches(tree).filter((id) => !seen.has(id));
}

/**
 * The nodes to open so that the patches of `resultIds` have rows: the result itself and every group
 * above it down from `/mesh/results`, and `/mesh` too in the Mesh stage (any other stage has folded it
 * on purpose, and it opens with the stage). Only nodes that are not open already are returned.
 */
export function revealIds(tree: NodeSummary | null, resultIds: readonly string[], stage: Stage, expanded: Readonly<Record<string, boolean>>): string[] {
  const group = tree?.children?.find((child) => child.path === "/mesh");
  if (!group || resultIds.length === 0) return [];
  const chain: string[] = [];
  const walk = (node: NodeSummary, trail: NodeSummary[]): void => {
    for (const child of node.children ?? []) {
      if (resultIds.includes(child.id)) {
        for (const open of [...trail, child]) if (!chain.includes(open.id)) chain.push(open.id);
      } else {
        walk(child, [...trail, child]);
      }
    }
  };
  walk(group, []);
  if (chain.length === 0) return []; // none of the results is in the tree
  if (stage === "Mesh") chain.unshift(group.id);
  return chain.filter((id) => expanded[id] !== true);
}

/**
 * Whether the viewport is drawing the mesh now: there are patches, the stage draws them (`meshShown`)
 * and at least one of them is visible. False with a finished mesh is what the panel's "Show mesh" is for.
 */
export function meshOnScreen(stage: Stage, entries: readonly MeshPatchEntry[], resultsOn: boolean): boolean {
  return meshShown(stage, entries.length > 0, resultsOn) && entries.some((entry) => entry.visible);
}

/** The ids of the patches switched off with their eye: "Show mesh" turns them on again. */
export function hiddenPatchIds(entries: readonly MeshPatchEntry[]): string[] {
  return entries.filter((entry) => !entry.visible).map((entry) => entry.id);
}
