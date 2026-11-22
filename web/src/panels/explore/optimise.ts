/**
 * The Optimise panel's pure helpers (ADR-0038 decision 6): which parts and names it offers, how a
 * variable row is checked, the readers of the `shape.*` replies and events, the convergence chart's
 * geometry, the per-cycle table and its CSV. No React, no store: the tests run in Node.
 *
 * **Everything from the server is data**: a reply or an event that lacks an array or a number gives a
 * neutral value here, never an exception in a listener (an event listener that threw was the
 * "Cannot read properties of undefined (reading 'length')" of the Windows walkthrough, 2026-11-13).
 *
 * Values travel in SI; the panel shows them in the modeller's units (lengths in millimetres, angles in
 * degrees: `GEOMETRY_UNIT_SYSTEM`), and a variable's bounds are typed in those units.
 */
import type { NodeSummary } from "../../api/types";

/** One cycle of a `shape.optimise` run, as the `shape.cycle` event and the result carry it. */
export interface ShapeCycle {
  index: number;
  x: number[];
  objective: number;
  gradient: number[];
  gradient_norm: number;
  source: string;
  seconds: number;
  detail?: Record<string, unknown>;
}

/** A variable row as the user types it: a reference and its bounds in the reference's display unit, as text. */
export interface VariableRow {
  reference: string;
  lower: string;
  upper: string;
}

/** One quantity `shape.variables` offers: its reference, its words, its value and suggested bounds in SI. */
export interface Candidate {
  reference: string;
  label: string;
  /** What the quantity belongs to ("Sketch 1", "parameter"); "" from an older server. */
  body: string;
  /** The quantity's own name ("Pull distance", "R"); "" from an older server. */
  quantity: string;
  value: number;
  unit: string;
  lower: number;
  upper: number;
  node: string;
  name: string;
}

// ---------------------------------------------------------------------------------------------- reading the wire
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** The finite numbers of an array, as numbers; a missing or mistyped array is empty. A non-number is NaN, so positions keep. */
function numbers(value: unknown): number[] {
  return Array.isArray(value) ? value.map((v) => (typeof v === "number" ? v : Number.NaN)) : [];
}

/** `shape.variables`' reply as candidates; one that is not one is an empty list. */
export function readCandidates(reply: unknown): Candidate[] {
  if (!isRecord(reply) || !Array.isArray(reply.variables)) return [];
  const out: Candidate[] = [];
  for (const v of reply.variables) {
    if (!isRecord(v) || text(v.reference) === "") continue;
    const value = num(v.value);
    const lower = num(v.lower);
    const upper = num(v.upper);
    if (value === null || lower === null || upper === null) continue;
    out.push({
      reference: text(v.reference),
      label: text(v.label),
      body: text(v.body),
      quantity: text(v.quantity),
      value,
      unit: text(v.unit),
      lower,
      upper,
      node: text(v.node),
      name: text(v.name),
    });
  }
  return out;
}

/** A `shape.cycle` event (or a cycle of a result), or null without an index and a finite objective. */
export function readShapeCycle(data: unknown): ShapeCycle | null {
  if (!isRecord(data)) return null;
  const index = num(data.index);
  const objective = num(data.objective);
  if (index === null || objective === null) return null;
  const gradient = numbers(data.gradient);
  const norm = num(data.gradient_norm) ?? Math.sqrt(gradient.reduce((sum, g) => sum + (Number.isFinite(g) ? g * g : 0), 0));
  return {
    index,
    x: numbers(data.x),
    objective,
    gradient,
    gradient_norm: norm,
    source: text(data.source),
    seconds: num(data.seconds) ?? 0,
    ...(isRecord(data.detail) ? { detail: data.detail } : {}),
  };
}

/** A finished run's `result`. */
export interface ShapeResult {
  status: string;
  message: string;
  cycles: ShapeCycle[];
  /** (J0 - J_best) / |J0| of a CFD run, or null. */
  improvement: number | null;
  /** Whether the improvement is above the noise floor (a smaller change is not one). */
  significant: boolean;
  noiseFloor: number | null;
}

/** A finished run's `result`: its status, message and cycles in order (the ones that cannot be read are left out). */
export function readShapeResult(result: unknown): ShapeResult {
  if (!isRecord(result)) return { status: "", message: "", cycles: [], improvement: null, significant: false, noiseFloor: null };
  const cycles = (Array.isArray(result.cycles) ? result.cycles : []).map(readShapeCycle).filter((c): c is ShapeCycle => c !== null);
  return {
    status: text(result.status),
    message: text(result.message),
    cycles: cycles.sort((a, b) => a.index - b.index),
    improvement: num(result.improvement),
    significant: result.significant === true,
    noiseFloor: num(result.noise_floor),
  };
}

