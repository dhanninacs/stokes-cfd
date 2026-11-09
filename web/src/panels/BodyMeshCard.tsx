import { useEffect, useState } from "react";
import type { NodeSummary } from "../api/types";
import { useStore } from "../state/store";
import { Note } from "../components/Note";

/**
 * What a body is meshed AS (claude-win2's package-two review item 11).
 *
 * **The owner's question, answered in one line**: *"what is this meshed as?"* A body's page showed
 * its own facts - faces, size, colour - and nothing about the region that contains it or the material
 * that region uses. `region.list` already carries both, per body, so this reads it rather than the
 * user opening two other folders to find out.
 *
 * **Clickable, because the answer invites the next question**: a reader who learns "Region: fluid"
 * wants to see that region, and the pane follows the selection.
 */
interface RegionRow {
  id: string;
  name: string;
  kind: string;
  material?: { id: string; name: string; missing?: boolean };
  bodies?: string[];
}

export default function BodyMeshCard({ node }: { node: NodeSummary }) {
  const execute = useStore((s) => s.execute);
  const select = useStore((s) => s.select);
  const [row, setRow] = useState<RegionRow | null | undefined>(undefined);

  useEffect(() => {
    let live = true;
    void execute("region.list", {}, { silent: true })
      .then((reply) => {
        const rows = (Array.isArray(reply) ? reply : ((reply as { regions?: RegionRow[] }).regions ?? [])) as RegionRow[];
        // **The region whose bodies include this part.** A part in no region is the ordinary state
        // for a body nobody has set up yet, so it is said rather than left blank.
        const found = rows.find((r) => (r.bodies ?? []).includes(node.id)) ?? null;
        if (live) setRow(found);
      })
      .catch(() => live && setRow(null));
    return () => {
      live = false;
    };
  }, [execute, node.id]);

  if (row === undefined) return <p className="muted text-[12.5px]">Looking for its region…</p>;
  if (row === null) {
    return (
      <Note className="text-[12.5px]" tip="Not in a region yet, so it is meshed on its own." data-ws="body-mesh-card">
        Meshed on its own
      </Note>
    );
  }
  return (
    <p className="text-[12.5px]" data-ws="body-mesh-card">
      Region:{" "}
      <button type="button" className="link no-drag" onClick={() => select(row.id)}>
        {row.name}
      </button>
      {" · "}Material:{" "}
      {row.material === undefined ? (
        <span className="muted">none</span>
      ) : (
        <button type="button" className="link no-drag" onClick={() => select(row.material?.id ?? "")}>
          {row.material.name}
          {row.material.missing === true ? " (missing)" : ""}
        </button>
      )}
    </p>
  );
}
