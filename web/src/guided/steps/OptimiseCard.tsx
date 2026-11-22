/**
 * Optimise, the optional last step: two doors out of the flow into the Explore tools. Shape optimisation
 * opens the Optimise panel (it needs a body made with Create: only such a body has dimensions to change),
 * and a parametric study opens a new-study form or, once the project has one, its design table and plots.
 *
 * The card holds no state of its own and decides nothing: the Optimise panel and the Explore panels do the
 * work, the status line reads the small store the strip's step reads (`optimiseStatus`), and "New study…" runs
 * the registered `study.new` action, so the form is the one the palette opens.
 */
import { Button } from "../../components/ui";
import { Tooltip } from "../../components/Tooltip";
import { runAction } from "../../app/actions";
import { implicitParts } from "../../panels/explore/optimise";
import { useOptimiseStatus } from "../../panels/explore/optimiseStatus";
import { listStudies } from "../../panels/explore/study";
import { openPanelById } from "../../panels/paletteItems";
import { useStore } from "../../state/store";
import GuidedCard from "../GuidedCard";
import { plural, type GuidedStep } from "../guidedModel";

/** What the shape block says about the optimisation in this session, or "" before there is anything to say. */
export function shapeStatusLine(s: { running: boolean; cycles: number; finished: boolean }): string {
  if (s.running) return `Cycle ${s.cycles + 1} running`;
  if (s.finished) return `Finished after ${plural(s.cycles, "cycle")}`;
  return "";
}

export default function OptimiseCard({ step }: { step: GuidedStep }) {
  const bodies = useStore((s) => implicitParts(s.tree).length);
  const studies = useStore((s) => listStudies(s.tree).length);
  const shape = useOptimiseStatus();
  const line = shapeStatusLine(shape);
  const blocked = bodies === 0 ? "Needs a body made with Create: an imported CAD body has no dimensions to change" : undefined;
  return (
    <GuidedCard step={step}>
      <section className="guided-block" aria-label="Shape optimisation">
        <h3 className="guided-block-title">Shape optimisation</h3>
        <p className="guided-line muted">Changes dimensions of the shape to lower the pressure loss or balance a flow split, with gradients from the adjoint or finite differences.</p>
        <Tooltip label={blocked ?? "Choose the dimensions to change and start the cycles"} side="top">
          <Button variant="primary" aria-disabled={blocked !== undefined || undefined} onClick={() => blocked === undefined && openPanelById("optimise")} data-ws="guided-optimise-shape">
            Optimise the shape…
          </Button>
        </Tooltip>
        {line !== "" && (
          <p className="guided-line" data-ws="guided-optimise-status" role="status">
            {line}
          </p>
        )}
      </section>
      <section className="guided-block" aria-label="Parametric study">
        <h3 className="guided-block-title">Parametric study</h3>
        <p className="guided-line muted">Vary parameters over a design table and compare the runs.</p>
        {studies > 0 && (
          <p className="guided-line" data-ws="guided-study-count">
            <strong>{plural(studies, "study", "studies")}</strong>
          </p>
        )}
        {studies > 0 && (
          <div className="guided-actions">
            <Button onClick={() => openPanelById("design-table")} data-ws="guided-study-table">
              Design table
            </Button>
            <Button onClick={() => openPanelById("plots")} data-ws="guided-study-plots">
              Plots
            </Button>
          </div>
        )}
        <Button onClick={() => runAction("study.new")} data-ws="guided-study-new">
          New study…
        </Button>
      </section>
    </GuidedCard>
  );
}
