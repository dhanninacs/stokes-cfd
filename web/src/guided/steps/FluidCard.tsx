/**
 * Fluid: what the fluid body is made of.
 *
 * Water and Air are one press each (the built-in library: the project's copy is reused or added, then
 * assigned to the fluid body, the same two calls the Material menu makes). Browse opens the Material menu
 * on that body for the rest of the library. The chosen material is shown with its density and viscosity at
 * its reference temperature.
 */
import { useEffect, useMemo, useState } from "react";
import { useMaterialMenu } from "../../panels/MaterialMenu";
import { useStore } from "../../state/store";
import { Button } from "../../components/ui";
import GuidedCard from "../GuidedCard";
import { Chips, ErrorLine, Note, type Choice } from "../controls";
import { QUICK_MATERIALS, assignLibraryMaterial, materialReadout, type QuickMaterial } from "../commands";
import type { GuidedStep } from "../guidedModel";
import { fluidBody, readBodies, readFluidRegion } from "../guidedTree";
import { selectStep } from "../modeStore";
import { useAction } from "../useAction";
import FlowOptions from "./FlowOptions";

const CHOICES: readonly Choice<string>[] = QUICK_MATERIALS.map((m) => ({ value: m.fluid, label: m.label }));

export default function FluidCard({ step }: { step: GuidedStep }) {
  const tree = useStore((s) => s.tree);
  const body = useMemo(() => fluidBody(readBodies(tree)), [tree]);
  const region = useMemo(() => readFluidRegion(tree), [tree]);
  const action = useAction();
  const menu = useMaterialMenu();
  const [readout, setReadout] = useState("");

  // The density and the viscosity of the chosen material, read when the material changes.
  const materialId = region?.materialId ?? null;
  const refTemperature = region?.refTemperature ?? 300;
  useEffect(() => {
    setReadout("");
    if (materialId === null) return;
    let live = true;
    void materialReadout(materialId, refTemperature)
      .then((text) => live && setReadout(text))
      .catch(() => live && setReadout(""));
    return () => {
      live = false;
    };
  }, [materialId, refTemperature]);

  if (body === null) {
    return (
      <GuidedCard step={step}>
        <Note tip="Mark one body as Fluid in the Geometry step: the flow fills that body.">No fluid body yet</Note>
        <Button onClick={() => selectStep("geometry")} data-ws="guided-to-geometry">
          Back to Geometry
        </Button>
      </GuidedCard>
    );
  }

  const pick = (fluid: string) => {
    const choice: QuickMaterial | undefined = QUICK_MATERIALS.find((m) => m.fluid === fluid);
    if (choice !== undefined) void action.run(() => assignLibraryMaterial(body.id, choice));
  };

  /** Browse: the Material menu works on the picked bodies, so the fluid body is picked for it. */
  const browse = (button: HTMLElement) => {
    const state = useStore.getState();
    state.setPickMode("body");
    state.pickBody(body.id, "replace");
    menu.open(button);
  };

  return (
    <GuidedCard step={step}>
      <p className="guided-line">
        <span className="muted">Fluid body</span> <strong data-ws="guided-fluid-body">{body.name}</strong>
      </p>
      <Chips<string> label="Material" value={region?.libraryId ?? null} choices={CHOICES} busy={action.busy} onChange={pick} />
      <Button onClick={(e) => browse(e.currentTarget)} disabled={action.busy} data-ws="guided-browse-materials">
        Browse the library…
      </Button>
      {region?.materialName != null ? (
        <div className="guided-readout" data-ws="guided-material">
          <strong>{region.materialName}</strong>
          {readout !== "" && <span className="muted">{readout}</span>}
        </div>
      ) : (
        <Note tip={`Water and Air are one press each; Browse opens the whole library for ${body.name}.`}>Choose the fluid for {body.name}</Note>
      )}
      <FlowOptions />
      <ErrorLine text={action.error} />
      {menu.menu}
      {menu.dialog}
    </GuidedCard>
  );
}
