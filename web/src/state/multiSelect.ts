/**
 * The tree's selection: one row, or many, and the gestures that change it.
 *
 * **The whole rule is here and nowhere else**, because selection is the kind of state that rots when it is
 * spread over a component: `TreePanel` reads `selectedId` in sixteen places, and a second copy of "which
 * rows are chosen" living in a handler would be wrong the first time a folder collapsed.
 *
 * **`primary` is the row the tree's OTHER behaviours follow** - drag, rename (F2), the row menu, and the
 * Details page - so a multi-selection still has exactly one subject. `ids` always contains it, and a
 * selection of one has `primary === ids[0]`.
 *
 * The gestures are Explorer's and Fluent's, which is what the owner asked for by naming them:
 *
 * | gesture | result |
 * |---|---|
 * | plain click | that row alone |
 * | Ctrl/Cmd click | toggle that row, keeping the rest |
 * | Shift click | the range from the ANCHOR to the row, in visible order |
 * | Ctrl+Shift click | the anchor's range ADDED to what is chosen |
 * | Ctrl+A | every sibling of the primary's type in its folder |
 * | Shift+Up/Down | extend from the anchor to the new row |
 * | Escape, or a press on empty tree | nothing |
 */
export interface Selection {
  /** The chosen rows, in the order they were chosen. Empty means nothing is selected. */
  readonly ids: readonly string[];
  /** The row the other behaviours follow; `null` exactly when `ids` is empty. */
  readonly primary: string | null;
  /** Where a Shift range starts: the last row chosen WITHOUT Shift. */
  readonly anchor: string | null;
}

export const NO_SELECTION: Selection = { ids: [], primary: null, anchor: null };

/** One row of the tree as the selection needs it: its id and whether it is VISIBLE right now. */
export interface VisibleRow {
  id: string;
  /** The row's parent, for `selectAllOfType`. */
  parent: string | null;
  /** The node's type, for `selectAllOfType`. */
  type: string;
}

/** A plain click, or a keyboard move with no modifier: exactly that row. */
export function selectOne(id: string): Selection {
  return { ids: [id], primary: id, anchor: id };
}

/**
 * Ctrl/Cmd click: that row toggles, and the anchor MOVES to it.
 *
 * **Toggling the primary off leaves the selection with no primary**, and the next row in the selection
 * takes over rather than the whole selection collapsing - a user who Ctrl-clicks a chosen row out of a set
 * of five expects four, not none.
 */
export function toggle(selection: Selection, id: string): Selection {
  if (selection.ids.includes(id)) {
    const ids = selection.ids.filter((x) => x !== id);
    if (ids.length === 0) return NO_SELECTION;
    return { ids, primary: selection.primary === id ? ids[ids.length - 1]! : selection.primary, anchor: id };
  }
  return { ids: [...selection.ids, id], primary: id, anchor: id };
}

/**
 * Shift click: the range between the anchor and this row, **over the rows as the user can see them**.
 *
 * **Collapsed children are not included**, because they are not on screen: a user who Shift-clicks down a
 * list cannot have meant the five rows inside a folder they cannot see. That is why the caller passes the
 * VISIBLE order rather than the tree's.
 */
export function extend(selection: Selection, id: string, visible: readonly string[], additive: boolean): Selection {
  const anchor = selection.anchor ?? selection.primary ?? id;
  const from = visible.indexOf(anchor);
  const to = visible.indexOf(id);
  if (from < 0 || to < 0) return selectOne(id);
  const range = visible.slice(Math.min(from, to), Math.max(from, to) + 1);
  if (!additive) return { ids: range, primary: id, anchor };
  // **Ctrl+Shift ADDS the range** to what is already chosen, keeping the earlier choices and their order.
  const kept = selection.ids.filter((x) => !range.includes(x));
  return { ids: [...kept, ...range], primary: id, anchor };
}

/** Ctrl+A: every row of the same type in the same folder. **Not every row in the tree**, which is not what the key means here. */
export function selectSiblings(selection: Selection, rows: readonly VisibleRow[]): Selection {
  const primary = rows.find((r) => r.id === selection.primary);
  if (primary === undefined) return selection;
  const same = rows.filter((r) => r.parent === primary.parent && r.type === primary.type);
  if (same.length === 0) return selection;
  return { ids: same.map((r) => r.id), primary: selection.primary, anchor: selection.anchor };
}

/** The row a Shift+Up/Down lands on: the next VISIBLE row, so a collapsed folder is one step. */
export function moveBy(rows: readonly string[], id: string, step: -1 | 1): string | null {
  const at = rows.indexOf(id);
  if (at < 0) return null;
  return rows[at + step] ?? null;
}
