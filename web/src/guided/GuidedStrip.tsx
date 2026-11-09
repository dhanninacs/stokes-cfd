/**
 * The flow strip: `Geometry › Fluid › Boundaries › Mesh › Run › Results`, each step with a state dot and a
 * one-line hint, the step whose card is open marked, and the Guided / Full switch at the end.
 *
 * It sits at the top of the viewport in both modes. A press opens that step's card in Guided mode; in Full
 * mode (no cards) it brings the step's stage in front, which the tree follows.
 */
import { AlertCircle, Check, LoaderCircle } from "lucide-react";
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

function Dot({ state }: { state: StepState }) {
  return (
    <span className="guided-dot" data-state={state} aria-hidden="true">
      {state === "done" && <Check size={10} strokeWidth={3.25} />}
      {state === "attention" && <AlertCircle size={13} />}
      {state === "running" && <LoaderCircle size={13} className="mesh-spin" />}
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
          {steps.map((step) => (
            <li key={step.id} className="guided-step-item">
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
                <Dot state={step.state} />
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
