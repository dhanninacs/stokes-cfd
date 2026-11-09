import { Boxes, ChevronDown, X } from "lucide-react";
import { useShortcut } from "../workspace/keymap";
import { describePick, useStore } from "../state/store";
import { Button } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { useAssignMenu } from "./AssignMenu";
import { overlapNotice } from "./coverage";
import { usePickOverlap } from "./usePickOverlap";

/**
 * The card for a live pick (T-061): what the pick holds ("3 faces on 2 parts"), how much of it,
 * and the actions that belong to it. It stands in for the patch card while faces are picked, so
 * nothing here reads as the whole body being selected. Assign is the menu of condition kinds,
 * which applies to exactly this pick.
 */
export default function PickCard({ divider }: { divider: boolean }) {
  const picked = useStore((s) => s.picked);
  const pickMode = useStore((s) => s.pickMode);
  const clearPick = useStore((s) => s.clearPick);
  const assign = useAssignMenu();
  const clearHint = useShortcut("selection.clear");
  const notice = overlapNotice(usePickOverlap());

  const label = describePick(picked, pickMode) ?? "Nothing is picked";
  const parts = Object.values(picked.faces).filter((ids) => ids.length > 0).length || picked.bodies.length;
  const faces = Object.values(picked.faces).reduce((n, ids) => n + ids.length, 0);

  return (
    <section aria-label="Pick selection" className={`px-3 py-3 ${divider ? "border-t" : ""}`} style={{ borderColor: "var(--panel-border)" }}>
      <div className="section-title mb-2.5 flex items-center gap-2">
        <Boxes size={12} /> Selection
      </div>
      <div className="mb-2.5 font-medium">{label}</div>
      <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12.5px]">
        {faces > 0 && (
          <>
            <span className="muted">Faces</span>
            <span>{faces.toLocaleString()}</span>
          </>
        )}
        <span className="muted">{pickMode === "face" ? "Parts" : "Bodies"}</span>
        <span>{parts.toLocaleString()}</span>
      </div>
      {notice !== null && (
        // G10: an overlap is allowed while preparing and refused by the case compiler, so this warns
        // and leaves Assign enabled. Amber is the stale/warning status colour (DESIGN.md §2).
        <p role="status" className="m-0 mt-2.5 text-[12px]" style={{ color: "var(--warning)" }} data-ws="pick-overlap">
          {notice}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <Tooltip label="Assign a condition to the picked faces" side="top" describe={false}>
          <Button variant="primary" data-prop-field="action" className="!h-8 min-w-0 flex-1 justify-center" disabled={!assign.ready} onClick={(e) => assign.open(e.currentTarget)}>
            <span className="truncate">Assign</span>
            <ChevronDown size={14} />
          </Button>
        </Tooltip>
        <Button variant="default" className="!h-8 shrink-0" onClick={clearPick} aria-keyshortcuts={clearHint.keys}>
          <X size={14} /> Clear
        </Button>
      </div>
      {assign.menu}
      {assign.dialog}
    </section>
  );
}