/** A cycle that arrived as an event, merged into the list: in order of index, once each. */
export function withCycle(cycles: readonly ShapeCycle[], cycle: ShapeCycle): ShapeCycle[] {
  if (cycles.some((c) => c.index === cycle.index)) return cycles as ShapeCycle[];
  return [...cycles, cycle].sort((a, b) => a.index - b.index);
}

/** One quantity's row of a gradient check: the finite difference, and the adjoint beside it when there is one. */
export interface CheckRow {
  parameter: string;
  step: number | null;
  finiteDifference: number | null;
  adjoint: number | null;
  ratio: number | null;
  forward: number | null;
  backward: number | null;
  /** Forward and backward differences agree and the signal stands above the noise floor. */
  consistent: boolean;
  /** Why it is not consistent: "noise" (a weak signal) or "curvature" (the two differences disagree). */
  cause: string;
  suggestedStep: number | null;
}

/** What `shape.check` answered. `status` is "done", "cancelled" or "failed". */
export interface CheckResult {
  status: string;
  ratio: number | null;
  adjoint: number | null;
  finiteDifference: number | null;
  /** How many of the 2N + 1 evaluations finished (a cancelled check). */
  completed: number;
  checks: number;
  /** The gate's word: "passed", "failed", "inconclusive", "no_adjoint", or "" from an older server. */
  verdict: string;
  /** The objective the check measured ("pressure_loss", "flow_split"). */
  objective: string;
  cosine: number | null;
  wrongSign: boolean;
  rows: CheckRow[];
  /** Where the adjoint's wall sensitivity came from ("solver", "reconstructed"), or "" (ADR-0041). */
  sensitivitySource: string;
}

function readCheckRow(row: unknown): CheckRow | null {
  if (!isRecord(row) || text(row.parameter) === "") return null;
  return {
    parameter: text(row.parameter),
    step: num(row.step),
    finiteDifference: num(row.finite_difference),
    adjoint: num(row.adjoint),
    ratio: num(row.ratio),
    forward: num(row.forward),
    backward: num(row.backward),
    consistent: row.consistent !== false,
    cause: text(row.cause),
    suggestedStep: num(row.suggested_step),
  };
}

export function readCheckResult(event: unknown): CheckResult {
  const data = isRecord(event) ? event : {};
  const result = isRecord(data.result) ? data.result : {};
  const status = text(result.status) || text(data.status) || (text(data.error) !== "" ? "failed" : "done");
  const rows = (Array.isArray(result.gradients) ? result.gradients : []).map(readCheckRow).filter((r): r is CheckRow => r !== null);
  const checks = num(result.checks) ?? (rows.length > 0 ? 2 * rows.length + 1 : 3);
  return {
    status,
    ratio: num(result.ratio),
    adjoint: num(result.adjoint),
    finiteDifference: num(result.finite_difference),
    completed: num(result.completed) ?? (status === "done" ? checks : 0),
    checks,
    verdict: text(result.verdict),
    objective: text(result.objective_name),
    cosine: num(result.cosine),
    wrongSign: result.wrong_sign === true,
    rows,
    sensitivitySource: text(result.sensitivity_source),
  };
}

/** The words for where an adjoint's wall sensitivity came from (ADR-0041 decision 8): the solver's
 *  own `shapeSensitivity`, or Stokes' reconstruction from U and Ua; "" when unknown. */
export function sensitivitySourceText(source: string): string {
  if (source === "solver") return "wall sensitivity from the solver";
  if (source === "reconstructed") return "wall sensitivity reconstructed from U and Ua";
  return "";
}

/** The sensitivity source of a run's latest adjoint cycle, or "". */
export function cycleSensitivitySource(cycles: readonly ShapeCycle[]): string {
  for (let i = cycles.length - 1; i >= 0; i--) {
    const found = cycles[i]?.detail?.sensitivity_source;
    if (typeof found === "string" && found !== "") return found;
  }
  return "";
}

/** The gradient check's verdict in the panel's three words, and the sentence behind it. */
export interface CheckVerdict {
  word: "verified" | "inconclusive" | "disagrees" | "consistent";
  tone: "ok" | "warn" | "bad";
  detail: string;
}

/**
 * What a finished check means for the user. **The words are honest about what was compared**: "verified" only when an
 * adjoint gradient agreed with the finite difference within the gate's band; "disagrees" when it did not (or had the
 * wrong sign); "inconclusive" when the finite difference itself is not trustworthy (noise or curvature), with the step
 * to try next; and, for an objective without an adjoint, "consistent" says the finite difference is sound, nothing more.
 */
