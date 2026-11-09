import type { NodeSummary } from "../api/types";
import { buildOutline, outlineRowsFor, resolveIn, type OutlineNode } from "./outlineModel";

/** Resolve synthetic outline pages as well as decorated model nodes for the Details pane. */
export function findDetailsRow(
  root: NodeSummary | null,
  id: string | null,
  layout: string,
  expanded: Record<string, boolean>,
  meshers: readonly { id: string; label: string }[] = [],
  hidden: readonly string[] = [],
): NodeSummary | null {
  if (root === null || id === null) return null;
  const visit = (node: NodeSummary): NodeSummary | null => {
    if (node.id === id) return node;
    for (const child of node.children ?? []) {
      const hit = visit(child);
      if (hit !== null) return hit;
    }
    return null;
  };
  const real = visit(root);
  if (real !== null || layout !== "outline") return real;
  // The selected page survives folding its parent or clearing a tree search.
  const complete = { ...expanded };
  const expand = (node: OutlineNode): void => {
    complete[node.id] = true;
    node.children?.forEach(expand);
  };
  for (const section of buildOutline(root, resolveIn(root), hidden).sections) {
    complete[section.id] = true;
    section.rows.forEach(expand);
  }
  return outlineRowsFor(root, complete, "", meshers, hidden).find((row) => row.node.id === id)?.node ?? null;
}
