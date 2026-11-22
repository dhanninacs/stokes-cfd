/**
 * The flow strip: `Geometry › Fluid › Boundaries › Mesh › Run › Results › Optimise`, each step a compact
 * segment with its mark (index, or a glyph once done / needing attention / running), its label, and a
 * 2 px segment of the progress rail under it in the state's colour; the step whose card is open is marked,
 * and the Guided / Full switch sits at the end. The optional Optimise step is set apart with a dashed rail.
 *
 * It sits at the top of the viewport in both modes. A press opens that step's card in Guided mode; in Full
 * mode (no cards) it brings the step's stage in front, which the tree follows.
 */
import { AlertTriangle, Check, LoaderCircle } from "lucide-react";
import { Segmented } from "../components/ui";
import { useStore } from "../state/store";
import { STEP_STAGE, type GuidedStep, type GuidedStepId, type StepState } from "./guidedModel";
import type { GuidedMode } from "./modeModel";
import { chooseMode, selectStep } from "./modeStore";
import { arrangeFor } from "./treeToggle";

const STATE_WORDS: Record<StepState, string> = {
  todo: "not started",
  attention: "needs attention",
  done: "done",
  running: "running",
};

/** The step's mark: its index in tabular mono figures until something has happened, then a glyph that says what. */
function Mark({ state, index }: { state: StepState; index: number }) {
  return (
    <span className="guided-dot" data-state={state} aria-hidden="true">
      {state === "done" && <Check size={12} strokeWidth={3} />}
      {state === "attention" && <AlertTriangle size={12} strokeWidth={2.5} />}
      {state === "running" && <LoaderCircle size={12} strokeWidth={2.5} className="mesh-spin" />}
      {state === "todo" && String(index + 1).padStart(2, "0")}
    </span>
  );
}

function press(id: GuidedStepId, mode: GuidedMode): void {
  if (mode === "guided") {
    selectStep(id);
    return;
  }
  const state = useStore.getState();
  if (state.stage !== STEP_STAGE[id]) state.setStage(STEP_STAGE[id]);
}

export default function GuidedStrip({ steps, current, mode }: { steps: readonly GuidedStep[]; current: GuidedStepId | null; mode: GuidedMode }) {
  return (
    <div className="guided-strip-wrap">
      <nav className="guided-strip glass" aria-label="Set-up steps" data-ws="guided-strip" data-mode={mode}>
        <ol className="guided-steps">
          {steps.map((step, index) => (
            <li key={step.id} className="guided-step-item" data-state={step.state} data-optional={step.id === "optimise" ? "" : undefined}>
              <button
                type="button"
                className="guided-step no-drag"
                aria-current={step.id === current ? "step" : undefined}
                aria-label={`${step.label}, ${STATE_WORDS[step.state]}. ${step.hint}`}
                data-step={step.id}
                data-state={step.state}
                title={step.hint}
                onClick={() => press(step.id, mode)}
              >
                <Mark state={step.state} index={index} />
                <span className="guided-step-text">
                  <span className="guided-step-label">{step.label}</span>
                  <span className="guided-step-hint">{step.hint}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
        <Segmented<GuidedMode>
          label="Layout"
          value={mode}
          options={[
            { value: "guided", label: "Guided" },
            { value: "full", label: "Full" },
          ]}
          onChange={(next) => {
            chooseMode(next);
            arrangeFor(next);
          }}
        />
      </nav>
    </div>
  );
}
