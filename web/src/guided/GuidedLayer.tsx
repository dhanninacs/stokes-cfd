/**
 * Guided mode on the viewport: the flow strip across the top, and under it at the left the open step's
 * card with the field legend below it. App mounts this once for an open project.
 *
 * Which card is open is the user's: it starts at the first step that is not done when the project opens, and
 * from then on moves only when the user presses a step or a card's Next button. Nothing here moves it on its
 * own, so a step that has just been finished (the boundaries, say) stays in front for the next face.
 *
 * In Full mode the strip stays and the cards go: the tree and Details are beside the view, and the legend is
 * the viewport's own.
 *
 * **The strip lives in the top bar** (owner, 2026-11-03: "the top part is not completely utilised"): the bar's
 * middle cell was empty whenever a project was open, while the strip took a second 52 px row over the viewport.
 * `TopBarStrip` draws it there from the same state (`useStripState`); this layer keeps the card and the legend.
 */
import { useEffect } from "react";
import { useStore } from "../state/store";
import { FieldLegend } from "../viewport/FieldLegend";
import GuidedStrip from "./GuidedStrip";
import type { GuidedStep, GuidedView } from "./guidedModel";
import { selectStep, useGuided, useGuidedMode } from "./modeStore";
import { useTreeAwayOnce } from "./treeToggle";
import { useGuidedView } from "./useGuidedView";
import { readCase } from "./guidedTree";
import BoundariesCard from "./steps/BoundariesCard";
import CaseSettingsCard from "./steps/CaseSettingsCard";
import FluidCard from "./steps/FluidCard";
import GeometryCard from "./steps/GeometryCard";
import MeshCard from "./steps/MeshCard";
import ResultsCard from "./steps/ResultsCard";
import RunCard from "./steps/RunCard";
import "./guided.css";

function Card({ step }: { step: GuidedStep }) {
  // A tutorial (an imported case) edits its own dictionaries on the Fluid and Boundaries steps; the CAD
  // cards' bodies, materials and face picks do not exist for it.
  const caseId = useStore((s) => readCase(s.tree)?.id ?? null);
  switch (step.id) {
    case "geometry":
      return <GeometryCard step={step} />;
    case "fluid":
      return caseId !== null ? <CaseSettingsCard step={step} caseId={caseId} which="fluid" /> : <FluidCard step={step} />;
    case "boundaries":
      return caseId !== null ? <CaseSettingsCard step={step} caseId={caseId} which="boundaries" /> : <BoundariesCard step={step} />;
    case "mesh":
      return <MeshCard step={step} />;
    case "run":
      return <RunCard step={step} />;
    case "results":
      return <ResultsCard step={step} />;
  }
}

function openStep(view: GuidedView, chosen: GuidedStep["id"] | null): GuidedStep | null {
  const id = chosen ?? view.active;
  return view.steps.find((s) => s.id === id) ?? null;
}

/** What the flow strip shows: the steps, the step whose card is open (Guided mode only), and the mode. */
export function useStripState(): { steps: readonly GuidedStep[]; current: GuidedStep["id"] | null; mode: ReturnType<typeof useGuidedMode> } {
  const mode = useGuidedMode();
  const view = useGuidedView();
  const chosen = useGuided((s) => s.step);
  const open = openStep(view, chosen);
  return { steps: view.steps, current: mode === "guided" ? open?.id ?? null : null, mode };
}

/** The flow strip in the top bar's middle cell, while a project is open (TopBar). */
export function TopBarStrip() {
  const { steps, current, mode } = useStripState();
  return <GuidedStrip steps={steps} current={current} mode={mode} />;
}

export default function GuidedLayer() {
  const mode = useGuidedMode();
  const view = useGuidedView();
  const chosen = useGuided((s) => s.step);
  const projectKey = useStore((s) => s.project?.path ?? s.project?.name ?? "");
  const treeLoaded = useStore((s) => s.tree !== null);
  useTreeAwayOnce();

  // A new project starts at its own first open step.
  useEffect(() => {
    useGuided.setState({ step: null, folded: false });
  }, [projectKey]);
  useEffect(() => {
    if (mode === "guided" && chosen === null && treeLoaded && projectKey !== "") selectStep(view.active);
  }, [mode, chosen, treeLoaded, projectKey, view.active]);

  // The rest of the viewport makes room for the strip (guided.css) while it is on screen, in both modes.
  useEffect(() => {
    document.documentElement.dataset.guided = mode;
    return () => {
      delete document.documentElement.dataset.guided;
    };
  }, [mode]);

  const open = openStep(view, chosen);
  return (
    <div className="guided-layer" data-ws="guided-layer" data-mode={mode}>
      {mode === "guided" && (
        <div className="guided-stack">
          {open !== null && <Card key={open.id} step={open} />}
          <FieldLegend />
        </div>
      )}
    </div>
  );
}
