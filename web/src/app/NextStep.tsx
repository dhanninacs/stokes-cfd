/**
 * The "Next step" button at the end of the stage bar (T-219 C): Import geometry, Define boundaries,
 * Generate mesh, Run, Show results, whichever the case needs next. What is next, and why a step is blocked, is
 * `nextStep.ts` (tested); this file reads the stores, draws the button and presses the step: it goes to the
 * stage the step belongs to and does the one thing the label says.
 *
 * A blocked step keeps its tooltip and says why in it, and pressing it toasts the same reason, as the Run
 * button does: `aria-disabled`, not `disabled`, so the tooltip still opens and the button stays in the tab order.
 */
import { useMemo } from "react";
import { ArrowRight } from "lucide-react";
import { findMeshNodes, isStale, phaseNow, readResult } from "../panels/meshPanelModel";
import { generateMesh, useGenerateState } from "../panels/meshRun";
import { openPanel } from "../panels/panelOpen";
import { showResults } from "../panels/resultsControl";
import { pressRun, useRunButton } from "../panels/runControl";
import { latestProgress } from "../state/parts";
import { useStore } from "../state/store";
import { Tooltip } from "../components/Tooltip";
import { runAction } from "./actions";
import { nextStep, type NextStep } from "./nextStepModel";
import { useUpload } from "./upload";
import { hasParts } from "./uploadModel";

/**
 * The guided flow's `openGuidedStep`, when `../guided` exists and exports it. Looked up with a glob rather
 * than a named import so a build without the module still resolves (the glob is then empty) and the
 * button keeps today's behaviour; the module is loaded only when a step asks for it.
 */
const guidedModules = import.meta.glob<{ openGuidedStep?: (id: string) => unknown }>("../guided/index.{ts,tsx}");

/** Opens a guided step; false when there is no guided module or export, or it declines (full mode). */
async function openGuided(id: string): Promise<boolean> {
  const load = Object.values(guidedModules)[0];
  if (load === undefined) return false;
  try {
    const module = await load();
    if (typeof module.openGuidedStep !== "function") return false;
    return module.openGuidedStep(id) !== false;
  } catch {
    return false;
  }
}

/** Goes to the step's stage and does what its label says. A blocked step only says why. */
function performStep(step: NextStep): void {
  const state = useStore.getState();
  if (!step.enabled) {
    state.pushToast("info", step.reason);
    return;
  }
  switch (step.id) {
    case "import":
      runAction("project.importStep");
      return;
    case "define":
      // In guided mode this opens the Boundaries card; without it, the Conditions panel as before.
      void openGuided("boundaries").then((opened) => {
        if (opened) return;
        const now = useStore.getState();
        if (now.stage !== "Physics") now.setStage("Physics");
        openPanel({ panel: "conditions" });
      });
      return;
    case "mesh":
      if (state.stage !== "Mesh") state.setStage("Mesh");
      void generateMesh();
      return;
    case "run":
      if (state.stage !== "Solve") state.setStage("Solve");
      pressRun();
      return;
    case "results":
      showResults();
      return;
    default:
      return;
  }
}

/**
 * The stage's next step, as the chip computes it.
 *
 * **Exported so the Details pane can show the same thing** (the layout decision: on an empty selection
 * the pane shows the stage's "what to do next" checklist). It was inline in the button; a second copy
 * in the pane would be two answers to one question, and they would drift the first time a step was
 * added.
 */
export function useNextStep(): NextStep {
  const project = useStore((s) => s.project);
  const tree = useStore((s) => s.tree);
  const setup = useStore((s) => s.setup);
  const live = useStore((s) => s.mesh);
  const runs = useStore((s) => s.run.runs);
  const stage = useStore((s) => s.stage);
  const resultsLoaded = useStore((s) => s.results.data !== null);
  const importProgress = useStore((s) => s.importProgress);
  const uploading = useUpload((u) => u.phase === "uploading");
  const generate = useGenerateState();
  const runButton = useRunButton();

  const step = useMemo(() => {
    const view = readResult(findMeshNodes(tree).result);
    return nextStep({
      hasProject: project !== null,
      hasParts: hasParts(tree),
      importing: uploading || latestProgress(importProgress) !== null,
      setup,
      meshPhase: phaseNow(view, live),
      meshStale: isStale(view),
      generate,
      runs,
      runButton,
      resultsShown: stage === "Results" && resultsLoaded,
    });
  }, [project, tree, uploading, importProgress, setup, live, generate, runs, runButton, stage, resultsLoaded]);

  return step;
}

export default function NextStepButton() {
  const step = useNextStep();
  return (
    /*
      **The tooltip is the REASON, not "label. reason"** (claude-win2's review item 9: *"the same
      sentence prints twice (heading and sub-text) on those cards"*). The chip already shows the
      label, so repeating it in the tooltip spent the first half of a short space on words the user is
      looking at - and made the reason, which is the part they do not know, start mid-tooltip.
    */
    <Tooltip label={step.reason} side="bottom" describe={false}>
      <button
        type="button"
        className="chip next-step no-drag"
        aria-label={`Next step: ${step.label}`}
        aria-disabled={!step.enabled || undefined}
        data-step={step.id}
        data-ws="next-step"
        onClick={() => performStep(step)}
      >
        <span className="chip-label next-step-label" data-ws="next-step-label">
          {step.label}
        </span>
        <ArrowRight size={14} aria-hidden="true" />
      </button>
    </Tooltip>
  );
}