export function checkVerdict(check: CheckResult, unitOf: (reference: string) => string = () => ""): CheckVerdict | null {
  if (check.status !== "done") return null;
  const weak = check.rows.find((r) => !r.consistent);
  if (check.verdict === "inconclusive" || weak !== undefined) {
    const row = weak ?? check.rows[0];
    const why = row?.cause === "curvature" ? "the forward and backward differences disagree (curvature)" : "the change in J is too close to the mesh noise";
    const next = row?.suggestedStep != null ? `; try a step of ${formatValue(row.suggestedStep, unitOf(row.parameter))}` : "";
    return { word: "inconclusive", tone: "warn", detail: `${row ? `${row.parameter}: ` : ""}${why}${next}` };
  }
  if (check.verdict === "passed") {
    return { word: "verified", tone: "ok", detail: `the adjoint gradient agrees with finite differences${check.cosine !== null && check.rows.length > 1 ? ` (cosine ${check.cosine.toFixed(3)})` : ""}` };
  }
  if (check.verdict === "failed") {
    return {
      word: "disagrees",
      tone: "bad",
      detail: check.wrongSign ? "the adjoint gradient has the wrong sign: use finite differences" : "the adjoint gradient is off by more than 25 %: use finite differences",
    };
  }
  if (check.verdict === "no_adjoint") return { word: "consistent", tone: "ok", detail: "no adjoint was compared here; the finite differences are sound" };
  // An older server: the single ratio is all there is.
  if (check.ratio === null) return null;
  const near = Math.abs(check.ratio - 1) <= 0.1;
  return near ? { word: "verified", tone: "ok", detail: `ratio ${check.ratio.toFixed(2)}` } : { word: "disagrees", tone: "bad", detail: `ratio ${check.ratio.toFixed(2)}` };
}

// ---------------------------------------------------------------------------------------------- the flow split
/** One outlet's share of the flow in one cycle. */
export interface OutletShare {
  outlet: string;
  /** Q_i / Q, 0..1. */
  share: number;
  /** Q_i in m³/s, when the cycle carries it. */
  flow: number | null;
}

/** A cycle's flow split: each outlet's share, the equal share 1/N and the largest deviation from it; null without one. */
export interface CycleSplit {
  outlets: OutletShare[];
  equal: number;
  deviation: number;
  /** The mass balance |Σ Q| / Q_in, when the cycle carries it. */
  balance: number | null;
}

export function readSplit(cycle: ShapeCycle | null | undefined): CycleSplit | null {
  const detail = cycle?.detail;
  if (!detail || !isRecord(detail.fractions)) return null;
  const flows = isRecord(detail.flows) ? detail.flows : {};
  const outlets: OutletShare[] = [];
  for (const [outlet, value] of Object.entries(detail.fractions)) {
    const share = num(value);
    if (share !== null) outlets.push({ outlet, share, flow: num(flows[outlet]) });
  }
  if (outlets.length === 0) return null;
  const equal = 1 / outlets.length;
  const deviation = num(detail.deviation) ?? Math.max(...outlets.map((o) => Math.abs(o.share - equal)));
  return { outlets, equal, deviation, balance: num(detail.mass_balance) };
}

/** The outlet names over a run's cycles, in the order the first split gave them. */
export function splitOutlets(cycles: readonly ShapeCycle[]): string[] {
  const out: string[] = [];
  for (const c of cycles) for (const o of readSplit(c)?.outlets ?? []) if (!out.includes(o.outlet)) out.push(o.outlet);
  return out;
}

/** A share as a percentage, one decimal: "25.1 %". */
export function shareText(share: number): string {
  return Number.isFinite(share) ? `${(share * 100).toFixed(1)} %` : "n/a";
}

/** The project's outlets: the boundary names of its pressure outlets, in tree order (the flow split shares among them). */
export function outletNames(tree: NodeSummary | null): string[] {
  if (tree === null) return [];
  const byId = new Map<string, NodeSummary>();
  const boundaries: NodeSummary[] = [];
  const walk = (node: NodeSummary) => {
    byId.set(node.id, node);
    if (node.type === "boundary" && node.params.kind === "pressure_outlet") boundaries.push(node);
    for (const child of node.children ?? []) walk(child);
  };
  walk(tree);
  const out: string[] = [];
  for (const b of boundaries) {
    const tag = byId.get(String(b.params.tag ?? ""));
    if (tag && !out.includes(tag.name)) out.push(tag.name);
  }
  return out;
}

