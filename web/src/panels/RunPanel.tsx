/**
 * The Solve panel (T-217): the Run and Stop button with the reason it is disabled, the state and the
 * progress of the newest run, the residual chart, the result, the job log and the solver settings, a
 * tab beside Properties, Mesh and Conditions in the right slot.
 *
 * Everything that decides something is `runPanelModel.ts` (states, labels, button enablement and its
 * reason, tested); `runControl.ts` presses the buttons and keeps the state true; the names and shapes of
 * the commands are `runApi.ts`. This file only draws. The settings are the server's node
 * (`/solver/settings`), every edit is one `solve.configure` (one undo step), and the `solve.check`
 * findings, with their suggestions, are what Run says when it cannot start.
 *
 * Importers must write `./SchemaFields.tsx` with the extension: on Windows `./SchemaFields` resolves to
 * the helpers' `schemaFields.ts`.
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronRight, CircleAlert, CircleCheck, CircleHelp, CircleX, LoaderCircle, Play, Square } from "lucide-react";
import { StokesApiError } from "../api/client";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import { placeOf } from "../workspace/geometry";
import { useWorkspace } from "../workspace/store";
import JobLogView from "./JobLogView";
import ResidualPlot from "./ResidualPlot";
import { isRunActive } from "./runApi";
import SettingsNodeFields from "./SettingsNodeFields";
import { SOLVER_LANDING_SECTION, findingsTouch } from "./settingsPresentation";
import { showResults } from "./resultsControl";
import { configureSolve, loadEngines, pressRun, resumeRun, useIterationLimit, useRunButton, useRunContext } from "./runControl";
import { canResume, findSolverSettings, formatResidual, formatRunSeconds, progressFraction, residualTarget, stateLine, type Tone } from "./runPanelModel";
import "./runPanel.css";

const TONE_ICON: Record<Tone, typeof CircleCheck> = { neutral: CircleHelp, busy: LoaderCircle, ok: CircleCheck, warn: CircleAlert, bad: CircleX };

function messageOf(err: unknown): string {
  return err instanceof StokesApiError ? err.message : err instanceof Error ? err.message : String(err);
}

export default function RunPanel({ section }: { section?: string } = {}) {
  // The landing section is the whole Solve page; any other solver section is just its settings, Advanced open at it.
  const settingsOnly = section?.startsWith("solver-") === true && section !== SOLVER_LANDING_SECTION;
  const monitoringOnly = section === "run-monitor";
  const tree = useStore((s) => s.tree);
  const connected = useStore((s) => s.connected);
  const stage = useStore((s) => s.stage);
  const nodeTypes = useStore((s) => s.nodeTypes);
  const run = useStore((s) => s.run.runs[0] ?? null);
  const check = useStore((s) => s.run.check);
  const residuals = useStore((s) => s.run.residuals);
  const residualRun = useStore((s) => s.run.residualRun);
  const logLines = useStore((s) => s.run.jobLog.length);
  const engines = useRunContext((c) => c.engines);
  const loaded = useRunContext((c) => c.loaded);
  const busy = useRunContext((c) => c.busy);
  const runError = useRunContext((c) => c.error);
  const button = useRunButton();
  const limit = useIterationLimit();

  const settings = useMemo(() => findSolverSettings(tree), [tree]);
  const target = residualTarget(settings);
  const line = stateLine(run, limit);
  const fraction = progressFraction(run, limit);
  const active = run !== null && isRunActive(run.state);
  const indeterminate = active && (fraction === null || fraction === 0);
  const Icon = TONE_ICON[line.tone];
  const stopping = button.label === "Stop";

  const [logOpen, setLogOpen] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // What the server says about engines is asked for when the connection comes up; a panel mounted after that asks again.
  useEffect(() => {
    if (connected && !useRunContext.getState().loaded) void loadEngines();
  }, [connected]);

  // In the Solve stage this is the tab in front, wherever the workspace put it (the slot's other tabs stay).
  useEffect(() => {
    if (stage !== "Solve") return;
    const workspace = useWorkspace.getState();
    const place = placeOf(workspace.document.layout, "solve");
    if (place === "left" || place === "right" || place === "bottom") workspace.setActiveTab(place, "solve");
  }, [stage]);

  // ---- settings edits: each is one `solve.configure`, so one undo step.
  const schema = nodeTypes.solver_settings;
  const configure = async (params: Record<string, unknown>, field: string | null) => {
    setSettingsError("");
    if (field !== null) setFieldErrors((e) => ({ ...e, [field]: "" }));
    try {
      await configureSolve(params);
    } catch (err) {
      if (field !== null) setFieldErrors((e) => ({ ...e, [field]: messageOf(err) }));
      else setSettingsError(messageOf(err));
      throw err;
    }
  };
  // The first thing wrong is Run's own reason, said once above; the list is what else the check found.
  const findings = (check?.findings ?? []).filter((f) => (f.suggestion !== "" ? f.suggestion : f.message) !== button.reason);
  const finals = run?.last ?? [];
  const hasSeries = residuals !== null && residuals.iterations.length > 0 && residualRun === run?.id;

  return (
    <div className="mesh-panel" data-ws="run-panel" data-state={run?.state ?? "none"}>
      {!settingsOnly && <>
      <section className="mesh-section" aria-label="Run">
        <div className="mesh-state" data-tone={line.tone} aria-live="polite" data-ws="run-state">
          <Icon size={14} aria-hidden="true" className={line.tone === "busy" ? "mesh-spin" : ""} />
          <span data-ws="run-state-text">{line.text}</span>
        </div>
        <div
          className="mesh-bar"
          role="progressbar"
          aria-label="Run progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={indeterminate || fraction === null ? undefined : Math.round(fraction * 100)}
          aria-valuetext={line.text}
          data-indeterminate={indeterminate}
          data-tone={line.tone}
        >
          <span style={{ width: `${Math.round((fraction ?? 0) * 100)}%` }} />
        </div>
        <div className="mesh-actions">
          <Button
            variant={stopping ? "ghost" : "primary"}
            aria-label={stopping ? "Stop run" : "Run simulation"}
            aria-disabled={!button.enabled || undefined}
            title={button.enabled ? (stopping ? "Stop the run" : "Run the simulation") : `${button.label} · ${button.reason}`}
            onClick={() => button.enabled && pressRun()}
            data-ws="run-button"
          >
            {stopping ? <Square size={13} fill="currentColor" /> : <Play size={13} fill="currentColor" />}
            {busy && !stopping ? "Starting…" : button.label}
          </Button>
        </div>
        {!button.enabled && !stopping && (
          <p className="muted mesh-hint" data-ws="run-why">
            {button.reason}
          </p>
        )}
        {findings.length > 0 && !stopping && (
          <ul className="run-why" aria-label="What the check found" data-ws="run-findings">
            {findings.map((f) => (
              <li key={`${f.code}:${f.nodes.join(",")}`} data-severity={f.severity}>
                <CircleAlert size={13} aria-hidden="true" />
                <span>{f.suggestion !== "" ? f.suggestion : f.message}</span>
              </li>
            ))}
          </ul>
        )}
        {runError !== "" && (
          <p role="alert" className="mesh-error" data-ws="run-error">
            {runError}
          </p>
        )}
      </section>

      <section className="mesh-section" aria-label="Residuals" data-ws="run-residuals">
        <div className="section-title">Residuals</div>
        {hasSeries ? (
          <ResidualPlot series={residuals} target={target} />
        ) : (
          <p className="muted mesh-hint" title="The residuals appear here once a run starts. The dashed line is the convergence target.">No run yet</p>
        )}
      </section>

      {run !== null && !active && (
        <section className="mesh-section mesh-result" aria-label="Result" data-ws="run-result">
          <div className="section-title">Result</div>
          <dl className="mesh-counts">
            <div>
              <dt className="muted">Iterations</dt>
              <dd data-ws="run-iterations">{run.iterations}</dd>
            </div>
            <div>
              <dt className="muted">Time</dt>
              <dd>{formatRunSeconds(run.seconds) || "-"}</dd>
            </div>
            <div>
              <dt className="muted">Host</dt>
              <dd>{run.host !== "" ? run.host : "-"}</dd>
            </div>
          </dl>
          {(run.state === "done" || run.state === "done_unconverged") && (
            <div className="mesh-actions">
              <Button onClick={() => showResults()} title="Show the fields of this run on the mesh in the Results stage" data-ws="run-show-results">
                Show results
              </Button>
              {canResume(run, limit) && (
                <Button onClick={() => void resumeRun()} title={`Continue from iteration ${run.iterations} on the saved fields`} data-ws="run-resume">
                  Resume
                </Button>
              )}
            </div>
          )}
          {finals.length > 0 && (
            <dl className="run-final" aria-label="Final residuals" data-ws="run-final">
              {finals.map((f) => (
                <div key={f.name}>
                  <dt>{f.name}</dt>
                  <dd>{formatResidual(f.value)}</dd>
                </div>
              ))}
            </dl>
          )}
        </section>
      )}

      </>}
      {!monitoringOnly && <section className="mesh-section" aria-label="Solve settings" data-ws="run-settings">
        <div className="section-title">Solve settings</div>
        {settingsError !== "" && (
          <p role="alert" className="mesh-error">
            {settingsError}
          </p>
        )}
        {settings === null ? (
          <div className="mesh-empty">
            <p className="muted m-0 text-[12px]" title="Create them to choose the engine, the iterations and the convergence target, or press Run to see what is missing.">No solve settings yet</p>
            <Button disabled={!connected || schema === undefined || active} onClick={() => void configure({}, null).catch(() => undefined)} data-ws="run-create">
              Create settings
            </Button>
          </div>
        ) : schema === undefined ? (
          <p className="muted mesh-hint">Solver settings unknown</p>
        ) : (
          <div className="mesh-fields">
            <SettingsNodeFields
              key={settings.id}
              node={settings}
              schema={schema}
              mode="solver"
              layout="basic"
              openAdvanced={findingsTouch(check?.findings ?? [], settings.id)}
              initialSection={settingsOnly ? section : undefined}
              onCommit={(name, value) => configure({ values: { [name]: value } }, name)}
              onEdit={(name) => setFieldErrors((e) => ({ ...e, [name]: "" }))}
              tree={tree}
              engines={engines}
              enginesLoaded={loaded}
              disabled={!connected || active}
              errors={fieldErrors}
            />
          </div>
        )}
      </section>}

      {!settingsOnly && <section className="mesh-section" aria-label="Job log">
        <button type="button" className="mesh-log-toggle" aria-expanded={logOpen} aria-controls="run-log-body" onClick={() => setLogOpen((o) => !o)} data-ws="run-log-toggle">
          <ChevronRight size={13} aria-hidden="true" style={{ transform: logOpen ? "rotate(90deg)" : "none" }} />
          <span className="section-title">Job log</span>
          <span className="muted text-[11.5px]">{logLines === 0 ? "empty" : `${logLines} lines`}</span>
        </button>
        {logOpen && (
          <div id="run-log-body">
            <JobLogView inPanel />
          </div>
        )}
      </section>}
    </div>
  );
}
