import type { KeyboardEvent } from "react";
import type { NodeSummary } from "../api/types";
import { useStore } from "../state/store";
import { matchesAction } from "../workspace/keymap";
import { moveBy } from "../state/multiSelect";
import { isStructural, rowDomId, type RowModel } from "./nodeRules";

/** Window event the properties panel listens for (Enter in the tree). */
export const FOCUS_PROPERTIES_EVENT = "stokes:focus-properties";
/** Window event the tree listens for (Esc in a properties field). */
export const FOCUS_TREE_EVENT = "stokes:focus-tree";

export interface TreeKeyboardActions {
  rows: RowModel[];
  selectedId: string | null;
  select(id: string): void;
  startRename(node: NodeSummary): void;
  remove(node: NodeSummary): void;
  /** Open the node's menu at a point (the selected row, for the keyboard). */
  openMenu(node: NodeSummary, at: { x: number; y: number }): void;
}

/**
 * Keyboard model of the tree (DESIGN.md section 3): up and down move, right and left expand and
 * collapse or step to the child or parent, Enter hands focus to the properties panel. The three
 * actions of the tree, rename, delete and the row menu, are the workspace's (`tree.rename`,
 * `tree.delete`, `tree.menu`; F2, Delete and Shift F10 or the menu key by default), so they follow a
 * rebinding. Keys typed into the inline rename box are left alone, as are Ctrl, Alt and Meta
 * combinations that no tree action claims, and every key handled here is consumed so the global
 * shortcuts do not also act on it.
 */
export function treeKeyHandler(a: TreeKeyboardActions) {
  return (e: KeyboardEvent<HTMLElement>): void => {
    if ((e.target as HTMLElement).tagName === "INPUT") return;
    const { rows } = a;
    const index = rows.findIndex((r) => r.node.id === a.selectedId);
    const current = index >= 0 ? rows[index] : null;
    const go = (target: number) => {
      const row = rows[Math.min(Math.max(target, 0), rows.length - 1)];
      if (row) a.select(row.node.id);
    };
    const store = useStore.getState();

    // The tree's own actions come first: whatever chord the user bound, a modifier included.
    const key = e.nativeEvent;
    if (matchesAction(key, "tree.rename")) {
      if (!current || isStructural(current.node)) return;
      e.preventDefault();
      a.startRename(current.node);
      return;
    }
    if (matchesAction(key, "tree.delete")) {
      if (!current) return;
      e.preventDefault();
      a.remove(current.node);
      return;
    }
    if (matchesAction(key, "tree.menu")) {
      if (!current) return;
      e.preventDefault();
      const rect = document.getElementById(rowDomId(current.node.id))?.getBoundingClientRect();
      if (rect) a.openMenu(current.node, { x: rect.left + 48, y: rect.bottom - 6 });
      return;
    }
    /*
      **The multi-selection keys, BEFORE the modifier guard** (claude-win2's 1400 file): Ctrl+A selects the
      siblings of the primary's type, Escape clears, and Shift+arrow extends. The guard below returns on
      any Ctrl/Alt/Meta press, which is why these have to be handled first - otherwise the gestures the
      brief asks for could never arrive.
    */
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "a") {
      e.preventDefault();
      store.selectSiblings();
      return;
    }
    if (e.key === "Escape" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      store.clearSelection();
      return;
    }
    if (e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      // **From the primary**, over the rows as shown: a folded folder is one step, not a hundred.
      const visible = rows.filter((r) => !isStructural(r.node)).map((r) => r.node.id);
      const from = a.selectedId ?? visible[0];
      if (from !== undefined) {
        const step = e.key === "ArrowDown" ? 1 : -1;
        const landed = moveBy(visible, from, step);
        if (landed !== null) store.selectRow(landed, { shift: true }, visible);
      }
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        go(index < 0 ? 0 : index + 1);
        break;
      case "ArrowUp":
        e.preventDefault();
        go(index < 0 ? rows.length - 1 : index - 1);
        break;
      case "Home":
        e.preventDefault();
        go(0);
        break;
      case "End":
        e.preventDefault();
        go(rows.length - 1);
        break;
      case "PageDown":
        e.preventDefault();
        go(index < 0 ? 0 : index + 8);
        break;
      case "PageUp":
        e.preventDefault();
        go(index < 0 ? 0 : index - 8);
        break;
      case "ArrowRight":
        if (!current) break;
        e.preventDefault();
        if (current.hasChildren && !current.open) store.toggleExpanded(current.node.id);
        else if (current.open && rows[index + 1]?.parentId === current.node.id) go(index + 1);
        break;
      case "ArrowLeft":
        if (!current) break;
        e.preventDefault();
        // A node held open only by the filter cannot be collapsed; step to the parent instead.
        if (current.open && store.expanded[current.node.id]) store.toggleExpanded(current.node.id);
        else if (current.parentId) a.select(current.parentId);
        break;
      case "Enter":
        e.preventDefault();
        if (!current && rows[0]) a.select(rows[0].node.id);
        window.dispatchEvent(new CustomEvent(FOCUS_PROPERTIES_EVENT));
        break;
      default:
        break;
    }
  };
}