/** `shape.applied`: the apply finished, with the tags found again, or failed with why. */
export function readApplied(data: unknown): { ok: boolean; error: string; tags: string[]; rebuilt: number } {
  const d = isRecord(data) ? data : {};
  const ok = text(d.status) === "done";
  return { ok, error: text(d.error), tags: isRecord(d.tags) ? Object.keys(d.tags) : [], rebuilt: num(d.rebuilt) ?? 0 };
}

/** The finite-difference steps (SI) for the variables: `percent` of each one's span; null for a span that is not positive. */
export function fdSteps(variables: ReadonlyArray<{ lower: number; upper: number }>, percent: number): number[] | null {
  if (!(percent > 0 && percent < 100)) return null;
  const out = variables.map((v) => ((v.upper - v.lower) * percent) / 100);
  return out.every((s) => s > 0 && Number.isFinite(s)) ? out : null;
}

/** One `shape.step` event: where a CFD cycle of an optimisation or a check is. */
export interface ShapeStep {
  run: "optimise" | "check";
  step: string;
  /** The cycle's number, from 1; 0 from an older server. */
  cycle: number;
  stepNumber: number;
  steps: number;
  /** Seconds since the cycle began, when the step began. */
  elapsed: number;
  /** For a check: which of the three evaluations (1..3), and what it is ("base", "plus", "minus"). */
  check: number;
  checks: number;
  checkRun: string;
  /** For finite differences: the solve within the cycle (1..solves), 0 when a cycle is one solve. */
  solve: number;
  solves: number;
}

export function readShapeStep(data: unknown): ShapeStep | null {
  if (!isRecord(data) || text(data.step) === "") return null;
  return {
    run: data.run === "check" ? "check" : "optimise",
    step: text(data.step),
    cycle: num(data.cycle) ?? 0,
    stepNumber: num(data.step_number) ?? 0,
    steps: num(data.steps) ?? 0,
    elapsed: num(data.elapsed) ?? 0,
    check: num(data.check) ?? 0,
    checks: num(data.checks) ?? 3,
    checkRun: text(data.check_run),
    solve: num(data.solve) ?? 0,
    solves: num(data.solves) ?? 0,
  };
}

/** What each step of a cycle does, in words. */
const STEP_WORDS: Record<string, string> = {
  open: "preparing a copy",
  rebuild: "rebuilding the part",
  tags: "finding the boundaries",
  mesh: "meshing",
  solve: "solving flow and adjoint",
  sensitivity: "reading the sensitivity",
};

const CHECK_WORDS: Record<string, string> = { base: "at the current value", plus: "value + step", minus: "value − step" };

