/**
 * The top bar's `Fast` button (ADR-0047): puts the Fast mode panel on screen.
 *
 * The panel is hidden in every stock layout, and the palette lists the raw `fast.*` commands above
 * "Open Fast mode", so without a button the preview could not be found (GUI review, 2026-11-22). It
 * is lit while a preview runs: the lattice keeps stepping with the panel put away, and this is then
 * the one sign of it, and the way back to Stop. Shown with a project open only.
 */
import { Zap } from "lucide-react";
import { Tooltip } from "../components/Tooltip";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import { previewRuns } from "./fastModel";
import { useFastStore } from "./fastStore";

function openFastPanel(): void {
  // Imported at the press, as `panelOpen.ts` does: the registry is not pulled into the top bar.
  void import("../panels/paletteItems").then((m) => m.openPanelById("fast"));
}

export function FastToggle() {
  const project = useStore((s) => s.project);
  const running = useFastStore((s) => s.frame !== null || previewRuns(s.status?.state));
  if (project === null) return null;
  return (
    <Tooltip label={running ? "Fast mode is running: open its panel" : "Fast mode: a quick GPU preview of the flow"} side="bottom" describe={false}>
      <Button variant="ghost" active={running} aria-label="Fast mode" onClick={openFastPanel} data-ws-item="fast-toggle" data-ws="fast-toggle">
        <Zap size={15} aria-hidden="true" />
        <span className="tb-tree-label">Fast</span>
      </Button>
    </Tooltip>
  );
}
