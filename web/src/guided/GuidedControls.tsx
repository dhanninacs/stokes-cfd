/**
 * The `Tree` button of the top bar: puts the tree and the Details pane away or brings them back.
 *
 * In Guided mode it moves the whole left dock (the cards do the set-up, and the tree is for the settings the
 * cards do not offer); in Full mode it is the tree's own toggle, as it was. `Ctrl+B` does the same through
 * `treeShortcut`, which `App.tsx` registers.
 */
import { PanelLeft } from "lucide-react";
import { Tooltip } from "../components/Tooltip";
import { Button } from "../components/ui";
import { useShortcut } from "../workspace/keymap";
import { toggleTree } from "../workspace/Layout";
import { useWorkspace } from "../workspace/store";
import { currentMode, useGuidedMode } from "./modeStore";
import { toggleLeft, useLeftShown } from "./treeToggle";

/** Ctrl+B: the left dock in Guided mode, the tree alone in Full mode. */
export function treeShortcut(): void {
  if (currentMode() === "guided") toggleLeft();
  else toggleTree();
}

export function TreeToggle() {
  const mode = useGuidedMode();
  const dock = useLeftShown();
  const treeShown = useWorkspace((s) => s.document.layout.left.panels.some((p) => p.id === "tree"));
  const shown = mode === "guided" ? dock : treeShown;
  const keys = useShortcut("layout.toggleTree");
  return (
    <Tooltip label={shown ? "Put the tree away" : "Show the tree"} hint={keys.hint} side="bottom" describe={false}>
      <Button variant="ghost" active={shown} aria-pressed={shown} aria-keyshortcuts={keys.keys} onClick={treeShortcut} data-ws-item="tree-toggle" data-ws="tree-toggle">
        <PanelLeft size={15} aria-hidden="true" />
        <span className="tb-tree-label">Tree</span>
      </Button>
    </Tooltip>
  );
}
