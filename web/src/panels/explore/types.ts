/**
 * The shapes of `study.results`, `study.pareto` and the `study.*` events (docs/engineering/study-schema.md),
 * and the readers that turn what the socket sent into them. A reply is data from the wire: each reader
 * checks the shape it needs, drops what does not fit and returns null when the reply is not a table at
 * all, so a panel never meets a value of the wrong type. Every number in a table is SI; the columns say
 * their dimension, their SI unit and the project's display unit.
 */

export type RowStatus = "pending" | "running" | "done" | "failed";
export const ROW_STATUSES: readonly RowStatus[] = ["pending", "running", "done", "failed"];

/** A design-table column: a variable (input) or a response (output). */
export interface Column {
  name: string;
  dimension: string;
  /** The SI unit, as the server writes it ("m**3/s"). */
  unit: string;
  /** The parameter a variable varies, or the expression a response records. */
  source: string;
  /** The response's display name; empty when it has none. */
  label: string;
  /** The project's display unit for the dimension ("L/min"); the SI unit when the server sent none. */
  displayUnit: string;
}

/** One design point. `inputs` and `outputs` hold only the finite SI numbers the server sent. */
export interface PointRow {
  index: number;
  inputs: Record<string, number>;
  outputs: Record<string, number>;
  status: RowStatus;
  run: string | null;
  retain: boolean;
  error: string | null;
}

export type StudyCounts = Record<RowStatus, number>;

export interface StudyResults {
  /** The study node's id. */
  study: string;
  path: string;
  name: string;
  type: string;
  /** The study node's status: idle, generated, running, done, failed. */
  status: string;
  sampler: string | null;
  seed: number | null;
  engine: string | null;
  generated: string | null;
  updated: string | null;
  variables: Column[];
  responses: Column[];
  counts: StudyCounts;
  points: PointRow[];
}

export interface ParetoObjective {
  /** The objective node's name; the key of `ParetoPoint.objectives`. */
  name: string;
  /** The response the objective optimises, by name. */
  response: string;
  direction: "minimise" | "maximise";
}

export interface ParetoPoint {
  index: number;
  objectives: Record<string, number>;
  /** 0 is the front. Failed points rank -1 and infeasible ones after every feasible one. */
  rank: number;
  crowding: number | null;
  feasible: boolean;
  violation: number | null;
  onFront: boolean;
  error?: string;
}

export interface ParetoResult {
  objectives: ParetoObjective[];
  /** Indices of the feasible non-dominated points, ordered by the first objective. */
  front: number[];
  points: ParetoPoint[];
  /** Indices of the points that were not evaluated. */
  excluded: number[];
}

/** The payload of a `study.point` event. */
export interface PointEvent {
  study: string;
  index: number;
  status: RowStatus;
  outputs: Record<string, number>;
  error: string | null;
}

// ---------------------------------------------------------------------------------------------- readers
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function finiteNumbers(value: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (isRecord(value)) for (const [key, v] of Object.entries(value)) if (typeof v === "number" && Number.isFinite(v)) out[key] = v;
  return out;
}

function isStatus(value: unknown): value is RowStatus {
  return typeof value === "string" && (ROW_STATUSES as readonly string[]).includes(value);
}

function readColumn(value: unknown): Column | null {
  if (!isRecord(value) || typeof value.name !== "string") return null;
  const unit = text(value.unit) ?? "";
  return {
    name: value.name,
    dimension: text(value.dimension) ?? "dimensionless",
    unit,
    source: text(value.source) ?? "",
    label: text(value.label) ?? "",
    displayUnit: text(value.display_unit) ?? unit,
  };
}

function readColumns(value: unknown): Column[] {
  return Array.isArray(value) ? value.map(readColumn).filter((c): c is Column => c !== null) : [];
}

export function readRow(value: unknown): PointRow | null {
  if (!isRecord(value) || typeof value.index !== "number" || !Number.isInteger(value.index) || value.index < 0) return null;
  return {
    index: value.index,
    inputs: finiteNumbers(value.inputs),
    outputs: finiteNumbers(value.outputs),
    status: isStatus(value.status) ? value.status : "pending",
    run: text(value.run),
    retain: value.retain === true,
    error: text(value.error),
  };
}

export function countRows(points: readonly PointRow[]): StudyCounts {
  const counts: StudyCounts = { pending: 0, running: 0, done: 0, failed: 0 };
  for (const row of points) counts[row.status]++;
  return counts;
}

/** `study.results` (format json), or null when the reply is not a table. */
export function readResults(raw: unknown): StudyResults | null {
  if (!isRecord(raw) || typeof raw.study !== "string" || !Array.isArray(raw.points)) return null;
  const points = raw.points.map(readRow).filter((r): r is PointRow => r !== null);
  return {
    study: raw.study,
    path: text(raw.path) ?? "",
    name: text(raw.name) ?? "",
    type: text(raw.type) ?? "sweep",
    status: text(raw.status) ?? "idle",
    sampler: text(raw.sampler),
    seed: typeof raw.seed === "number" ? raw.seed : null,
    engine: text(raw.engine),
    generated: text(raw.generated),
    updated: text(raw.updated),
    variables: readColumns(raw.variables),
    responses: readColumns(raw.responses),
    // The rows are the truth; the server's counts are for the clients that do not read rows.
    counts: countRows(points),
    points,
  };
}

function readObjective(value: unknown): ParetoObjective | null {
  if (!isRecord(value) || typeof value.name !== "string" || typeof value.response !== "string") return null;
  return { name: value.name, response: value.response, direction: value.direction === "maximise" ? "maximise" : "minimise" };
}

function readParetoPoint(value: unknown): ParetoPoint | null {
  if (!isRecord(value) || typeof value.index !== "number" || !Number.isInteger(value.index)) return null;
  const point: ParetoPoint = {
    index: value.index,
    objectives: finiteNumbers(value.objectives),
    rank: typeof value.rank === "number" ? value.rank : -1,
    crowding: typeof value.crowding === "number" && Number.isFinite(value.crowding) ? value.crowding : null,
    feasible: value.feasible !== false,
    violation: typeof value.violation === "number" && Number.isFinite(value.violation) ? value.violation : null,
    onFront: value.on_front === true,
  };
  if (typeof value.error === "string") point.error = value.error;
  return point;
}

function integers(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((v): v is number => typeof v === "number" && Number.isInteger(v)) : [];
}

/** `study.pareto`, or null when the reply is not a Pareto set. */
export function readPareto(raw: unknown): ParetoResult | null {
  if (!isRecord(raw) || !Array.isArray(raw.objectives) || !Array.isArray(raw.points)) return null;
  return {
    objectives: raw.objectives.map(readObjective).filter((o): o is ParetoObjective => o !== null),
    front: integers(raw.front),
    points: raw.points.map(readParetoPoint).filter((p): p is ParetoPoint => p !== null),
    excluded: integers(raw.excluded),
  };
}

/** The payload of a `study.point` event, or null when it is not one. */
export function readPointEvent(data: Record<string, unknown>): PointEvent | null {
  if (typeof data.study !== "string" || typeof data.index !== "number" || !isStatus(data.status)) return null;
  return { study: data.study, index: data.index, status: data.status, outputs: finiteNumbers(data.outputs), error: text(data.error) };
}
