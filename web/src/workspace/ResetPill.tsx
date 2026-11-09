import { RotateCcw } from "lucide-react";
import { useWorkspace } from "./store";

/**
 * The way out of a hidden dock, shown only when both panels are away.
 *
 * **The owner, via Claude (1330)**: *"a visible 'Reset layout' pill"*. The command existed and the
 * palette entry did nothing until this round; a pill where the panels belong is the discoverable form,
 * and it appears exactly in the state that needs it rather than as a permanent button.
 */
export default function ResetPill() {
  return (
    <button
      type="button"
      className="reset-pill glass no-drag"
      data-ws="reset-pill"
      onClick={() => void useWorkspace.getState().resetLayout()}
    >
      <RotateCcw size={13} aria-hidden="true" />
      Reset layout
    </button>
  );
}