/** A duration as a person reads it: "8 s", "1 min 12 s", "1 h 4 min". */
export function durationText(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/**
 * The progress line of a cycle: "Check 2/3 (value + step) · meshing · step 4 of 6 · 1 min 12 s" or "Cycle 3 of at
 * most 20 · solving flow and adjoint · step 5 of 6 · 40 s". `since` is the seconds since the step's event arrived.
 */
export function stepText(step: ShapeStep, opts: { since?: number; maxCycles?: number; cyclesDone?: number; adjoint?: boolean } = {}): string {
  // A finite-difference solve has no adjoint: say what is running, not what the adjoint path would run.
  const words = step.step === "solve" && opts.adjoint === false ? "solving the flow" : step.step === "sensitivity" && opts.adjoint === false ? "reading the outlet flows" : (STEP_WORDS[step.step] ?? step.step);
  const head =
    step.run === "check"
      ? `Check ${Math.max(1, step.check)}/${step.checks}${CHECK_WORDS[step.checkRun] ? ` (${CHECK_WORDS[step.checkRun]})` : ""}`
      : `Cycle ${step.cycle > 0 ? step.cycle : (opts.cyclesDone ?? 0) + 1}${opts.maxCycles ? ` of at most ${opts.maxCycles}` : ""}${step.solves > 1 && step.solve > 0 ? ` · solve ${step.solve} of ${step.solves}` : ""}`;
  const where = step.stepNumber > 0 && step.steps > 0 ? ` · step ${step.stepNumber} of ${step.steps}` : "";
  const time = durationText(step.elapsed + (opts.since ?? 0));
  return `${head} · ${words}${where}${time ? ` · ${time}` : ""}`;
}

// ---------------------------------------------------------------------------------------------- the model
/** The parts made with Create (the only ones the optimiser can rebuild), by id and name. */
export function implicitParts(tree: NodeSummary | null): Array<{ id: string; name: string; path: string }> {
  const out: Array<{ id: string; name: string; path: string }> = [];
  const walk = (node: NodeSummary) => {
    if (node.type === "part" && node.params.family === "implicit" && node.params.simulate !== false) out.push({ id: node.id, name: node.name, path: node.path });
    for (const child of node.children ?? []) walk(child);
  };
  if (tree) walk(tree);
  return out;
}

/** The project's parameters (the names a variable most often is). */
export function parameterNames(tree: NodeSummary | null): string[] {
  const out: string[] = [];
  const walk = (node: NodeSummary) => {
    if (node.type === "parameter" && node.path.startsWith("/physics/parameters/")) out.push(node.name);
    for (const child of node.children ?? []) walk(child);
  };
  if (tree) walk(tree);
  return out.sort();
}

/** Whether the project has a flow set up: a boundary condition exists, so the adjoint objective applies. */
export function hasFlowSetup(tree: NodeSummary | null): boolean {
  if (tree === null) return false;
  if (tree.type === "boundary") return true;
  return (tree.children ?? []).some((child) => hasFlowSetup(child));
}

// ---------------------------------------------------------------------------------------------- names and units
/**
 * A variable as a person names it: "Sketch 1 · pull distance", "parameter R". Never the reference cut short
 * (`geometr...1.depth`): a reference the server did not describe is spelled from its last two parts
 * ("sketch 1 · depth"), and the whole reference is the row's tooltip.
 */
export function variableName(candidate: Candidate | undefined, reference: string): string {
  if (candidate !== undefined) {
    if (candidate.body === "parameter") return `parameter ${candidate.quantity || candidate.reference}`;
    // Only the first letter is lowered ("Pull distance" reads "pull distance"): a name inside the label
    // is the user's own and keeps its case ("dimension H", not "dimension h").
    if (candidate.body !== "" && candidate.quantity !== "") return `${candidate.body} · ${candidate.quantity.charAt(0).toLowerCase()}${candidate.quantity.slice(1)}`;
    if (candidate.label !== "") return candidate.label;
  }
  const parts = reference.trim().split(".").filter((p) => p !== "");
  if (parts.length <= 1) return reference.trim();
  const tail = parts.slice(-2).map((p) => p.replace(/_/g, " "));
  return `${tail[0]} · ${tail[1]}`;
}

/** The unit a quantity is shown in, and the factor from SI to it: lengths in mm, angles in degrees, as the modeller shows them. */
export function displayUnit(unit: string): { unit: string; scale: number } {
  switch (unit) {
    case "m":
      return { unit: "mm", scale: 1000 };
    case "m^2":
    case "m2":
    case "m²":
      return { unit: "mm²", scale: 1e6 };
    case "m^3":
    case "m3":
    case "m³":
      return { unit: "mm³", scale: 1e9 };
    case "rad":
      return { unit: "°", scale: 180 / Math.PI };
    case "1":
      return { unit: "", scale: 1 };
    default:
      return { unit, scale: 1 };
  }
}

/** An SI value in its display unit, as the text a bound field starts with (six significant digits, no float dust). */
export function boundText(value: number, unit: string): string {
  if (!Number.isFinite(value)) return "";
  return String(Number((value * displayUnit(unit).scale).toPrecision(6)));
}

/** A value with its unit as a person reads it: lengths in millimetres, angles in degrees, the rest as they are. */
export function formatValue(value: number, unit: string): string {
  if (!Number.isFinite(value)) return "n/a";
  const shown = displayUnit(unit);
  const textValue = Number((value * shown.scale).toPrecision(4)).toString();
  if (shown.unit === "") return textValue;
  return shown.unit === "°" ? `${textValue}°` : `${textValue} ${shown.unit}`;
}

/**
 * The rows as the command's variables, bounds in SI, or why they cannot be. `unitOf` gives a reference's SI unit
 * (its bounds were typed in that unit's display unit: mm for "m"); a reference it does not know is typed in SI.
 */
export function readVariables(
  rows: readonly VariableRow[],
  unitOf: (reference: string) => string = () => "",
): { ok: true; value: Array<{ reference: string; lower: number; upper: number }> } | { ok: false; error: string } {
  const value: Array<{ reference: string; lower: number; upper: number }> = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const reference = row.reference.trim();
    if (reference === "") continue;
    const shown = displayUnit(unitOf(reference));
    const lower = row.lower.trim() === "" ? Number.NaN : Number(row.lower);
    const upper = row.upper.trim() === "" ? Number.NaN : Number(row.upper);
    const unitWords = shown.unit === "" ? "" : ` (${shown.unit})`;
    if (!Number.isFinite(lower) || !Number.isFinite(upper)) return { ok: false, error: `${reference}: the bounds must be numbers${unitWords}` };
    if (!(lower < upper)) return { ok: false, error: `${reference}: the lower bound must be below the upper` };
    if (seen.has(reference)) return { ok: false, error: `${reference} is listed twice` };
    seen.add(reference);
    value.push({ reference, lower: lower / shown.scale, upper: upper / shown.scale });
  }
  if (value.length === 0) return { ok: false, error: "Add a variable: a quantity of the part, with its bounds" };
  return { ok: true, value };
}

