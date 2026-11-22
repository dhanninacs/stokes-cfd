/**
 * How bodies and the operations that make and read them hang together (ADR-0035, ADR-0036), read off
 * the tree: an operation lists the parts it reads in `inputs` (and a combine's cutters in `tools`) and
 * the parts it made in `outputs`; a made part names its operation in `derived`.
 *
 * Four questions the UI asks of that graph:
 * - which operation made a body (double-click a body, "Edit <operation>");
 * - which operations and bodies are built from a body, so deleting it can say what goes with it
 *   instead of leaving a stale history behind (review finding A7);
 * - which bodies are steps another operation consumed, so the tree keeps them under that operation
 *   rather than in the bodies list (review finding B8);
 * - which body a transform or a combine acts on (the target) and which act on it (the tools).
 *
 * Pure functions over `NodeSummary`: the tests run in Node.
 */
import type { NodeSummary } from "../api/types";

function ids(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** Every node of the tree, depth first. */
function nodes(root: NodeSummary | null): NodeSummary[] {
  const out: NodeSummary[] = [];
  const stack: NodeSummary[] = root === null ? [] : [root];
  while (stack.length > 0) {
    const at = stack.pop() as NodeSummary;
    out.push(at);
    if (at.children) for (let i = at.children.length - 1; i >= 0; i--) stack.push(at.children[i]);
  }
  return out;
}

/** Whether `node` is a geometry operation: it has an `outputs` list (every operation node does). */
export function isOperationNode(node: NodeSummary | null | undefined): boolean {
  return node !== null && node !== undefined && Array.isArray(node.params?.outputs) && node.path.startsWith("/geometry/operations/");
}

/** The parts an operation reads: its inputs, then a combine's tools. */
export function operationReads(op: NodeSummary): string[] {
  return [...ids(op.params.inputs), ...ids(op.params.tools)];
}

/** The operation that made `partId`, or null for an imported part (or one whose operation is gone). */
export function operationOf(tree: NodeSummary | null, partId: string): NodeSummary | null {
  const all = nodes(tree);
  const part = all.find((n) => n.id === partId);
  const derived = typeof part?.params.derived === "string" ? part.params.derived : "";
  if (derived !== "") {
    const op = all.find((n) => n.id === derived);
    if (op !== undefined) return op;
  }
  return all.find((n) => isOperationNode(n) && ids(n.params.outputs).includes(partId)) ?? null;
}

/** What is built from a node: the operations that read it (directly or through their outputs) and the bodies they made. */
export interface Dependents {
  /** Operations, upstream first: deleting them in reverse order never leaves one reading a gone part. */
  operations: NodeSummary[];
  /** The bodies those operations made. */
  bodies: NodeSummary[];
}

/**
 * Everything built from `partId`, transitively: the operations that read it, the bodies they made,
 * the operations that read those, and so on. The part's own maker is not a dependent.
 */
export function dependentsOf(tree: NodeSummary | null, partId: string): Dependents {
  const all = nodes(tree);
  const ops = all.filter(isOperationNode);
  const byId = new Map(all.map((n) => [n.id, n] as const));
  const operations: NodeSummary[] = [];
  const bodies: NodeSummary[] = [];
  const seen = new Set<string>();
  let frontier = [partId];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const op of ops) {
      if (seen.has(op.id) || !operationReads(op).some((id) => frontier.includes(id))) continue;
      seen.add(op.id);
      operations.push(op);
      for (const out of ids(op.params.outputs)) {
        const body = byId.get(out);
        if (body !== undefined && !bodies.includes(body)) bodies.push(body);
        next.push(out);
      }
    }
    frontier = next;
  }
  return { operations, bodies };
}

/** The operation that consumed `part` as a step (an implicit part an implicit operation reads), or null. */
export function consumerOf(tree: NodeSummary | null, part: NodeSummary): NodeSummary | null {
  if (part.type !== "part" || part.params.family !== "implicit") return null;
  return nodes(tree).find((n) => isOperationNode(n) && n.type.startsWith("implicit_") && operationReads(n).includes(part.id)) ?? null;
}

/**
 * The geometry group with each consumed step moved from the parts list to under the operation that
 * consumed it, so the bodies list holds the bodies the user is building and each operation keeps
 * its inputs. A part read by several operations stays under the first. Untouched when nothing moves.
 */
export function nestConsumedSteps(geometry: NodeSummary, tree: NodeSummary | null): NodeSummary {
  const kids = geometry.children ?? [];
  const parts = kids.find((c) => c.path === "/geometry/parts");
  const operations = kids.find((c) => c.path === "/geometry/operations");
  if (parts === undefined || operations === undefined || parts.children === undefined) return geometry;
  const under = new Map<string, NodeSummary[]>();
  const kept: NodeSummary[] = [];
  for (const part of parts.children) {
    const op = consumerOf(tree, part);
    if (op === null) kept.push(part);
    else under.set(op.id, [...(under.get(op.id) ?? []), part]);
  }
  if (under.size === 0) return geometry;
  const ops = (operations.children ?? []).map((op) => {
    const steps = under.get(op.id);
    return steps === undefined ? op : { ...op, children: [...(op.children ?? []), ...steps], children_count: (op.children?.length ?? 0) + steps.length };
  });
  return {
    ...geometry,
    children: kids.map((c) => (c === parts ? { ...parts, children: kept, children_count: kept.length } : c === operations ? { ...operations, children: ops } : c)),
  };
}

