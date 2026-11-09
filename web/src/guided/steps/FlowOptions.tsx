/**
 * The Fluid card's flow options: laminar or turbulent, and heat transfer.
 *
 * **Why they are here and not only in Full mode.** The mixing-elbow walkthrough (a Fluent tutorial case:
 * cold and hot inlets, a turbulent flow) could not be set up in Guided mode at all: the two choices that
 * decide what the solver solves, whether the flow is turbulent and whether temperature matters, were two
 * pages deep in the Solve settings. They are the same `solve.configure` the settings page sends, so the
 * card and the page cannot disagree; everything finer (the model's constants, the initial turbulence, time
 * stepping) stays in Full mode.
 */
import { useMemo } from "react";
import { useStore } from "../../state/store";
import { readSetupContext } from "../../setup/basicSets";
import { Chips, ErrorLine, Note, type Choice } from "../controls";
import { FLOW_MODELS, setFlowModel, setHeatTransfer } from "../commands";
import { useAction } from "../useAction";

const MODEL_CHOICES: readonly Choice<string>[] = FLOW_MODELS.map((m) => ({ value: m.value, label: m.label, sub: m.sub }));
const HEAT_CHOICES: readonly Choice<"off" | "on">[] = [
  { value: "off", label: "Off", sub: "flow only" },
  { value: "on", label: "On", sub: "solve temperature" },
];

/** True for a model the card offers as its own chip; any other model (set in Full mode) shows as "Other". */
function isOffered(model: string): boolean {
  return FLOW_MODELS.some((m) => m.value === model);
}

export default function FlowOptions() {
  const tree = useStore((s) => s.tree);
  const context = useMemo(() => readSetupContext(tree), [tree]);
  const action = useAction();

  return (
    <div className="guided-options" data-ws="guided-flow-options">
      <span className="guided-cap">Flow</span>
      <Chips<string>
        label="Flow"
        value={isOffered(context.turbulence) ? context.turbulence : null}
        choices={MODEL_CHOICES}
        busy={action.busy}
        onChange={(model) => void action.run(() => setFlowModel(model))}
        wide
      />
      {!isOffered(context.turbulence) && <Note tip="Set in the Solve settings (Full mode); pick one above to replace it.">Flow model: {context.turbulence}</Note>}
      <span className="guided-cap">Heat transfer</span>
      <Chips<"off" | "on">
        label="Heat transfer"
        value={context.energy ? "on" : "off"}
        choices={HEAT_CHOICES}
        busy={action.busy}
        onChange={(value) => void action.run(() => setHeatTransfer(value === "on"))}
      />
      {context.energy && <Note tip="With heat transfer on, each inlet takes a temperature: set it in the Boundaries step.">Inlets take a temperature</Note>}
      <ErrorLine text={action.error} />
    </div>
  );
}
