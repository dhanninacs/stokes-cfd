import { useState } from "react";
import { Boxes, ChevronDown, Eye, EyeOff, Focus } from "lucide-react";
import { useShortcut } from "../workspace/keymap";
import { findByPath, useStore } from "../state/store";
import type { PatchMeta } from "../viewport/geometry";
import { Button, IconButton } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { assignBoundary, boundaryPath } from "./assignBoundary";
import { useAssignMenu } from "./AssignMenu";

/**
 * The card for a selected geometry patch: its swatch and counts, the boundary action, and Hide and
 * Isolate as icon buttons. The boundary action is the menu of condition kinds (`boundary.kinds`)
 * (T-061): it assigns to the live pick when there is one, else to this patch's own boundary, and it
 * only reads "Show boundary" while the patch has one and nothing is picked, when it only takes you
 * there. A patch of an imported part is one solid: it also counts its B-rep faces.
 */
export default function PatchCard({ patch, divider }: { patch: PatchMeta; /** A node card sits above: draw the separating line. */ divider: boolean }) {
  const tree = useStore((s) => s.tree);
  const hidden = useStore((s) => s.hiddenPatches.includes(patch.name));
  const isolated = useStore((s) => s.isolatedPatch === patch.name);
  const toggleHidden = useStore((s) => s.toggleHidden);
  const isolate = useStore((s) => s.isolate);
  const [busy, setBusy] = useState(false);
  const hideHint = useShortcut("selection.hide");
  const isolateHint = useShortcut("selection.isolate");
  const assign = useAssignMenu();

  const assigned = findByPath(tree, boundaryPath(patch.name)) !== null;
  const [r, g, b] = patch.color; // 0..1 floats from the geometry transport
  const swatch = `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`;

  const reveal = async () => {
    setBusy(true);
    try {
      await assignBoundary(patch.name);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="Patch selection" className={`px-3 py-3 ${divider ? "border-t" : ""}`} style={{ borderColor: "var(--panel-border)" }}>
      <div className="section-title mb-2.5 flex items-center gap-2">
        <Boxes size={12} /> Selection
      </div>
      <div className="mb-2.5 flex items-center gap-2.5">
        <span
          className="h-6 w-6 shrink-0 rounded-[7px] border"
          style={{ background: swatch, borderColor: "var(--panel-border-strong)" }}
          aria-hidden="true"
        />
        <div className="min-w-0">
          <Tooltip label={patch.name} side="top" describe={false}>
            <div className="truncate font-medium">{patch.name}</div>
          </Tooltip>
          <div className="muted text-[11.5px]">{patch.kind}</div>
        </div>
      </div>
      <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12.5px]">
        <span className="muted">Triangles</span>
        <span>{patch.triangles.toLocaleString()}</span>
        <span className="muted">Vertices</span>
        <span>{patch.vertices.toLocaleString()}</span>
        {patch.faces > 0 && (
          <>
            <span className="muted">Faces</span>
            <span>{patch.faces.toLocaleString()}</span>
          </>
        )}
        <span className="muted">Boundary</span>
        <span style={{ color: assigned ? "var(--ok)" : "var(--text-muted)" }}>{assigned ? "assigned" : "not assigned yet"}</span>
      </div>
      <div className="mt-3 flex gap-2">
        {assigned && !assign.hasPick ? (
          <Tooltip label="Select the existing boundary" side="top" describe={false}>
            <Button variant="default" data-prop-field="action" className="!h-8 min-w-0 flex-1 justify-center" disabled={busy} onClick={() => void reveal()}>
              <span className="truncate">Show boundary</span>
            </Button>
          </Tooltip>
        ) : (
          <Tooltip
            label={assign.hasPick ? `Assign a condition to ${assign.pickLabel}` : `Assign a condition to ${patch.name}`}
            side="top"
            describe={false}
          >
            <Button
              variant={assigned ? "default" : "primary"}
              data-prop-field="action"
              className="!h-8 min-w-0 flex-1 justify-center"
              disabled={!assign.ready}
              onClick={(e) => assign.open(e.currentTarget)}
            >
              <span className="truncate">Assign</span>
              <ChevronDown size={14} />
            </Button>
          </Tooltip>
        )}
        <IconButton
          variant="default"
          label={hidden ? `Show ${patch.name} again` : `Hide ${patch.name}`}
          hint={hideHint.hint}
          keys={hideHint.keys}
          className="!h-8 !w-8 shrink-0"
          onClick={() => toggleHidden(patch.name)}
        >
          {hidden ? <EyeOff size={14} /> : <Eye size={14} />}
        </IconButton>
        <IconButton
          variant="default"
          label={isolated ? "Show all patches again" : `Show only ${patch.name}`}
          hint={isolateHint.hint}
          keys={isolateHint.keys}
          className="!h-8 !w-8 shrink-0"
          aria-pressed={isolated}
          onClick={() => isolate(isolated ? null : patch.name)}
        >
          <Focus size={14} />
        </IconButton>
      </div>
      {assign.menu}
      {assign.dialog}
    </section>
  );
}