/** The objective's unit: the adjoint's total-pressure power is per unit density (m⁵/s³); the volume objective has none. */
export function objectiveUnit(objective: string): string {
  return objective === "pressure_loss" ? "m⁵/s³" : "";
}

/** The objectives the panel offers, in words. */
export type Objective = "volume" | "pressure_loss" | "flow_split";
/** Where a CFD objective's gradient comes from. */
export type GradientSource = "adjoint" | "finite_difference";

/**
 * The gradient sources an objective may use. The flow split's adjoint needs its own solver program on the compute host,
 * so it is offered only when `shape.gradients` found it (`splitAdjoint`); finite differences stay its default.
 */
export function gradientSources(objective: Objective, splitAdjoint = false): GradientSource[] {
  if (objective === "volume") return [];
  if (objective === "flow_split") return splitAdjoint ? ["finite_difference", "adjoint"] : ["finite_difference"];
  return ["adjoint", "finite_difference"];
}

/** The gradient an objective starts with: the adjoint for the pressure loss, finite differences for the flow split. */
export function defaultGradient(objective: Objective): GradientSource {
  return objective === "pressure_loss" ? "adjoint" : "finite_difference";
}

/** What `shape.gradients` says the compute host allows: whether the flow split's adjoint can run, and why not. */
export interface GradientOffer {
  splitAdjoint: boolean;
  /** Why the flow split's adjoint is not offered ("" when it is, or when the server did not say). */
  why: string;
}

/** A `shape.gradients` reply, read defensively: anything missing means the flow split's adjoint is not offered. */
export function readGradientOffer(reply: unknown): GradientOffer {
  const data = reply !== null && typeof reply === "object" ? (reply as Record<string, unknown>) : {};
  const objectives = data.objectives !== null && typeof data.objectives === "object" ? (data.objectives as Record<string, unknown>) : {};
  const split = Array.isArray(objectives.flow_split) ? objectives.flow_split : [];
  const program = data.adjoint_program !== null && typeof data.adjoint_program === "object" ? (data.adjoint_program as Record<string, unknown>) : {};
  const splitAdjoint = split.includes("adjoint");
  return { splitAdjoint, why: splitAdjoint ? "" : typeof program.why === "string" ? program.why : "" };
}

/** Why the adjoint radio is greyed out for the flow split, in the panel's words (no internal references). */
export function splitAdjointNote(offer: GradientOffer): string {
  if (offer.splitAdjoint) return "1 solve per cycle with the open-source adjoint; experimental, unverified until a check passes; takes every outlet";
  return "needs the open-source adjoint solver (adjointFoam) on the compute host";
}

/** How many CFD solves one cycle costs: one (flow and adjoint together) for the adjoint, N + 1 for finite differences. */
export function solvesPerCycle(source: GradientSource, variables: number): number {
  return source === "adjoint" ? 1 : Math.max(1, variables) + 1;
}

/** The objective's name in a sentence. */
export function objectiveWords(objective: string): string {
  switch (objective) {
    case "pressure_loss":
      return "pressure loss";
    case "flow_split":
      return "flow split";
    case "volume":
      return "volume";
    default:
      return objective;
  }
}

// ---------------------------------------------------------------------------------------------- the chart
/** The points of one series over the cycles in a box, on a log scale (see `seriesPoints`). */
export function seriesCoords(values: readonly number[], width: number, height: number, pad = 4): Array<{ x: number; y: number }> {
  if (values.length === 0) return [];
  const logs = values.map((v) => (v > 0 && Number.isFinite(v) ? Math.log10(v) : Number.NaN));
  const finite = logs.filter((v) => Number.isFinite(v));
  const lo = finite.length ? Math.min(...finite) : 0;
  const hi = finite.length ? Math.max(...finite) : 1;
  const span = hi - lo || 1;
  const step = values.length > 1 ? (width - 2 * pad) / (values.length - 1) : (width - 2 * pad) / 2;
  const first = values.length > 1 ? pad : width / 2;
  return logs.map((v, i) => ({
    x: Number((first + i * step).toFixed(1)),
    y: Number((Number.isFinite(v) ? (hi === lo ? height / 2 : height - pad - ((v - lo) / span) * (height - 2 * pad)) : height - pad).toFixed(1)),
  }));
}

