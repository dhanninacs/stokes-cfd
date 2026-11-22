/**
 * Which node "Edit sketch…" opens (ADR-0036): a drawn `implicit_sketch` itself, or the drawn sketch
 * whose output a picked part is. Pure: the tests run in Node.
 */
import type { NodeSummary } from "../api/types";

/** Whether `node` is a sketch operation with a drawn (custom) profile. */
export function isDrawnSketch(node: NodeSummary | null | undefined): boolean {
  return node !== null && node !== undefined && node.type === "implicit_sketch" && node.params.profile === "custom";
}

/** The drawn sketch `node` is or was made by, or null. */
export function drawnSketchOf(tree: NodeSummary | null, node: NodeSummary | null): NodeSummary | null {
  if (node === null) return null;
  if (isDrawnSketch(node)) return node;
  if (node.type !== "part" || tree === null) return null;
  const stack: NodeSummary[] = [tree];
  while (stack.length > 0) {
    const at = stack.pop() as NodeSummary;
    const outputs = at.params.outputs;
    if (isDrawnSketch(at) && Array.isArray(outputs) && outputs.includes(node.id)) return at;
    if (at.children) stack.push(...at.children);
  }
  return null;
}
