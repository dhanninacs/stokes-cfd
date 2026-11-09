/**
 * Putting the tree away and bringing it back, for guided mode.
 *
 * The left dock holds the tree and the Details pane. Guided mode starts with both put away (the cards
 * do the set-up); the `Tree` button in the top bar and `Ctrl+B` bring them back for the settings the
 * cards do not offer. Both go through the workspace's own `movePanel`, so the arrangement is the same
 * document write the old toggles made and it survives a reload.
 */
import { useEffect } from "react";
import { movePanel } from "../workspace/arrange";
import { useWorkspace, useWorkspaceReady } from "../workspace/store";
import type { LayoutSpec } from "../workspace/types";
import { TREE_INIT_KEY, type GuidedMode } from "./modeModel";
import { useGuidedMode } from "./modeStore";

const LEFT: readonly ("tree" | "details")[] = ["tree", "details"];

function has(layout: LayoutSpec, id: string): boolean {
  return layout.left.panels.some((p) => p.id === id);
}

/** The left dock puts one or both panels on screen. */
export function useLeftShown(): boolean {
  return useWorkspace((s) => s.document.layout.left.panels.length > 0);
}

/** Show or put away the tree and the Details pane together, in one document write. */
export function setLeftShown(show: boolean): void {
  const workspace = useWorkspace.getState();
  const layout = workspace.document.layout;
  let next = layout;
  LEFT.forEach((id, index) => {
    const here = has(next, id);
    if (show && !here) next = movePanel(next, id, { kind: "slot", slot: "left", index });
    if (!show && here) next = movePanel(next, id, { kind: "hidden" });
  });
  // **Shown means unfolded.** The dock keeps its own `collapsed` flag through a put-away, so bringing
  // the panels back into a folded dock showed two icons on a rail and no tree (measured on the switch
  // to Full mode). Showing the tree is what was asked; the fold is undone with it.
  if (show && next.left.collapsed) next = { ...next, left: { ...next.left, collapsed: false } };
  if (next !== layout) void workspace.setValue(["layout"], next);
}

/** The Tree button and Ctrl+B in guided mode: the whole left dock, tree and Details together. */
export function toggleLeft(): void {
  const layout = useWorkspace.getState().document.layout;
  setLeftShown(!LEFT.some((id) => has(layout, id)));
}

/** Switching mode arranges the dock: Guided puts the tree away, Full brings tree and Details back. */
export function arrangeFor(mode: GuidedMode): void {
  setLeftShown(mode === "full");
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Guided mode starts with the tree put away, once. The first time the window is guided and the
 * workspace is loaded, the dock is cleared and that is remembered, so a user who opens the tree keeps
 * it open from then on.
 */
export function useTreeAwayOnce(): void {
  const mode = useGuidedMode();
  const ready = useWorkspaceReady();
  useEffect(() => {
    if (!ready || mode !== "guided") return;
    const store = storage();
    try {
      if (store?.getItem(TREE_INIT_KEY) === "1") return;
      store?.setItem(TREE_INIT_KEY, "1");
    } catch {
      // Without storage the tree is put away on every load, which is what a first run would see.
    }
    setLeftShown(false);
  }, [ready, mode]);
}
