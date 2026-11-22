/**
 * "Edit sketch…": opens the sketcher (ADR-0036) on a drawn profile, from the operation card of a new
 * sketch (`opId` null: Finish fills the card) or from a sketch node's properties (Finish sets its
 * parameters).
 */
import { PenTool } from "lucide-react";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import { mapEntries } from "../panels/compoundValues";
import { parseStored } from "./bindings";
import { originOf } from "../direct/plane";

const PLANES = new Set(["xy", "yz", "zx"]);

export default function EditSketchButton({ opId, params }: { opId: string | null; params: Record<string, unknown> }) {
  const open = () => {
    const plane = typeof params.plane === "string" && PLANES.has(params.plane) ? (params.plane as "xy" | "yz" | "zx") : "xy";
    const { entities, bindings } = parseStored(params.entities);
    const dimensions = Object.fromEntries(mapEntries(params, undefined, "dimensions").map((e) => [e.name, e.text]));
    useStore.getState().openSketch({ opId, plane, entities, bindings, dimensions, origin: originOf(params.origin) });
  };
  return (
    <Button onClick={open} data-ws="edit-sketch">
      <PenTool size={13} /> Edit sketch…
    </Button>
  );
}
