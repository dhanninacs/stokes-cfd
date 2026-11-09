/**
 * A material's page, Basic part: its density and viscosity at the reference temperature, and Change…
 * which opens the properties under Advanced.
 */
import { useEffect, useState } from "react";
import type { NodeSummary } from "../api/types";
import { Button } from "../components/ui";
import { materialReadout } from "../guided/commands";
import { plainMaterialName, refTemperatureOf } from "../guided/guidedTree";

export default function MaterialReadout({ node, onChange }: { node: NodeSummary; onChange: () => void }) {
  const [text, setText] = useState("");
  const t = refTemperatureOf(node) ?? 300;
  useEffect(() => {
    setText("");
    let live = true;
    void materialReadout(node.id, t)
      .then((answer) => live && setText(answer))
      .catch(() => live && setText(""));
    return () => {
      live = false;
    };
  }, [node.id, t, node.status]);
  const fluid = typeof node.params.fluid === "string" ? node.params.fluid : "";
  return (
    <div className="flex items-center justify-between gap-2 text-[12.5px]" data-ws="material-readout">
      <div className="min-w-0">
        <strong>{plainMaterialName(node.name, fluid)}</strong>
        {text !== "" && <span className="muted"> · {text}</span>}
      </div>
      <Button onClick={onChange} title="Edit the material's properties">
        Change…
      </Button>
    </div>
  );
}
