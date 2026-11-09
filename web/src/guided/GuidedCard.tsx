/**
 * The frame every step's card shares: the step's number and name, its one-line hint, a fold button, the
 * card's own controls, and, once the step is done, one button to the next step.
 *
 * Small on purpose: 300 px wide, two to five controls, no scrolling at 900 px tall.
 */
import type { ReactNode } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "../components/ui";
import { GUIDED_STEPS, nextStepId, type GuidedStep } from "./guidedModel";
import { selectStep, useGuided } from "./modeStore";

const WORDS: Record<GuidedStep["id"], string> = {
  geometry: "Geometry",
  fluid: "Fluid",
  boundaries: "Boundaries",
  mesh: "Mesh",
  run: "Run",
  results: "Results",
};

export default function GuidedCard({ step, children }: { step: GuidedStep; children: ReactNode }) {
  const folded = useGuided((s) => s.folded);
  const next = nextStepId(step.id);
  const number = GUIDED_STEPS.indexOf(step.id) + 1;
  return (
    <section className="guided-card glass fade-in" data-ws="guided-card" data-step={step.id} data-state={step.state} aria-label={`Step ${number}: ${WORDS[step.id]}`}>
      <header className="guided-card-head">
        <span className="guided-card-num" aria-hidden="true">
          {number}
        </span>
        <div className="guided-card-titles">
          <h2 className="guided-card-title">{WORDS[step.id]}</h2>
          <p className="guided-card-hint muted" data-ws="guided-card-hint">
            {step.hint}
          </p>
        </div>
        <button
          type="button"
          className="guided-fold no-drag"
          aria-label={folded ? "Open the card" : "Fold the card"}
          aria-expanded={!folded}
          title={folded ? "Open the card" : "Fold the card"}
          onClick={() => useGuided.setState({ folded: !folded })}
        >
          {folded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronUp size={14} aria-hidden="true" />}
        </button>
      </header>
      {!folded && (
        <>
          <div className="guided-card-body">{children}</div>
          {next !== null && step.state === "done" && (
            <footer className="guided-card-foot">
              <Button variant="primary" onClick={() => selectStep(next)} data-ws="guided-next">
                Next: {WORDS[next]}
              </Button>
            </footer>
          )}
        </>
      )}
    </section>
  );
}
