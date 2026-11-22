/**
 * The Optimise panel (ADR-0038 decision 6, ADR-0040): gradient-based shape optimisation of a part made with
 * Create. Pick the part, the objective (reach a volume, pressure loss, or an equal flow split over chosen outlets
 * with a target), the gradient source (adjoint, or finite differences at N + 1 solves a cycle; the flow split's adjoint
 * only where `shape.gradients` finds its solver on the compute host, ADR-0041) and the variables (listed by name, "Sketch 1 · pull distance",
 * with the full reference on hover and under the row; or typed as expressions name them) with their
 * bounds in the modeller's units (mm, degrees); Start runs `shape.optimise`, whose cycles stream in as
 * `shape.cycle` events and draw the convergence of J and |grad J| (and, for the flow split, the largest deviation
 * from the equal share against the target) on numbered axes, with a per-cycle table, a per-outlet share table and
 * a CSV of the history; Apply runs `shape.apply`: the best point is set (one undo step), the part is rebuilt and
 * its boundary tags are found again on the new surface (`shape.applied` reports the end).
 *
 * A CFD cycle reports its steps (`shape.step`: the cycle, the step of six, the seconds since it began),
 * shown as one progress line with a bar; Stop after this cycle and Cancel this cycle (`shape.cancel`,
 * `now`) are on screen while it runs. The gradient check is its own run, shown as "Check 2/3", with its
 * own Cancel and its own result line; it never reuses the optimisation's cycle count or status.
 *
 * The panel holds no model state: the run is the server's and the history comes back from it. Every
 * reply and event is read through `optimise.ts`, which turns a missing array into an empty one.
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronsUpDown, Download, Gauge, Play, Plus, Square, Trash2, Wand2, X } from "lucide-react";
import type { ReactNode } from "react";
import { findNode, useStore } from "../../state/store";
import type { NodeSummary } from "../../api/types";
import { implicitShows, isImplicitOperation } from "../../state/implicitFields";
import { Button } from "../../components/ui";
import { saveTextFile } from "../monitorsExport";
import {
  bestCycle,
  boundText,
  checkVerdict,
  convergenceChart,
  cycleSensitivitySource,
  defaultGradient,
  displayUnit,
  durationText,
  fdSteps,
  formatValue,
  gradientSources,
  hasFlowSetup,
  historyCsv,
  implicitParts,
  objectiveUnit,
  objectiveWords,
  outletNames,
  parameterNames,
  powerText,
  readApplied,
  readCandidates,
  readCheckResult,
  readGradientOffer,
  readShapeCycle,
  readShapeResult,
  readShapeStep,
  readSplit,
  readVariables,
  sensitivitySourceText,
  shareText,
  solvesPerCycle,
  splitAdjointNote,
  splitOutlets,
  stepText,
  variableName,
  withCycle,
  type Candidate,
  type CheckResult,
  type GradientOffer,
  type GradientSource,
  type HistoryColumn,
  type Objective,
  type ShapeCycle,
  type ShapeResult,
  type ShapeStep,
  type VariableRow,
  sciText,
} from "./optimise";
import "./optimise.css";

const CHART = { w: 296, h: 132 };
const GRADIENT_CHART = { w: 296, h: 92 };

/** A row of the variables list; `custom` when the user types a reference rather than choosing one. */
type Row = VariableRow & { custom?: boolean };

/** The candidates the operation's kind reads (a sphere offers its radius, not a box's size). */
function relevant(candidates: Candidate[], tree: NodeSummary | null): Candidate[] {
  return candidates.filter((c) => {
    const node = findNode(tree, c.node);
    if (!node || !isImplicitOperation(node.type)) return true;
    return implicitShows(node.type, node.params as Record<string, unknown>, c.name.split(".")[0] ?? c.name);
  });
}

/** One series against the cycle number, with numbered axes; the best cycle's point is ringed. */
function Chart({ values, size, className, title, unit, best, ws, reference, referenceLabel }: { values: readonly number[]; size: { w: number; h: number }; className: string; title: string; unit: string; best?: number; ws: string; reference?: number; referenceLabel?: string }) {
  const chart = convergenceChart(values, size.w, size.h, undefined, reference);
  const scale = chart.log ? "log scale" : powerText(chart.exponent);
  const heading = [title, unit !== "" ? unit : null, scale !== "" ? scale : null].filter((p) => p !== null).join(" · ");
  return (
    <figure className="optimise-figure" data-ws={ws}>
      <figcaption className="optimise-caption muted">{heading}</figcaption>
      <svg className={`optimise-chart ${className}`} viewBox={`0 0 ${size.w} ${size.h}`} role="img" aria-label={`${title} against the cycle`}>
        {chart.yTicks.map((t, i) => (
          <g key={`y${i}`}>
            <line className="optimise-grid" x1={chart.x0} x2={chart.x1} y1={t.at} y2={t.at} />
            <text className="optimise-tick" x={chart.x0 - 4} y={t.at + 3.5} textAnchor="end">
              {t.label}
            </text>
          </g>
        ))}
        <line className="optimise-axis" x1={chart.x0} x2={chart.x1} y1={chart.y1} y2={chart.y1} />
        {chart.xTicks.map((t, i) => (
          <text key={`x${i}`} className="optimise-tick" x={t.at} y={chart.y1 + 12} textAnchor="middle">
            {t.label}
          </text>
        ))}
        {chart.reference !== null && (
          <g data-ws={`${ws}-target`}>
            <line className="optimise-target-line" x1={chart.x0} x2={chart.x1} y1={chart.reference.at} y2={chart.reference.at} />
            <text className="optimise-tick" x={chart.x1 - 2} y={chart.reference.at - 3} textAnchor="end">
              {referenceLabel ?? ""}
            </text>
          </g>
        )}
        {chart.points.length > 1 && <polyline points={chart.points.map((p) => `${p.x},${p.y}`).join(" ")} />}
        {chart.points.map((p) => (
          <circle key={p.cycle} cx={p.x} cy={p.y} r={p.cycle === best ? 4 : 2.5} className={p.cycle === best ? "optimise-best-dot" : undefined}>
            <title>{`cycle ${p.cycle}: ${p.value.toPrecision(4)}${unit !== "" ? ` ${unit}` : ""}`}</title>
          </circle>
        ))}
      </svg>
    </figure>
  );
}

