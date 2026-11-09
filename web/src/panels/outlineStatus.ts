/**
 * The outline's state, bound to what the app ACTUALLY knows (G-306).
 *
 * **The model's `outlineStatus` takes a `stateOf` function**; this is that function, built from the real
 * sources rather than a stub - which is the gap Codex named when accepting the model: *"do not call G-305
 * fully accepted based on stub-only status tests"*.
 *
 * Sources, in the order they win:
 *
 * * **`setup.check` findings** - an `error` bound to a node makes that node `todo`, with the finding's own
 *   message as its value text;
 * * **a stale or missing mesh result** - `stale`, which is a different fact from "not done";
 * * **run state** - `running`, `failed`, `done`;
 * * **results** - `ready` once the fields are on screen.
 *
 * **A finding's `nodes` are what bind it.** A finding with no nodes is about the project rather than a row,
 * and it contributes nothing to a row's state - it belongs in the Setup page, not the tree.
 */
import type { NodeSummary, SetupCheck, SetupFinding } from "../api/types";

/** The state a row can be in, as the model defines it. */
export type RowState = "idle" | "todo" | "ready" | "stale" | "running" | "done" | "failed";

/** What the adapter reads. Every field is what the store already holds. */
export interface OutlineStateInput {
  setup: SetupCheck | null;
  /** The run the Solve section follows, or null. */
  run: { id: string; state: string } | null;
  /** True when the mesh result is present but older than the model it describes. */
  meshStale: boolean;
  /** True when a mesh result exists at all. */
  hasMesh: boolean;
  /** True when the results are on screen. */
  resultsShown: boolean;
}

/** The findings bound to each node id, keep the first (most severe, as the server orders them). */
export function findingsByNode(setup: SetupCheck | null): Map<string, SetupFinding> {
  const out = new Map<string, SetupFinding>();
  for (const finding of setup?.findings ?? []) {
    // **A finding with no nodes is about the project**, not a row: it has nowhere to land in the tree.
    if (finding.nodes === undefined) continue;
    for (const id of finding.nodes) if (!out.has(id)) out.set(id, finding);
  }
  return out;
}

/**
 * The state of one node, from the real sources.
 *
 * **The run's own state wins over a finding for that run**: a run that failed says `failed` even where a
 * setup error also names it, because the failure is the newer and more specific fact.
 */
export function stateOfNode(id: string, input: OutlineStateInput, bound: Map<string, SetupFinding>): RowState {
  if (input.run !== null && id === input.run.id) {
    switch (input.run.state) {
      case "running":
        return "running";
      case "failed":
        return "failed";
      case "done":
      case "done_unconverged":
        return "done";
      default:
        break;
    }
  }
  const finding = bound.get(id);
  if (finding !== undefined && finding.severity === "error") return "todo";
  return "idle";
}

/**
 * The whole tree's state: `stateOf` for the model, plus the value text a row shows.
 *
 * **A node with a bound finding shows the finding's message**, which is what "needs a material" is: the
 * server's own words, not a phrase this module invents.
 */
export function outlineStateAdapter(
  input: OutlineStateInput,
): { stateOf: (id: string) => RowState; valueOf: (id: string) => string } {
  const bound = findingsByNode(input.setup);
  return {
    stateOf: (id) => stateOfNode(id, input, bound),
    valueOf: (id) => bound.get(id)?.message ?? "",
  };
}

/**
 * The section-level states the outline shows when a section has no rows of its own to roll up.
 *
 * **Mesh and Solve are known from the run and the mesh result**, not from findings alone: a stale mesh and a
 * running solve are states the Setup page does not report.
 */
export function stageStateOf(
  stage: string,
  input: OutlineStateInput,
): RowState {
  if (stage === "Mesh") {
    if (!input.hasMesh) return "todo";
    return input.meshStale ? "stale" : "ready";
  }
  if (stage === "Solve") {
    if (input.run === null) return "idle";
    if (input.run.state === "running") return "running";
    if (input.run.state === "failed") return "failed";
    return "done";
  }
  if (stage === "Results") return input.resultsShown ? "ready" : "idle";
  return "idle";
}

/** Every node a finding is bound to, for a caller that wants to mark a set rather than a tree. */
export function todoNodes(nodes: readonly NodeSummary[], setup: SetupCheck | null): string[] {
  const bound = findingsByNode(setup);
  const out: string[] = [];
  for (const node of nodes) {
    const finding = bound.get(node.id);
    if (finding !== undefined && finding.severity === "error") out.push(node.id);
  }
  return out;
}

/**
 * The decorated tree with a bound finding's message on the node's row.
 *
 * **This is the seam the missing-finding picture needs**: `treeValue` reads `node.meta.subtitle` first,
 * so a finding's own words — *"`/regions/fluid` has no inlet"* — appear on the region's row without a
 * second rendering path and without the tree knowing what a finding is.
 *
 * **Only `error` findings**: a warning is information, not work to do, and the row's value is for what
 * the user must act on. A finding with no `nodes` lands nowhere — that is `solve.check`'s case today.
 */
export function withFindingValues<T extends NodeSummary | null>(root: T, setup: SetupCheck | null): T {
  if (root === null) return root;
  const bound = findingsByNode(setup);
  if (bound.size === 0) return root;
  const decorate = (node: NodeSummary): NodeSummary => {
    const finding = bound.get(node.id);
    const error = finding !== undefined && finding.severity === "error" ? finding : undefined;
    const children = node.children?.map(decorate);
    if (error === undefined && (children === undefined || children === node.children)) return node;
    const meta = error === undefined ? node.meta : { ...(node.meta ?? {}), subtitle: error.message };
    return { ...node, ...(meta !== undefined ? { meta } : {}), ...(children !== undefined ? { children } : {}) };
  };
  return decorate(root) as T;
}
