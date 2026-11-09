/**
 * A body's page, Basic part: what the body is for the flow (Fluid, Solid, Hole, Leave out).
 *
 * The same decision the Geometry card makes, through the same command (`setBodyRole` is one
 * `mesh.body_role`, which also sets the mesh settings' domain part for the fluid). Only one body can be the
 * fluid or the hole, so those two are off for the others with the reason as their tooltip.
 */
import { useMemo } from "react";
import type { NodeSummary } from "../api/types";
import { Segmented } from "../components/ui";
import { setBodyRole } from "../guided/commands";
import { readBodies } from "../guided/guidedTree";
import { useAction } from "../guided/useAction";
import { BODY_ROLES } from "../setup/basicSets";
import { useStore } from "../state/store";
import { slotReason, type Role } from "./meshTaskPages/bodiesModel";

export default function BodyRoleChips({ node }: { node: NodeSummary }) {
  const tree = useStore((s) => s.tree);
  const action = useAction();
  const bodies = useMemo(() => readBodies(tree), [tree]);
  const body = bodies.find((b) => b.id === node.id);
  if (body === undefined) return null;
  const slotTaken = bodies.some((b) => b.role === "fluid" || b.role === "hole");
  const options = BODY_ROLES.map((r) => {
    const exclusive = r.value === "fluid" || r.value === "hole";
    return { value: r.value, label: r.label, disabled: exclusive && slotTaken && body.role !== r.value ? slotReason(bodies.length) : undefined };
  });
  return (
    <div className="flex flex-col gap-1.5" data-ws="body-role">
      <Segmented<Role> label={`What ${node.name} is`} value={body.role} options={options} onChange={(role) => void action.run(() => setBodyRole(node.id, role))} />
      {action.error !== "" && (
        <p role="alert" className="text-[12px]" style={{ color: "var(--danger)" }}>
          {action.error}
        </p>
      )}
    </div>
  );
}