export default function OptimisePanel() {
  const tree = useStore((s) => s.tree);
  const client = useStore((s) => s.client);
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const parts = useMemo(() => implicitParts(tree), [tree]);
  const names = useMemo(() => parameterNames(tree), [tree]);
  const outlets = useMemo(() => outletNames(tree), [tree]);
  const [part, setPart] = useState("");
  // A project with a flow set up optimises its pressure loss; one without can only size a part.
  const [objective, setObjectiveState] = useState<Objective>(() => (hasFlowSetup(useStore.getState().tree) ? "pressure_loss" : "volume"));
  const [gradient, setGradient] = useState<GradientSource>("adjoint");
  const [target, setTarget] = useState("");
  /** The flow split's target, in percentage points of the equal share. */
  const [splitTarget, setSplitTarget] = useState("1");
  /** The finite-difference step, in percent of each variable's span. */
  const [fdPercent, setFdPercent] = useState("10");
  /** The outlets the user left out of the flow split (all are in by default, and a new outlet joins). */
  const [leftOut, setLeftOut] = useState<string[]>([]);
  const [method, setMethod] = useState("SLSQP");
  const [maxCycles, setMaxCycles] = useState(20);
  const [rows, setRows] = useState<Row[]>([{ reference: "", lower: "", upper: "" }]);
  const [cycles, setCycles] = useState<ShapeCycle[]>([]);
  /** The variables, objective and gradient the cycles on screen were run with (the table's columns, the chart's unit). */
  const [ran, setRan] = useState<{ objective: string; gradient: string; target: number; columns: HistoryColumn[] }>({ objective: "", gradient: "", target: 0, columns: [] });
  const [result, setResult] = useState<ShapeResult | null>(null);
  const [running, setRunning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [stopping, setStopping] = useState<"" | "after" | "now">("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [step, setStep] = useState<{ step: ShapeStep; at: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkRuns, setCheckRuns] = useState(3);
  const [showTable, setShowTable] = useState(false);
  /** Whether the compute host can run the flow split's adjoint (`shape.gradients`); off until it says so. */
  const [offer, setOffer] = useState<GradientOffer>({ splitAdjoint: false, why: "" });
  useEffect(() => {
    let live = true;
    execute("shape.gradients", {}, { silent: true })
      .then((r) => live && setOffer(readGradientOffer(r)))
      .catch(() => live && setOffer({ splitAdjoint: false, why: "" }));
    return () => {
      live = false;
    };
  }, [execute, client]);

  const setObjective = (next: Objective) => {
    setObjectiveState(next);
    // Each objective starts on its own default: finite differences for the flow split, even where its adjoint can run.
    if (next !== "volume" && next !== objective) setGradient(defaultGradient(next));
  };
  const chosenOutlets = outlets.filter((o) => !leftOut.includes(o));

  const chosen = parts.find((p) => p.id === part) ?? parts[0];
  // What shapes the chosen part, with suggested bounds (`shape.variables`).
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  useEffect(() => {
    if (!chosen) return setCandidates([]);
    let live = true;
    execute("shape.variables", { part: chosen.id }, { silent: true })
      .then((r) => live && setCandidates(relevant(readCandidates(r), useStore.getState().tree)))
      .catch(() => live && setCandidates([]));
    return () => {
      live = false;
    };
  }, [chosen?.id, execute]);

  const partId = chosen?.id ?? "";
  useEffect(() => {
    if (!client) return;
    return client.onEvent((event, data) => {
      if (!event.startsWith("shape.")) return;
      // Another part's run is not this panel's news.
      if (typeof data.part === "string" && partId !== "" && data.part !== partId) return;
      if (event === "shape.step") {
        const read = readShapeStep(data);
        if (read !== null) setStep({ step: read, at: Date.now() });
        return;
      }
      if (event === "shape.cycle") {
        const row = readShapeCycle(data);
        if (row !== null) setCycles((c) => withCycle(c, row));
        return;
      }
      // The long work runs in the background (`wait` false): its end arrives as an event.
      if (event === "shape.finished") {
        const done = readShapeResult(data.result);
        const failed = typeof data.error === "string" && data.error !== "" ? data.error : "";
        setRunning(false);
        setStopping("");
        if (done.cycles.length > 0) setCycles(done.cycles);
        setResult(done);
        setStatus(failed !== "" ? "" : finishedText(done.status || String(data.status ?? ""), done.message, done.cycles.length));
        if (failed !== "") setError(failed);
        setStep(null);
        return;
      }
      if (event === "shape.checked") {
        setChecking(false);
        setStopping("");
        setCheck(readCheckResult(data));
        if (typeof data.error === "string" && data.error !== "") setError(data.error);
        setStep(null);
        return;
      }
      if (event === "shape.applied") {
        const done = readApplied(data);
        setApplying(false);
        if (done.ok) setStatus(done.tags.length > 0 ? `Best point applied: the part was rebuilt and its boundaries (${done.tags.join(", ")}) found again` : "Best point applied: the part was rebuilt");
        else setError(done.error || "Applying the best point failed");
      }
    });
  }, [client, partId]);

  const busy = running || checking || applying;
  // The progress line's clock: once a second while a cycle runs.
  useEffect(() => {
    if (!running && !checking) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running, checking]);

  const candidateOf = (reference: string) => candidates.find((c) => c.reference === reference.trim());
  const unitOf = (reference: string) => candidateOf(reference)?.unit ?? "";
  const nameOf = (reference: string) => variableName(candidateOf(reference), reference);
  const cfd = objective !== "volume";
  const sources = gradientSources(objective, offer.splitAdjoint);
  const source: GradientSource = sources.includes(gradient) ? gradient : (sources[0] ?? "adjoint");
  /** The flow split's adjoint takes every outlet: with one left out, the check and the run fall back to finite differences. */
  const allOutlets = chosenOutlets.length === outlets.length;
  const checksAdjoint = objective === "pressure_loss" || (objective === "flow_split" && offer.splitAdjoint && allOutlets);
  const variableCount = Math.max(1, rows.filter((r) => r.reference.trim() !== "").length);

  /** The CFD settings of a run, or why they cannot be. */
  const cfdOptions = (variables: ReadonlyArray<{ lower: number; upper: number }>): { ok: true; value: Record<string, unknown>; target: number } | { ok: false; error: string } => {
    const value: Record<string, unknown> = { gradient: source };
    let deviation = 0;
    if (source === "finite_difference") {
      const steps = fdSteps(variables, Number(fdPercent));
      if (steps === null) return { ok: false, error: "The finite-difference step is a percentage of each variable's span, between 0 and 100" };
      value.fd_step = steps;
    }
    if (objective === "flow_split") {
      if (chosenOutlets.length < 2) return { ok: false, error: outlets.length < 2 ? "The flow split needs at least two pressure outlets: set them up under Boundaries" : "Choose at least two outlets to share the flow" };
      const points = Number(splitTarget);
      if (!(points > 0 && points < 100)) return { ok: false, error: "The target is in percentage points of the equal share, above 0" };
      deviation = points / 100;
      value.target = deviation;
      if (source === "adjoint" && !allOutlets) return { ok: false, error: "The adjoint flow split shares the flow among every outlet: include them all, or use finite differences" };
      // Every outlet is the default; name them only when some are left out.
      if (!allOutlets) value.outlets = chosenOutlets;
    }
    return { ok: true, value, target: deviation };
  };

  const start = async () => {
    setError("");
    const read = readVariables(rows, unitOf);
    if (!read.ok) return setError(read.error);
    if (!chosen) return setError("Create a part first: the optimiser rebuilds parts made with Create");
    const volume = Number(target);
    if (objective === "volume" && !(volume > 0)) return setError("Give the target volume in m³");
    const options = cfd ? cfdOptions(read.value) : null;
    if (options !== null && !options.ok) return setError(options.error);
    setCycles([]);
    setResult(null);
    setCheck(null);
    setStep(null);
    setRan({ objective, gradient: cfd ? source : "", target: options?.ok ? options.target : 0, columns: read.value.map((v) => ({ reference: v.reference, name: nameOf(v.reference), unit: unitOf(v.reference) })) });
    setRunning(true);
    setStatus("");
    try {
      await execute("shape.optimise", {
        part: chosen.id,
        variables: read.value,
        objective,
        ...(objective === "volume" ? { target_volume: volume } : {}),
        ...(options?.ok ? options.value : {}),
        method,
        max_cycles: maxCycles,
      });
    } catch (err) {
      // Refused before it started (no tags, a bad variable, a port the adjoint cannot see): nothing runs.
      setError((err as Error).message);
      setRunning(false);
    }
  };

  const cancel = (now: boolean) => {
    if (!chosen) return;
    setStopping(now ? "now" : "after");
    void execute("shape.cancel", { part: chosen.id, now }).catch((err: unknown) => {
      setStopping("");
      setError((err as Error).message);
    });
  };

  // The gradient against central finite differences over every variable (2N + 1 runs). The adjoint is compared with it
  // when there is one here; otherwise the check says whether the differences are sound.
  const checkGradient = async () => {
    const read = readVariables(rows, unitOf);
    if (!read.ok || !chosen) return setError(read.ok ? "Create a part first" : read.error);
    setError("");
    setCheck(null);
    setStep(null);
    // The check is its own run: the optimisation's last status ("stopped after 20 cycles") is not its news.
    setStatus("");
    setChecking(true);
    setCheckRuns(2 * read.value.length + 1);
    try {
      await execute("shape.check", {
        part: chosen.id,
        parameters: read.value.map((v) => v.reference),
        objective,
        ...(objective === "flow_split" && !allOutlets && chosenOutlets.length >= 2 ? { outlets: chosenOutlets } : {}),
      });
    } catch (err) {
      setError((err as Error).message);
      setChecking(false);
    }
  };

  const best = bestCycle(cycles);
  const apply = async () => {
    if (!best || ran.columns.length === 0 || !chosen) return;
    const values: Record<string, number> = {};
    ran.columns.forEach((v, i) => {
      const x = best.x[i];
      if (x !== undefined && Number.isFinite(x)) values[v.reference] = x;
    });
    setError("");
    setStatus("Applying the best point: rebuilding the part…");
    setApplying(true);
    try {
      // The part is rebuilt and its boundaries found again in the background; `shape.applied` says when.
      await execute("shape.apply", { part: chosen.id, values });
    } catch (err) {
      setApplying(false);
      setStatus("");
      setError((err as Error).message);
    }
  };

  const exportHistory = async () => {
    const name = await saveTextFile(`${chosen?.name ?? "part"}-optimisation.csv`, historyCsv(cycles, ran.columns, ran.objective), "Export the optimisation history");
    if (name !== null) pushToast("ok", `Exported ${name}`);
  };

  const setRow = (i: number, patch: Partial<Row>) => setRows((r) => r.map((row, j) => (j === i ? { ...row, ...patch } : row)));
  const choose = (i: number, value: string) => {
    if (value === OTHER) return setRow(i, { custom: true, reference: "", lower: "", upper: "" });
    const known = candidates.find((c) => c.reference === value);
    // A listed quantity brings its suggested bounds, in its display unit.
    setRow(i, known ? { custom: false, reference: value, lower: boundText(known.lower, known.unit), upper: boundText(known.upper, known.unit) } : { custom: false, reference: value });
  };
  const unverified = cycles.some((c) => c.detail?.unverified === true);
  const wallSource = sensitivitySourceText(cycleSensitivitySource(cycles));
  const jUnit = objectiveUnit(ran.objective || objective);
  const since = step === null ? 0 : Math.max(0, (now - step.at) / 1000);
  const progress = step !== null && step.step.steps > 0 ? Math.min(1, (step.step.stepNumber - 0.5) / step.step.steps) : null;
  const split = ran.objective === "flow_split";
  const shareColumns = split ? splitOutlets(cycles) : [];
  const deviations = split ? cycles.map((c) => (readSplit(c)?.deviation ?? Number.NaN) * 100) : [];
  const verdict = check !== null ? checkVerdict(check, unitOf) : null;
  const runAdjoint = (ran.gradient || source) === "adjoint" && (ran.objective || objective) !== "volume";

  return (
    <section className="optimise-panel glass" aria-label="Optimise" data-ws="optimise-panel">
      <header className="optimise-title">
        <span className="section-title">Optimise</span>
        <span className="muted">shape, with gradients</span>
      </header>
      <div className="optimise-body">
        <label className="optimise-field">
          <span>Part</span>
          <span className="select-wrap">
            <select className="field" value={chosen?.id ?? ""} disabled={busy} onChange={(e) => setPart(e.target.value)} aria-label="Part to optimise">
              {parts.length === 0 && <option value="">No part made with Create</option>}
              {parts.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <ChevronsUpDown size={12} className="select-chevron" aria-hidden="true" />
          </span>
        </label>
        <div className="optimise-field">
          <span>Objective</span>
          <div className="optimise-choice" role="radiogroup" aria-label="Objective">
            <label title="J is the total-pressure power through the inlets and outlets per unit density (m⁵/s³); each cycle meshes and solves the flow on FoamWin (laminar internal flow). The inlet, outlets and walls need to be set up first">
              <input type="radio" disabled={busy} checked={objective === "pressure_loss"} onChange={() => setObjective("pressure_loss")} data-ws="optimise-objective-loss" /> Pressure loss
            </label>
            <label title="J = Σ (Qᵢ/Q − 1/N)² over the chosen outlets: zero when every outlet takes the same flow. Each cycle meshes and solves the flow on FoamWin">
              <input type="radio" disabled={busy} checked={objective === "flow_split"} onChange={() => setObjective("flow_split")} data-ws="optimise-objective-split" /> Equal flow split
            </label>
            <label title="Size the part to a volume, with exact gradients and no flow solve">
              <input type="radio" disabled={busy} checked={objective === "volume"} onChange={() => setObjective("volume")} data-ws="optimise-objective-volume" /> Reach a volume
            </label>
          </div>
        </div>
        {objective === "volume" && (
          <label className="optimise-field">
            <span>Target volume (m³)</span>
            <input className="field mono" disabled={busy} value={target} placeholder="4.2e-6" onChange={(e) => setTarget(e.target.value)} aria-label="Target volume" />
          </label>
        )}
        {objective === "flow_split" && (
          <div className="optimise-field" data-ws="optimise-split-settings">
            <span>Outlets sharing the flow</span>
            {outlets.length === 0 ? (
              <p className="muted optimise-note" title="The flow split shares the flow among the pressure outlets: set them up under Boundaries first">
                No pressure outlet yet
              </p>
            ) : (
              <div className="optimise-outlets" role="group" aria-label="Outlets">
                {outlets.map((o) => (
                  <label key={o} data-ws="optimise-outlet">
                    <input type="checkbox" disabled={busy} checked={!leftOut.includes(o)} onChange={(e) => setLeftOut((l) => (e.target.checked ? l.filter((x) => x !== o) : [...l, o]))} aria-label={`Outlet ${o}`} />
                    {o}
                  </label>
                ))}
              </div>
            )}
            <label className="optimise-inline" title="The run stops at the first cycle where every outlet's share is within this of the equal share 1/N">
              <span>Stop within</span>
              <input className="field mono" inputMode="decimal" disabled={busy} value={splitTarget} onChange={(e) => setSplitTarget(e.target.value)} aria-label="Flow split target (percentage points)" data-ws="optimise-target" />
              <span className="muted">
                points of {chosenOutlets.length > 0 ? shareText(1 / chosenOutlets.length) : "1/N"}
              </span>
            </label>
          </div>
        )}
        {cfd && (
          <div className="optimise-field">
            <span>Gradient</span>
            <div className="optimise-choice" role="radiogroup" aria-label="Gradient source">
              <label
                title={
                  objective === "flow_split"
                    ? offer.splitAdjoint
                      ? "One flow solve and one adjoint solve per cycle with the open-source adjoint solver; experimental. The wall sensitivity cannot see a moving inlet or outlet. Run Check gradient to verify it on this case"
                      : offer.why || "The flow split's adjoint needs the open-source adjoint solver (adjointFoam) on the compute host"
                    : "One flow solve and one adjoint solve per cycle; the wall sensitivity cannot see a moving inlet or outlet. Run Check gradient to verify it on this case"
                }
              >
                <input type="radio" disabled={busy || !sources.includes("adjoint")} checked={source === "adjoint"} onChange={() => { setGradient("adjoint"); setError(""); }} data-ws="optimise-gradient-adjoint" /> Adjoint
              </label>
              <small data-ws="optimise-gradient-adjoint-note">{objective === "flow_split" ? splitAdjointNote(offer) : "1 solve per cycle, laminar only; unverified until a check passes"}</small>
              <label title="Forward differences through the whole loop: one solve at the point and one per variable. Slow, but right up to the mesh noise">
                <input type="radio" disabled={busy} checked={source === "finite_difference"} onChange={() => { setGradient("finite_difference"); setError(""); }} data-ws="optimise-gradient-fd" /> Finite differences
              </label>
              <small>
                {solvesPerCycle("finite_difference", variableCount)} flow solves per cycle ({variableCount} variable{variableCount === 1 ? "" : "s"} + 1)
              </small>
            </div>
            {source === "finite_difference" && (
              <label className="optimise-inline" title="The step each variable moves by for its difference, as a percentage of its bounds' span: well above the mesh noise, small against the span">
                <span>Step</span>
                <input className="field mono" inputMode="decimal" disabled={busy} value={fdPercent} onChange={(e) => setFdPercent(e.target.value)} aria-label="Finite-difference step (percent of span)" data-ws="optimise-fd-step" />
                <span className="muted">% of span</span>
              </label>
            )}
          </div>
        )}

        <div className="optimise-head">Variables</div>
        <datalist id="optimise-names">
          {names.map((n) => (
            <option key={n} value={n} />
          ))}
        </datalist>
        {rows.map((row, i) => {
          const known = candidateOf(row.reference);
          const custom = row.custom === true || (row.reference !== "" && known === undefined);
          const shownUnit = displayUnit(known?.unit ?? "").unit;
          return (
            <div key={i} className="optimise-variable" data-ws="optimise-variable">
              <div className="optimise-var-top">
                <span className="select-wrap" title={row.reference !== "" ? row.reference : undefined}>
                  <select className="field" value={custom ? OTHER : row.reference} disabled={busy} onChange={(e) => choose(i, e.target.value)} aria-label={`Variable ${i + 1}`} data-ws="optimise-variable-choice">
                    <option value="" disabled>
                      {candidates.length > 0 ? "Choose a quantity…" : "Type a quantity below"}
                    </option>
                    {candidates.map((c) => (
                      <option key={c.reference} value={c.reference} title={c.reference}>
                        {variableName(c, c.reference)}
                      </option>
                    ))}
                    <option value={OTHER}>Other (an expression name)…</option>
                  </select>
                  <ChevronsUpDown size={12} className="select-chevron" aria-hidden="true" />
                </span>
                <button type="button" className="optimise-icon" disabled={busy} aria-label={`Remove variable ${i + 1}`} title="Remove this variable" onClick={() => setRows((r) => (r.length > 1 ? r.filter((_, j) => j !== i) : [{ reference: "", lower: "", upper: "" }]))}>
                  <Trash2 size={13} />
                </button>
              </div>
              {custom && (
                <input
                  className="field mono"
                  list="optimise-names"
                  placeholder="R, or geometry.….depth"
                  value={row.reference}
                  disabled={busy}
                  title={row.reference}
                  onChange={(e) => setRow(i, { reference: e.target.value, custom: true })}
                  aria-label={`Variable ${i + 1} reference`}
                />
              )}
              <div className="optimise-bounds">
                <input className="field mono" inputMode="decimal" placeholder="min" value={row.lower} disabled={busy} onChange={(e) => setRow(i, { lower: e.target.value })} aria-label={`Variable ${i + 1} lower bound${shownUnit ? ` (${shownUnit})` : ""}`} />
                <span className="muted">to</span>
                <input className="field mono" inputMode="decimal" placeholder="max" value={row.upper} disabled={busy} onChange={(e) => setRow(i, { upper: e.target.value })} aria-label={`Variable ${i + 1} upper bound${shownUnit ? ` (${shownUnit})` : ""}`} />
                <span className="muted optimise-unit" title={known ? `Typed in ${shownUnit || "the quantity's unit"}; sent in SI (${known.unit || "1"})` : "A quantity the part does not list: its bounds are typed in SI"}>
                  {/* Before a quantity is chosen there is no unit to name: "SI" beside empty bounds read as a unit. */}
                  {known ? shownUnit : row.reference.trim() === "" ? "" : "SI"}
                </span>
              </div>
              {row.reference.trim() !== "" && (
                <div className="muted optimise-var-label" data-ws="optimise-variable-label" title={row.reference}>
                  {/* The model's reference is for the tooltip: under a quantity the part lists, the line
                      says its current value in words, not an internal path. */}
                  {known ? `now ${formatValue(known.value, known.unit)}` : <span className="mono">{row.reference}</span>}
                </div>
              )}
            </div>
          );
        })}
        <button type="button" className="optimise-add" disabled={busy} onClick={() => setRows((r) => [...r, { reference: "", lower: "", upper: "" }])}>
          <Plus size={13} /> Add variable
        </button>

        <div className="optimise-run">
          <span className="select-wrap">
            <select className="field" value={method} disabled={busy} onChange={(e) => setMethod(e.target.value)} aria-label="Method">
              <option value="SLSQP">SLSQP</option>
              <option value="L-BFGS-B">L-BFGS-B</option>
            </select>
            <ChevronsUpDown size={12} className="select-chevron" aria-hidden="true" />
          </span>
          <label className="optimise-cycles" title="The most cycles the optimiser runs">
            <input className="field mono" type="number" min={1} max={200} value={maxCycles} disabled={busy} onChange={(e) => setMaxCycles(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} onFocus={(e) => e.currentTarget.select()} aria-label="Maximum cycles" />
            <span className="muted">cycles</span>
          </label>
          <Button variant="primary" disabled={busy} onClick={() => void start()} data-ws="optimise-start">
            <Play size={13} /> Start
          </Button>
          {/* Beside Start, in the row that stays in view: under it the button was below the fold of a
              short window, and a check nobody can see is a check nobody runs. */}
          {cfd && (
            <Button
              disabled={busy}
              onClick={() => void checkGradient()}
              data-ws="optimise-check"
              title={
                checksAdjoint
                  ? `Compare the adjoint gradient with central finite differences over every variable: ${2 * variableCount + 1} CFD runs`
                  : `Check that central finite differences are sound here (above the mesh noise, little curvature): ${2 * variableCount + 1} CFD runs`
              }
            >
              <Gauge size={13} /> Check gradient
            </Button>
          )}
        </div>
        {cfd && check !== null && !checking && (
          <div className="optimise-check">
            <CheckLine check={check} verdict={verdict} />
          </div>
        )}

        {error && (
          <div role="alert" className="optimise-error">
            {error}
          </div>
        )}
        {(running || checking) && (
          <div className="optimise-progress" role="status" data-ws={checking ? "optimise-check-progress" : "optimise-step"}>
            <div className="optimise-progress-text">
              {step !== null
                ? stepText(step.step, { since, maxCycles: running ? maxCycles : undefined, cyclesDone: cycles.length, adjoint: step.step.run === "check" ? checksAdjoint : runAdjoint })
                : checking
                  ? `Check 1/${checkRuns} · starting…`
                  : objective === "volume"
                    ? `Running · ${cycles.length} cycle${cycles.length === 1 ? "" : "s"} so far`
                    : `Cycle ${cycles.length + 1} of at most ${maxCycles} · starting…`}
            </div>
            {progress !== null && (
              <div className="optimise-bar" aria-hidden="true">
                <i style={{ width: `${Math.round(progress * 100)}%` }} />
              </div>
            )}
            {running && ran.gradient === "finite_difference" && <div className="muted">Each cycle is {solvesPerCycle("finite_difference", ran.columns.length)} flow solves: the point, then each variable moved by its step</div>}
            {stopping !== "" && <div className="muted">{stopping === "now" ? "Cancelling the cycle under way…" : "Stopping when this cycle ends…"}</div>}
            <div className="optimise-progress-actions">
              {running ? (
                <>
                  <Button onClick={() => cancel(false)} disabled={stopping !== ""} data-ws="optimise-stop" title="Let the cycle under way finish and keep it, then stop">
                    <Square size={13} /> Stop after this cycle
                  </Button>
                  <Button onClick={() => cancel(true)} disabled={stopping === "now"} data-ws="optimise-cancel-now" title="Cancel the mesh or run of the cycle under way now; it is not recorded">
                    <X size={13} /> Cancel this cycle
                  </Button>
                </>
              ) : (
                <Button onClick={() => cancel(true)} disabled={stopping === "now"} data-ws="optimise-check-cancel" title="Cancel the gradient check: the evaluation under way stops now">
                  <X size={13} /> Cancel check
                </Button>
              )}
            </div>
          </div>
        )}
        {status && (
          <div className="muted optimise-status" data-ws="optimise-status">
            {status}
          </div>
        )}
        {cycles.length > 0 && (
          <>
            <Chart values={cycles.map((c) => c.objective)} size={CHART} className="optimise-j" title={`J (${objectiveWords(ran.objective)})`} unit={jUnit} best={best ? best.index + 1 : undefined} ws="optimise-chart" />
            {split && <Chart values={deviations} size={GRADIENT_CHART} className="optimise-dev" title="Largest deviation from the equal share" unit="points" ws="optimise-deviation-chart" reference={ran.target * 100} referenceLabel="target" />}
            <Chart values={cycles.map((c) => c.gradient_norm)} size={GRADIENT_CHART} className="optimise-g" title="|∇J|" unit="" ws="optimise-gradient-chart" />
            {split && shareColumns.length > 0 && (
              <div className="optimise-table-wrap" data-ws="optimise-split-table">
                <table className="optimise-table">
                  <caption>
                    Share of the flow per outlet; equal is {shareText(1 / shareColumns.length)}
                  </caption>
                  <thead>
                    <tr>
                      <th>#</th>
                      {shareColumns.map((o) => (
                        <th key={o}>{o}</th>
                      ))}
                      <th title="The largest |share − 1/N|, in percentage points">max dev.</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cycles.map((c) => {
                      const s = readSplit(c);
                      return (
                        <tr key={c.index} className={best?.index === c.index ? "best" : undefined}>
                          <td>{c.index + 1}</td>
                          {shareColumns.map((o) => {
                            const share = s?.outlets.find((x) => x.outlet === o);
                            const off = share !== undefined && s !== null && Math.abs(share.share - s.equal) > ran.target;
                            return (
                              <td key={o} className={share === undefined ? undefined : off ? "off" : "on"} title={share?.flow != null ? `${share.flow.toPrecision(4)} m³/s` : undefined}>
                                {share === undefined ? "n/a" : shareText(share.share)}
                              </td>
                            );
                          })}
                          <td>{s === null ? "n/a" : (s.deviation * 100).toFixed(2)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div className="optimise-history-actions">
              <Button onClick={() => setShowTable(!showTable)} aria-expanded={showTable} data-ws="optimise-table-toggle">
                {showTable ? "Hide cycles" : `Cycles (${cycles.length})`}
              </Button>
              <Button onClick={() => void exportHistory()} data-ws="optimise-csv" title="Save every cycle's J, gradient, variables, outlet shares and time as a CSV file">
                <Download size={13} /> CSV
              </Button>
            </div>
            {showTable && (
              <div className="optimise-table-wrap" data-ws="optimise-table">
                <table className="optimise-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th title={jUnit ? `J (${jUnit})` : "J"}>J</th>
                      <th>|∇J|</th>
                      {ran.columns.map((c) => (
                        <th key={c.reference} title={c.reference}>
                          {c.name}
                          {displayUnit(c.unit).unit ? ` (${displayUnit(c.unit).unit})` : ""}
                        </th>
                      ))}
                      <th>time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cycles.map((c) => (
                      <tr key={c.index} className={best?.index === c.index ? "best" : undefined}>
                        <td>{c.index + 1}</td>
                        <td>{c.objective.toPrecision(4)}</td>
                        <td>{Number.isFinite(c.gradient_norm) ? c.gradient_norm.toPrecision(3) : "n/a"}</td>
                        {ran.columns.map((col, i) => (
                          <td key={col.reference}>{c.x[i] === undefined ? "n/a" : String(Number((c.x[i]! * displayUnit(col.unit).scale).toPrecision(5)))}</td>
                        ))}
                        <td>{c.seconds > 0 ? durationText(c.seconds) : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {best && (
              <div className="optimise-best" data-ws="optimise-best">
                <div>
                  Best: J = {sciText(best.objective)}
                  {jUnit ? ` ${jUnit}` : ""} at cycle {best.index + 1}
                  {cycles[0] && cycles[0].objective !== 0 && best.index !== cycles[0].index && <span className="muted"> ({(((best.objective - cycles[0].objective) / Math.abs(cycles[0].objective)) * 100).toFixed(1)} % from the start)</span>}
                  {unverified && <span className="optimise-warn"> · adjoint gradient not yet verified</span>}
                  {wallSource && <span className="muted" data-ws="optimise-sensitivity-source"> · {wallSource}</span>}
                </div>
                {split && readSplit(best) !== null && (
                  <div className="muted" data-ws="optimise-best-split">
                    largest deviation {(readSplit(best)!.deviation * 100).toFixed(2)} points (target {(ran.target * 100).toFixed(2)})
                  </div>
                )}
                {result !== null && result.improvement !== null && ran.objective !== "volume" && (
                  <div className={result.significant ? "muted" : "optimise-warn"} data-ws="optimise-significance">
                    {result.significant
                      ? `J fell ${(result.improvement * 100).toFixed(1)} %, above the mesh noise floor (${((result.noiseFloor ?? 0) * 100).toFixed(1)} %)`
                      : `J changed ${(result.improvement * 100).toFixed(1)} %, within the mesh noise floor (${((result.noiseFloor ?? 0) * 100).toFixed(1)} %): not an improvement`}
                  </div>
                )}
                {ran.columns.map((v, i) => (
                  <div key={v.reference} className="optimise-best-row" title={v.reference}>
                    <span className="muted">{v.name}</span>
                    <span className="mono">{formatValue(best.x[i] ?? Number.NaN, v.unit)}</span>
                  </div>
                ))}
                <Button onClick={() => void apply()} disabled={busy} data-ws="optimise-apply" title="Set these values, rebuild the part and find its inlet, outlets and walls again on the new surface (one undo step for the values)">
                  <Wand2 size={13} /> {applying ? "Applying…" : "Apply best point"}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/** The select's value for "type a reference". */
const OTHER = "\u0000other";

/** A finished optimisation in words. */
function finishedText(status: string, message: string, count: number): string {
  const n = `${count} cycle${count === 1 ? "" : "s"}`;
  switch (status) {
    case "converged":
      return `Converged after ${n}`;
    case "target_reached":
      return `Target reached after ${n}: every outlet is within the target of the equal share`;
    case "gradient_disagrees":
      return `Stopped after ${n}: J moved against the adjoint gradient's prediction, so the gradient is not trusted here; try finite differences`;
    case "max_cycles":
      return `Stopped at the cycle limit (${n}), not converged`;
    case "cancelled":
      return `Stopped by you after ${n}`;
    case "failed":
      return `The optimiser stopped after ${n}: ${message || "no reason given"}`;
    default:
      return message !== "" ? `${status}: ${message}` : status;
  }
}

/** The gradient check's own result: its verdict and, per variable, the finite difference beside the adjoint. */
function CheckLine({ check, verdict }: { check: CheckResult; verdict: ReturnType<typeof checkVerdict> }): ReactNode {
  if (check.status === "cancelled") {
    return (
      <span className="muted" data-ws="optimise-check-result">
        Check cancelled after {check.completed} of {check.checks}
      </span>
    );
  }
  if (check.status === "failed" || verdict === null) {
    return (
      <span className="optimise-error" data-ws="optimise-check-result">
        Check failed
      </span>
    );
  }
  const ratio = check.ratio !== null ? ` · ratio ${check.ratio.toFixed(2)}` : "";
  return (
    <div className="optimise-verdict" data-ws="optimise-check-result">
      <div>
        <span className={`optimise-verdict-word ${verdict.tone}`} data-ws="optimise-check-verdict">
          {verdict.word}
        </span>
        <span className="mono">{ratio}</span>
        <span className="muted"> · {verdict.detail}</span>
        {sensitivitySourceText(check.sensitivitySource) && (
          <span className="muted" data-ws="optimise-check-sensitivity-source"> · {sensitivitySourceText(check.sensitivitySource)}</span>
        )}
      </div>
      {check.rows.length > 0 && (
        <table className="optimise-table">
          <thead>
            <tr>
              <th>quantity</th>
              <th title="Central finite difference, dJ per SI unit">finite diff.</th>
              {check.rows.some((r) => r.adjoint !== null) && <th>adjoint</th>}
              {check.rows.some((r) => r.ratio !== null) && <th>ratio</th>}
            </tr>
          </thead>
          <tbody>
            {check.rows.map((r) => (
              <tr key={r.parameter}>
                <td title={r.parameter}>{r.parameter}</td>
                <td className={r.consistent ? undefined : "off"}>{r.finiteDifference === null ? "n/a" : r.finiteDifference.toPrecision(3)}</td>
                {check.rows.some((x) => x.adjoint !== null) && <td>{r.adjoint === null ? "n/a" : r.adjoint.toPrecision(3)}</td>}
                {check.rows.some((x) => x.ratio !== null) && <td>{r.ratio === null ? "n/a" : r.ratio.toFixed(2)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
