import { ChevronRight } from "lucide-react";
import { useShortcut } from "./keymap";
import { Tooltip } from "../components/Tooltip";
import { showPanel } from "./Layout";

/**
 * The thin handle on the screen edge while a panel is hidden.
 *
 * **Claude's GUI review item 5**: *"a user who collapses both sees an empty screen and no hint"*. The
 * chords and the palette work, and neither is discoverable from an empty window - so a 12 px chevron
 * sits where the panel was, with the shortcut in its tooltip.
 *
 * **Only while it is hidden**, which is what the owner asked for: a permanent control for a state most
 * users are not in would be one more thing on screen. It is 12 px because it must not cost the viewport
 * width it exists to free.
 */
export default function EdgeHandle({ panel }: { panel: "tree" | "details" }) {
  const keys = useShortcut(panel === "tree" ? "layout.toggleTree" : "layout.toggleDetails");
  const name = panel === "tree" ? "tree" : "Details pane";
  return (
    <Tooltip label={`Show the ${name}`} hint={keys.hint} side="right" describe={false}>
      <button
        type="button"
        className="edge-handle no-drag"
        aria-label={`Show the ${name}`}
        aria-expanded={false}
        data-edge-handle={panel}
        aria-keyshortcuts={keys.keys}
        // The handle's only job is to bring the panel back (claude-win2: a control does what it says).
        onClick={() => showPanel(panel)}
      >
        <ChevronRight size={13} aria-hidden="true" />
        {/* **The panel's own word**, so the tab says WHAT it restores: "T" and "D" are a puzzle. */}
        <span aria-hidden="true">{panel === "tree" ? "Tree" : "Info"}</span>
      </button>
    </Tooltip>
  );
}
