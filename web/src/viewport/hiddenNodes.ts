/**
 * Hiding a part or a tag in the VIEW, and nothing else.
 *
 * **One meaning for "Hide"** (claude-win2's 2000 review, item 2). The tree row's eye used to write the
 * model's `visible` property through `geometry.style`, while the viewport menu and the toolbar used
 * session-only state - the same label and the same icon doing two different things, one of them
 * saved, journalled and undoable. The owner's word for that is "they don't behave as we want them
 * to".
 *
 * Hide/Show is now a view filter everywhere: not saved, not journalled, gone on reload. `scene.ts`
 * already worked this way for a patch (`this.hidden.has(part.name)`), so this module is the join
 * between the two vocabularies rather than a new mechanism:
 *
 *   * the TREE holds nodes, and hides them by **id** - an id is stable across a rename;
 *   * the SCENE draws parts, and hides them by **name** - which is what it already had.
 *
 * The model keeps `visible`: `geometry.style` still accepts it, a headless caller may legitimately
 * set it, and a project saved with a part hidden must still draw it hidden. What changed is that no
 * WIDGET writes it - pressing the eye filters the view rather than editing the part.
 */
import type { NodeSummary } from "../api/types";

/** The node types whose names the scene draws, and so the only ones hiding can affect. */
const DRAWABLE = new Set(["part", "tag"]);

/**
 * The names of the parts and tags a set of node ids refers to.
 *
 * **An id that is not in the tree is dropped.** A node deleted while hidden must not leave its name
 * in the set, where it would hide whatever later took that name - a part called `box` replacing a
 * deleted `box` is a plausible sequence, and the user would see a new part appear pre-hidden for no
 * reason they could discover.
 *
 * A region or boundary is deliberately NOT drawable here: its row's eye proxies its tag
 * (`rowControls`), so hiding one must move the tag, not the region - and the tree passes the tag's
 * own id in that case.
 */
export function hiddenNodeNames(tree: NodeSummary | null, ids: readonly string[]): string[] {
  if (tree === null || ids.length === 0) return [];
  const wanted = new Set(ids);
  const found: string[] = [];
  const walk = (node: NodeSummary): void => {
    if (wanted.has(node.id) && DRAWABLE.has(node.type)) found.push(node.name);
    for (const child of node.children ?? []) walk(child);
  };
  walk(tree);
  return found;
}

/**
 * The ids in `ids` that the tree still holds, so a stale one can be forgotten.
 *
 * Called when the tree changes. Without it the list grows for the life of the session and, worse,
 * carries names that a future node could inherit - see `hiddenNodeNames`.
 */
export function livingIds(tree: NodeSummary | null, ids: readonly string[]): string[] {
  if (tree === null) return [];
  const alive = new Set<string>();
  const walk = (node: NodeSummary): void => {
    alive.add(node.id);
    for (const child of node.children ?? []) walk(child);
  };
  walk(tree);
  return ids.filter((id) => alive.has(id));
}

/** Whether a row's eye should show as hidden: the session filter, or the model's own flag. */
export function isNodeHidden(
  id: string,
  hidden: readonly string[],
  modelVisible: boolean | null,
): boolean {
  return hidden.includes(id) || modelVisible === false;
}
