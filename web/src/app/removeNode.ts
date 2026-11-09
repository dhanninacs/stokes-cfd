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
