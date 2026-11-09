/**
 * Run: how many iterations, Run and Stop, a small residual chart while it goes, and how it ended.
 *
 * Run and Stop are the top bar's own button (`pressRun`), the state line and the bar are the Solve panel's
 * (`stateLine`, `progressFraction`), and the iteration limit is the solver settings' `iterations`, changed by
 * the same `solve.configure` the Solve panel uses.
 */
import { useMemo, useState } from "react";
import { CircleAlert, CircleCheck, CircleHelp, CircleX, LoaderCircle, Play, Square } from "lucide-react";
import { Button } from "../../components/ui";
import ResidualPlot from "../../panels/ResidualPlot";
import { isRunActive } from "../../panels/runApi";
import { configureSolve, pressRun, resumeRun, useRunButton, useRunContext } from "../../panels/runControl";
import { canResume, findSolverSettings, iterationLimit, progressFraction, residualTarget, type Tone } from "../../panels/runPanelModel";
import { useStore } from "../../state/store";
import GuidedCard from "../GuidedCard";
import { ErrorLine, Note } from "../controls";
import { coresOf, setCores } from "../commands";
import { runLine, type GuidedStep } from "../guidedModel";
import { readCase } from "../guidedTree";
import { CASE_PICKS, CaseQuickSettings } from "./CaseSettingsCard";
import { useAction } from "../useAction";

const TONE_ICON: Record<Tone, typeof CircleCheck> = { neutral: CircleHelp, busy: LoaderCircle, ok: CircleCheck, warn: CircleAlert, bad: CircleX };

export default function RunCard({ step }: { step: GuidedStep }) {
  const tree = useStore((s) => s.tree);
  const run = useStore((s) => s.run.runs[0] ?? null);
  const residuals = useStore((s) => s.run.residuals);
  const residualRun = useStore((s) => s.run.residualRun);
  const starting = useRunContext((c) => c.busy);
  const runError = useRunContext((c) => c.error);
  const button = useRunButton();
  const action = useAction();
  const [draft, setDraft] = useState<string | null>(null);
  const [coresDraft, setCoresDraft] = useState<string | null>(null);

  const settings = useMemo(() => findSolverSettings(tree), [tree]);
  // An imported case runs its own controlDict, so the model's Iterations would change nothing it
  // runs; its end time is in the case's own settings above. Cores is the solve's for both.
  const caseId = useMemo(() => readCase(tree)?.id ?? null, [tree]);
  const isCase = caseId !== null;
  const limit = isCase ? null : iterationLimit(settings);
  const target = residualTarget(settings);
  const line = runLine(run, limit);
  const fraction = progressFraction(run, limit);
  const active = run !== null && isRunActive(run.state);
  const stopping = button.label === "Stop";
  const Icon = TONE_ICON[line.tone];
  const hasSeries = residuals !== null && residuals.iterations.length > 0 && residualRun === run?.id;
  const indeterminate = active && (fraction === null || fraction === 0);

  const commit = () => {
    const text = draft;
    setDraft(null);
    if (text === null) return;
    const n = Number(text.trim());
    if (!Number.isInteger(n) || n < 1) {
      action.fail("Iterations is a whole number, 1 or more.");
      return;
    }
    if (n === limit) return;
    void action.run(async () => {
      if (settings === null) await configureSolve({});
      await configureSolve({ values: { iterations: n } });
    });
  };

  const cores = coresOf(settings);
  const commitCores = () => {
    const text = coresDraft;
    setCoresDraft(null);
    if (text === null) return;
    const n = Number(text.trim());
    if (!Number.isInteger(n) || n < 1 || n > 256) {
      action.fail("Cores is a whole number from 1 to 256.");
      return;
    }
    if (n !== cores) void action.run(() => setCores(n));
  };

  return (
    <GuidedCard step={step}>
      {isCase && caseId !== null && <CaseQuickSettings caseId={caseId} picks={CASE_PICKS.run} />}
      {!isCase && <>
      <label className="guided-field">
        <span>Iterations</span>
        <input
          className="field"
          inputMode="numeric"
          spellCheck={false}
          autoComplete="off"
          aria-label="Number of iterations"
          disabled={active || action.busy}
          value={draft ?? (limit === null ? "" : String(limit))}
          placeholder="default"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") setDraft(null);
          }}
          data-ws="guided-iterations"
        />
      </label>
      </>}
      <label className="guided-field">
        <span>Cores</span>
        <input
          className="field"
          inputMode="numeric"
          spellCheck={false}
          autoComplete="off"
          aria-label="Number of processor cores"
          title={
            isCase
              ? "Processor cores the solve uses: the case is split into this many parts and solved over MPI (a single foamRun solve, or the case's own parallel steps). Meshing stays on one core."
              : "Processor cores the solve uses. More cores solve faster; 1 is the safe choice on a laptop."
          }
          disabled={active || action.busy}
          value={coresDraft ?? (cores === null ? "1" : String(cores))}
          onChange={(e) => setCoresDraft(e.target.value)}
          onBlur={commitCores}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") setCoresDraft(null);
          }}
          data-ws="guided-cores"
        />
      </label>
      <div className="guided-actions">
        <Button
          variant={stopping ? "default" : "primary"}
          aria-disabled={!button.enabled || undefined}
          title={button.enabled ? (stopping ? "Stop the run" : "Run the simulation") : button.reason}
          onClick={() => button.enabled && pressRun()}
          data-ws="guided-run"
        >
          {stopping ? <Square size={13} fill="currentColor" aria-hidden="true" /> : <Play size={13} fill="currentColor" aria-hidden="true" />}
          {starting && !stopping ? "Starting…" : button.label}
        </Button>
        {!active && canResume(run, limit) && (
          <Button
            aria-disabled={starting || undefined}
            title={`Continue from iteration ${run?.iterations ?? 0} to ${limit ?? "the limit"}, on the saved fields`}
            onClick={() => !starting && void resumeRun()}
            data-ws="guided-resume"
          >
            <Play size={13} aria-hidden="true" />
            Resume
          </Button>
        )}
      </div>
      {!button.enabled && !stopping && <Note>{button.reason}</Note>}
      <div className="mesh-state guided-state" data-tone={line.tone} aria-live="polite" data-ws="guided-run-state">
        <Icon size={14} aria-hidden="true" className={line.tone === "busy" ? "mesh-spin" : ""} />
        <span>{line.text}</span>
      </div>
      {active && (
        <div className="mesh-bar" role="progressbar" aria-label="Run progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={indeterminate || fraction === null ? undefined : Math.round(fraction * 100)} data-indeterminate={indeterminate} data-tone={line.tone}>
          <span style={{ width: `${Math.round((fraction ?? 0) * 100)}%` }} />
        </div>
      )}
      {hasSeries && residuals !== null && (
        <div className="guided-spark" data-ws="guided-residuals">
          <ResidualPlot series={residuals} target={target} fill />
        </div>
      )}
      <ErrorLine text={runError !== "" ? runError : action.error} />
    </GuidedCard>
  );
}
