/**
 * Rename the node behind a patch (T-172): the context toolbar's `rename`, as a function. It selects the
 * patch in the tree; with the tree shown the row renames in place (`emitRename`), else the caller asks
 * for a name with the dialog this returns the node for. The toolbar keeps its own copy until T-182
 * makes it call this one.
 */
import { isStructural } from "../panels/nodeRules";
import { findNode, useStore } from "../state/store";
import { emitRename } from "./events";

/** The node a name prompt is for. */
export interface RenameRequest {
  id: string;
  name: string;
}

/**
 * Start a rename of `patch`'s node. Returns null when the tree renames it in place or when it cannot be
 * renamed (a toast says why); returns the node when the caller has to show the prompt.
 */
export function renameTarget(patch: string, treeShown: boolean): RenameRequest | null {
  const s = useStore.getState();
  s.selectPatch(patch); // selects the matching node in the tree
  const nodeId = useStore.getState().selectedId;
  if (treeShown) {
    emitRename({ patch, nodeId });
    return null;
  }
  const node = findNode(useStore.getState().tree, nodeId);
  if (!node) s.pushToast("info", `${patch} has no node in the tree to rename.`);
  else if (isStructural(node)) s.pushToast("info", `${node.name} is part of the project skeleton and cannot be renamed.`);
  else return { id: node.id, name: node.name };
  return null;
}