/** The bodies a transform or a combine acts on and with, in the order the run reads them. */
export interface TargetTools {
  /** The body that is changed and keeps its place (Move's body, Subtract's "cut from"). */
  target: string | null;
  /** The bodies that act on it (Subtract's cutters); empty for one-body operations. */
  tools: string[];
}

/** The target and the tools of a pick, for `type`; null for a type that reads no body. */
export function targetAndTools(type: string, parts: readonly string[]): TargetTools | null {
  if (type === "implicit_primitive" || type === "implicit_sketch") return null;
  if (parts.length === 0) return { target: null, tools: [] };
  return { target: parts[0], tools: type === "implicit_boolean" || type === "boolean" ? parts.slice(1) : [] };
}

/** The pick with its target swapped: the first tool becomes the target. */
export function swapTarget(parts: readonly string[]): string[] {
  if (parts.length < 2) return [...parts];
  return [parts[1], parts[0], ...parts.slice(2)];
}

/** "Move and Subtract", "Move, Pattern and Subtract": the names of a list for a sentence. */
export function sayList(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** What the target and the tools of a combine are called, by its operation. */
const ROLE_WORDS: Record<string, readonly [string, string]> = {
  difference: ["Cut from", "Cut with"],
  union: ["Body", "Joined with"],
  intersection: ["Body", "Kept where it meets"],
};

/** The card's words for the target and the tools of an operation of `type` (a combine's by its `op`). */
export function roleWords(type: string, op: unknown): readonly [string, string] {
  if (type !== "implicit_boolean") return ["Body", ""];
  return ROLE_WORDS[typeof op === "string" ? op : ""] ?? ["Body", "With"];
}

/**
 * Whether an Enter that reached the operation card from `target` runs it: an Enter in a field of
 * the form does; one on a button (which presses the button) or a switch does not.
 */
export function enterRunsCard(target: { tagName?: string; getAttribute?(name: string): string | null; closest?(selector: string): unknown } | null): boolean {
  if (target === null || typeof target.tagName !== "string") return false;
  if (target.tagName === "BUTTON" || target.tagName === "TEXTAREA" || target.getAttribute?.("role") === "switch") return false;
  return (target.closest?.(".operation-card-body") ?? null) !== null;
}

/** What deleting a node takes with it, so nothing is left reading a body that is gone (review finding A7). */
export interface RemovalPlan {
  /** Operations built from the node, upstream first; they are deleted downstream first. */
  operations: NodeSummary[];
  /** The bodies those operations made, which go with them. */
  bodies: NodeSummary[];
  /**
   * The operation to delete in place of the node itself: the node when it is an operation, or the
   * operation that made a body whose only output it is (deleting the body alone left that operation
   * behind, stale). Null when the node goes by itself.
   */
  maker: NodeSummary | null;
}

/** What deleting `node` takes with it; null when it goes alone, as any other node does. */
export function removalPlan(tree: NodeSummary | null, node: NodeSummary): RemovalPlan | null {
  let roots: string[];
  let maker: NodeSummary | null = null;
  if (node.type === "part") {
    roots = [node.id];
    const made = operationOf(tree, node.id);
    const outputs = made === null ? [] : ids(made.params.outputs);
    if (made !== null && outputs.length === 1 && outputs[0] === node.id) maker = made;
  } else if (isOperationNode(node)) {
    roots = ids(node.params.outputs);
    maker = node;
  } else {
    return null;
  }
  const operations: NodeSummary[] = [];
  const bodies: NodeSummary[] = [];
  for (const root of roots) {
    const found = dependentsOf(tree, root);
    for (const op of found.operations) if (!operations.includes(op) && op !== maker) operations.push(op);
    for (const body of found.bodies) if (!bodies.includes(body)) bodies.push(body);
  }
  if (operations.length === 0 && maker === null) return null;
  return { operations, bodies, maker };
}

/** The question the delete asks when other operations are built from the node; null when none are. */
export function removalQuestion(node: NodeSummary, plan: RemovalPlan): { title: string; body: string; confirmLabel: string } | null {
  if (plan.operations.length === 0) return null;
  const opWords = plan.operations.map((op) => op.name);
  const bodyWords = plan.bodies.map((b) => b.name);
  const n = plan.operations.length;
  return {
    title: `Delete ${node.name}?`,
    body:
      `${sayList(opWords)} ${n === 1 ? "is" : "are"} built from ${node.name}. Deleting it also deletes ` +
      `${n === 1 ? "that operation" : `those ${n} operations`}` +
      (bodyWords.length > 0 ? ` and the ${bodyWords.length === 1 ? "body" : "bodies"} ${sayList(bodyWords)}.` : ".") +
      " Undo brings everything back.",
    confirmLabel: n === 1 ? "Delete both" : `Delete all ${n + 1}`,
  };
}
