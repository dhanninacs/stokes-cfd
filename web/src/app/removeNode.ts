/**
 * Delete a node with an Undo toast (T-172): `node.remove`, selection moved to null when the doomed
 * subtree held it, and a toast whose Undo steps back only while the delete is still the newest entry of
 * the linear undo stack. This is the tree's `removeNode` and `undoDelete` (TreePanel.tsx) for a caller
 * that has no row list: the viewport menu and the palette. The tree keeps its own copy, which also
 * moves the selection to the neighbouring row and puts the focus back (T-174 makes it call this one).
 */
import type { NodeSummary } from "../api/types";
import { isStructural, subtreeIds } from "../panels/nodeRules";
import { useStore } from "../state/store";
import { shortcutHint } from "../workspace/keymap";
import { removalPlan, removalQuestion } from "../panels/bodySteps";
import { confirmDialog } from "../panels/explore/confirm";

/** The undo kinds a delete with its dependents leaves on the stack (`node.remove`, `operation.delete`). */
const DELETE_KINDS: ReadonlySet<string> = new Set(["remove_node", "delete_operation"]);

/** Undo the `steps` deletes of one delete-with-dependents, while each is still the newest entry. */
async function undoSteps(id: string, steps: number): Promise<void> {
  const s = useStore.getState();
  try {
    for (let i = 0; i < steps; i++) {
      const state = (await s.execute("history.state", {}, { silent: true })) as { undo_kind: string | null };
      if (state.undo_kind === null || !DELETE_KINDS.has(state.undo_kind)) {
        const undo = shortcutHint("history.undo");
        s.pushToast("info", `Other changes were made since. ${undo ? `Use ${undo} to step back` : "Undo steps back"} through them.`);
        return;
      }
      await s.execute("history.undo");
    }
    await s.refresh();
    useStore.getState().select(id);
  } catch {
    /* reported by execute */
  }
}

/**
 * Delete a body or an operation together with what is built from it (review finding A7): asks
 * first when other operations read it, naming them, then deletes downstream first, so no operation
 * is ever left reading a body that is gone. A body that is its operation's only output goes by
 * deleting that operation, which takes the body with it (deleting the body alone left the operation
 * behind, stale). `none` when the node has nothing of the kind, so the caller deletes it as usual.
 */
export async function removeWithDependents(node: NodeSummary): Promise<"none" | "cancelled" | "removed" | "failed"> {
  const s = useStore.getState();
  const plan = removalPlan(s.tree, node);
  if (plan === null) return "none";
  const question = removalQuestion(node, plan);
  if (question !== null && !(await confirmDialog(question))) return "cancelled";
  let steps = 0;
  try {
    for (const op of [...plan.operations].reverse()) {
      await s.execute("operation.delete", { op: op.id });
      steps++;
    }
    if (plan.maker !== null) await s.execute("operation.delete", { op: plan.maker.id });
    else await s.execute("node.remove", { node: node.id });
    steps++;
  } catch {
    // Reported by execute; what was deleted before the failure stays deleted and can be undone.
    if (steps > 0) useStore.getState().pushToast("info", `Deleted ${steps} of the steps`, { label: "Undo", run: () => void undoSteps(node.id, steps) });
    return "failed";
  }
  const now = useStore.getState();
  const doomed = new Set([node.id, ...plan.bodies.map((b) => b.id), ...plan.operations.map((o) => o.id), ...(plan.maker ? [plan.maker.id] : [])]);
  if (now.selectedId && doomed.has(now.selectedId)) now.select(null);
  const more = plan.operations.length;
  const text = more === 0 ? `Deleted ${node.name}` : `Deleted ${node.name} and ${more} operation${more === 1 ? "" : "s"} built from it`;
  now.pushToast("info", text, { label: "Undo", run: () => void undoSteps(node.id, steps) });
  return "removed";
}

/** Undo the delete of `id` if it is still the latest step; otherwise say how to step back. */
export async function undoDelete(id: string): Promise<void> {
  const s = useStore.getState();
  try {
    const state = (await s.execute("history.state", {}, { silent: true })) as { undo_kind: string | null };
    if (state.undo_kind !== "remove_node") {
      const undo = shortcutHint("history.undo");
      s.pushToast("info", `Other changes were made since. ${undo ? `Use ${undo} to step back` : "Undo steps back"} through them.`);
      return;
    }
    await s.execute("history.undo");
    await s.refresh();
    useStore.getState().select(id);
  } catch {
    /* reported by execute */
  }
}

/** Remove the node; false when the skeleton refused it or the command failed. */
export async function removeNode(node: NodeSummary): Promise<boolean> {
  const s = useStore.getState();
  if (isStructural(node)) {
    s.pushToast("info", `${node.name} is part of the project skeleton and cannot be deleted.`);
    return false;
  }
  const withDependents = await removeWithDependents(node);
  if (withDependents === "removed") return true;
  if (withDependents !== "none") return false;
  const doomed = new Set(subtreeIds(node));
  try {
    await s.execute("node.remove", { node: node.id });
  } catch {
    return false; // reported by execute
  }
  const now = useStore.getState();
  if (now.selectedId && doomed.has(now.selectedId)) now.select(null);
  now.pushToast("info", `Deleted ${node.name}`, { label: "Undo", run: () => void undoDelete(node.id) });
  return true;
}