/**
 * The polyline of one series over the cycles, in a box of `width` x `height`, on a log scale
 * (objectives and gradient norms span decades). Non-positive values sit at the bottom.
 */
export function seriesPoints(values: readonly number[], width: number, height: number, pad = 4): string {
  if (values.length === 0) return "";
  const logs = values.map((v) => (v > 0 && Number.isFinite(v) ? Math.log10(v) : Number.NaN));
  const finite = logs.filter((v) => Number.isFinite(v));
  const lo = finite.length ? Math.min(...finite) : 0;
  const hi = finite.length ? Math.max(...finite) : 1;
  const span = hi - lo || 1;
  const step = values.length > 1 ? (width - 2 * pad) / (values.length - 1) : 0;
  return logs
    .map((v, i) => {
      const y = Number.isFinite(v) ? height - pad - ((v - lo) / span) * (height - 2 * pad) : height - pad;
      return `${(pad + i * step).toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/** The space round the plot for the tick labels. */
export const CHART_PAD = { left: 40, right: 8, top: 8, bottom: 18 };

export interface ConvergenceChart {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  points: Array<{ x: number; y: number; cycle: number; value: number }>;
  yTicks: Array<{ at: number; label: string }>;
  xTicks: Array<{ at: number; label: string }>;
  /** True when the values span more than two decades and are drawn on a log axis. */
  log: boolean;
  /** The power of ten the linear tick labels are written in ("×10⁻⁸"), 0 for none. */
  exponent: number;
  /** Where the reference line (a target) is drawn, or null without one. */
  reference: { at: number; value: number } | null;
}

const SUPERSCRIPT: Record<string, string> = { "-": "⁻", "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹" };

/** "×10⁻⁸" for -8, "" for 0. */
export function powerText(exponent: number): string {
  if (exponent === 0) return "";
  return `×10${String(exponent)
    .split("")
    .map((c) => SUPERSCRIPT[c] ?? c)
    .join("")}`;
}

function linearTicks(lo: number, hi: number, count: number): number[] {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

/**
 * One series against the cycle number, with numbered axes. A series spanning more than two decades (a gradient
 * norm) is drawn on a log axis with ticks at the powers of ten; otherwise linear, its tick labels written in one
 * power of ten (`exponent`) so neighbouring ticks never read the same (9.25 and 9.30, not 9.3e-8 twice).
 */
export function convergenceChart(values: readonly number[], width: number, height: number, cycles?: readonly number[], reference?: number): ConvergenceChart {
  const x0 = CHART_PAD.left;
  const y0 = CHART_PAD.top;
  const x1 = Math.max(x0 + 1, width - CHART_PAD.right);
  const y1 = Math.max(y0 + 1, height - CHART_PAD.bottom);
  const labels = values.map((_, i) => cycles?.[i] ?? i + 1);
  const shown = values.filter((v) => Number.isFinite(v));
  const empty: ConvergenceChart = { x0, y0, x1, y1, points: [], yTicks: [], xTicks: [], log: false, exponent: 0, reference: null };
  if (shown.length === 0) return empty;
  // A reference line (a target) widens the range so it is always on the chart.
  const finite = reference !== undefined && Number.isFinite(reference) ? [...shown, reference] : shown;
  const positive = finite.filter((v) => v > 0);
  const lo0 = Math.min(...finite);
  const hi0 = Math.max(...finite);
  const log = positive.length === finite.length && lo0 > 0 && hi0 / lo0 > 100;
  let lo: number;
  let hi: number;
  if (log) {
    lo = Math.floor(Math.log10(lo0));
    hi = Math.ceil(Math.log10(hi0));
    if (hi === lo) hi = lo + 1;
  } else {
    const pad = hi0 > lo0 ? (hi0 - lo0) * 0.08 : Math.abs(hi0) * 0.05 || 0.5;
    lo = lo0 - pad;
    hi = hi0 + pad;
  }
  const yOf = (v: number) => {
    const t = log ? (Math.log10(v) - lo) / (hi - lo) : (v - lo) / (hi - lo);
    return Number((y1 - t * (y1 - y0)).toFixed(1));
  };
  const n = values.length;
  const xOf = (i: number) => Number((n > 1 ? x0 + (i / (n - 1)) * (x1 - x0) : (x0 + x1) / 2).toFixed(1));
  const points: ConvergenceChart["points"] = [];
  values.forEach((v, i) => {
    if (Number.isFinite(v) && (!log || v > 0)) points.push({ x: xOf(i), y: yOf(v), cycle: labels[i]!, value: v });
  });
  let exponent = 0;
  let yTicks: ConvergenceChart["yTicks"];
  if (log) {
    const every = Math.max(1, Math.ceil((hi - lo) / 4));
    yTicks = [];
    for (let e = lo; e <= hi; e += every) yTicks.push({ at: yOf(10 ** e), label: e === 0 ? "1" : powerText(e).slice(1) });
  } else {
    const ticks = linearTicks(lo, hi, 3);
    const big = Math.max(...ticks.map((t) => Math.abs(t)));
    exponent = big > 0 && (big < 1e-2 || big >= 1e4) ? Math.floor(Math.log10(big)) : 0;
    yTicks = ticks.map((t) => ({ at: yOf(t), label: String(Number((t / 10 ** exponent).toPrecision(4))) }));
  }
  // The cycle numbers: every one when they fit, else about six.
  const every = Math.max(1, Math.ceil(n / 6));
  const xTicks: ConvergenceChart["xTicks"] = [];
  for (let i = 0; i < n; i += every) xTicks.push({ at: xOf(i), label: String(labels[i]) });
  if (n > 1 && (n - 1) % every !== 0) xTicks.push({ at: xOf(n - 1), label: String(labels[n - 1]) });
  const line = reference !== undefined && Number.isFinite(reference) && (!log || reference > 0) ? { at: yOf(reference), value: reference } : null;
  return { x0, y0, x1, y1, points, yTicks, xTicks, log, exponent, reference: line };
}

// ---------------------------------------------------------------------------------------------- the history
/** The best (lowest objective) cycle, or null. */
export function bestCycle(cycles: readonly ShapeCycle[]): ShapeCycle | null {
  return cycles.reduce<ShapeCycle | null>((best, c) => (best === null || c.objective < best.objective ? c : best), null);
}

/** One variable of the history's columns: its reference, its name and its SI unit. */
export interface HistoryColumn {
  reference: string;
  name: string;
  unit: string;
}

/**
 * The run's history as CSV: one line per cycle with J, |∇J|, each variable in its display unit and its own
 * gradient component, and the cycle's seconds. Values at full precision; the header names the units.
 */
export function historyCsv(cycles: readonly ShapeCycle[], columns: readonly HistoryColumn[], objective: string): string {
  const quote = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const unit = objectiveUnit(objective);
  const outlets = splitOutlets(cycles);
  const header = [
    "cycle",
    `J${unit ? ` (${unit})` : ""}`,
    "|grad J|",
    ...columns.flatMap((c) => {
      const shown = displayUnit(c.unit).unit;
      return [`${c.name}${shown ? ` (${shown})` : ""}`, `dJ/d ${c.name} (per ${c.unit || "1"})`];
    }),
    "source",
    "seconds",
    // The flow split's shares, one column per outlet, and the largest deviation from the equal share.
    ...outlets.map((o) => `share ${o}`),
    ...(outlets.length > 0 ? ["max deviation"] : []),
  ];
  const cell = (v: number | undefined) => (v === undefined || !Number.isFinite(v) ? "" : String(v));
  const lines = cycles.map((c) => {
    const split = readSplit(c);
    return [
      String(c.index + 1),
      cell(c.objective),
      cell(c.gradient_norm),
      ...columns.flatMap((col, i) => [cell(c.x[i] === undefined ? undefined : c.x[i]! * displayUnit(col.unit).scale), cell(c.gradient[i])]),
      quote(c.source),
      cell(c.seconds),
      ...outlets.map((o) => cell(split?.outlets.find((x) => x.outlet === o)?.share)),
      ...(outlets.length > 0 ? [cell(split?.deviation)] : []),
    ].join(",");
  });
  return [`# shape optimisation, objective: ${objective}`, header.map(quote).join(","), ...lines].join("\n") + "\n";
}

/**
 * A value as a reader takes it in: 4 significant figures, in scientific form with a superscript power
 * outside 0.01..10 000 ("1.439×10⁻⁵", not "0.00001439" whose zeros have to be counted).
 */
export function sciText(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  const size = Math.abs(value);
  if (size === 0 || (size >= 0.01 && size < 1e4)) return String(Number(value.toPrecision(4)));
  const exponent = Math.floor(Math.log10(size));
  const mantissa = Number((value / 10 ** exponent).toPrecision(4));
  return `${mantissa}${powerText(exponent)}`;
}
