/**
 * What the tree shows for every stage, always (T-219 B). The server's tree holds the nodes that exist; a
 * person working through Geometry, Mesh, Physics, Solve and Results also expects to see the settings that
 * do NOT exist yet and the things that are not nodes at all (a run is a record, a field is a result). This
 * module derives the rows for those from the tree and the store slices that exist, as a decorated copy of
 * the tree, so `TreePanel` keeps ONE row component, one windowing and one keyboard model:
 *
 * * a **placeholder** is a dimmed row where a node will be once the first edit makes it (`Mesh settings`,
 *   `Solver settings`, `Mesh result`, `Flow model`): it carries `meta.placeholder`, and opening it shows the
 *   panel that creates the node (`mesh.configure`, `solve.configure`);
 * * a **virtual** row stands for something that is not a node: the runs, the results' fields, slice,
 *   residuals and monitors, and the physics stage's `Flow model` (which edits `solver_settings.turbulence`);
 * * a **real** node of the set-up (a region, a boundary, a mesh result, the solver settings) keeps its
 *   id and gets a subtitle: the material and the boundary count, "inlet · 0.1 m/s", "12,000 cells . stale".
 *
 * Nothing here is written back to the project: the copy is for drawing, selection and the commands keep
 * using the server's own ids. A virtual id starts with `virtual:` and is never `selectedId`.
 *
 * Import rule: no React, no store, no DOM. The groups of the tree are found by path, as the stage map does.
 */
import type { ConditionKind, NodeStatus, NodeSummary, SetupCheck } from "../api/types";
import { inUseParams } from "./conditions";
import { fieldLabel, fieldUnits, formatTick, type ResultsLive } from "./fieldsModel";
import { findSolverSettings, formatResidual } from "./runPanelModel";
import { findMeshNodes, formatCount, isStale, readResult } from "./meshPanelModel";
import { monitorsOf } from "./monitorsModel";
import { INITIAL_TURBULENCE_SECTION } from "./settingsPresentation";
import type { TasksReply } from "./meshTasks";
import type { RunSummary } from "./runApi";

export const VIRTUAL_PREFIX = "virtual:";

/** The panel a row opens (the ids the workspace registers) and, optionally, the section to bring into view. */
export interface OpenTarget {
  /** A registered panel id (the registry and the Details pane both know these), or null for a row whose page is a card. */
  panel: "mesh" | "solve" | "results" | "conditions" | "mesh-boundaries" | null;
  section?: string;
  /** A geometry operation card to open, by operation type (the Prepare menu's card, `OperationCard`). */
  operation?: string;
  /** A real node to select, whose own page is the card (an existing operation node). */
  select?: string;
}

/** What a virtual or placeholder row is, in `meta.row`; the tree panel acts on it. */
export type RowKind =
  | "mesh-settings"
  | "mesh-result"
  | "mesh-capping"
  | "mesh-boundaries"
  | "mesh-surface"
  | "mesh-layers"
  | "mesh-quality"
  | "mesh-fidelity"
  | "solver-settings"
  | "flow-model"
  | "run"
  | "fields"
  | "field"
  | "surfaces"
  | "slice"
  | "residuals"
  | "monitors"
  | "result-vectors"
  | "result-streamlines"
  | "result-probe"
  | "result-report"
  | "result-lod";

export interface RowMeta {
  row: RowKind;
  /** The row stands for a node that does not exist yet. */
  placeholder: boolean;
  subtitle: string;
  /** For a run row, the run; for a field row, the field. */
  ref?: string;
}

export interface TreeContext {
  kinds: readonly ConditionKind[];
  runs: readonly RunSummary[];
  results: Pick<ResultsLive, "data" | "view">;
  setup: SetupCheck | null;
  /**
   * The guided-meshing tasks' answer (brief M-502), or null before the first one arrives: the Mesh
   * section's header reads "Step N of 9" from it, and the task rows' value lines come from the same
   * reply, so the tree and the stepper cannot disagree about where the user is. **Optional**
   * because the tree decorates before the first ask lands: without it the Mesh section simply has
   * no task rows yet, which is the honest state of a window that just opened.
   */
  tasks?: TasksReply | null;
}

// ---------------------------------------------------------------------------------------------- reading rows
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The row facts of a decorated node, or null for a node of the server's that was not decorated. */
export function rowMetaOf(node: NodeSummary): RowMeta | null {
  const meta = node.meta;
  if (!isRecord(meta) || typeof meta.row !== "string") return null;
  return { row: meta.row as RowKind, placeholder: meta.placeholder === true, subtitle: typeof meta.subtitle === "string" ? meta.subtitle : "", ref: typeof meta.ref === "string" ? meta.ref : undefined };
}

export function isVirtualId(id: string): boolean {
  return id.startsWith(VIRTUAL_PREFIX);
}

/** The subtitle a decorated node carries, or "" (the tree row then reads the node as it always did). */
export function subtitleOf(node: NodeSummary): string {
  const meta = node.meta;
  return isRecord(meta) && typeof meta.subtitle === "string" ? meta.subtitle : "";
}

export function isPlaceholder(node: NodeSummary): boolean {
  return isRecord(node.meta) && node.meta.placeholder === true;
}

/**
 * The panel a row opens on a double click or Enter, or null for a row with none. Real nodes count too: the
 * mesh settings and result open the Mesh panel, the solver settings the Solve panel, a region or a boundary
 * the Conditions panel; the results rows open the Results panel at their own section.
 */
export function openTargetOf(node: NodeSummary): OpenTarget | null {
  const meta = rowMetaOf(node);
  if (meta !== null) {
    switch (meta.row) {
      case "mesh-settings":
      case "mesh-result":
        return { panel: "mesh" };
      // The mesh tasks (brief M-502): Boundaries has its own page; Surface, Layers, Quality and
      // Fidelity show the Mesh panel's existing sections until their packages land (M-515, M-505,
      // M-506/M-507) — the brief's own fallback for them.
      case "mesh-boundaries":
        return { panel: "mesh-boundaries" };
      // Capping (ADR-0026 decision 2) is the `cap` geometry operation, so its page is the cap's card,
      // not the Mesh panel: the first cap node (`ref`) when there is one, whose page is that
      // operation's own; otherwise the Close openings card the Prepare menu opens.
      case "mesh-capping":
        return meta.ref !== undefined ? { panel: null, select: meta.ref } : { panel: null, operation: "cap" };
      case "mesh-surface":
      case "mesh-layers":
      case "mesh-quality":
      case "mesh-fidelity":
        return { panel: "mesh" };
      // The outline's four Solve page rows share one row kind; the row's id says which section it opens.
      // Initialisation opens the initial-turbulence fields (a laminar run has none, and the page then
      // falls back to the Model section).
      case "solver-settings":
        if (node.id.endsWith(":run") || node.id.endsWith(":runs")) return { panel: "solve", section: "run-monitor" };
        if (node.id.endsWith(":initialisation")) return { panel: "solve", section: INITIAL_TURBULENCE_SECTION };
        return { panel: "solve", section: node.id.endsWith(":controls") ? "solver-limits" : "solver-model" };
      case "flow-model":
        return { panel: "solve", section: "solver-model" };
      case "run":
        return { panel: "solve", section: "run-monitor" };
      case "fields":
      case "field":
        return { panel: "results", section: "results-field" };
      // Surfaces (ADR-0026 decision 3) holds the slice until the surface commands land.
      case "surfaces":
      case "slice":
        return { panel: "results", section: "results-slice" };
      case "residuals":
        return { panel: "results", section: "results-residuals" };
      case "monitors":
        return { panel: "results", section: "results-monitors" };
      case "result-vectors":
        return { panel: "results", section: "results-arrows" };
      case "result-streamlines":
        return { panel: "results", section: "results-streamlines" };
      case "result-probe":
        return { panel: "results", section: "results-probe" };
      case "result-report":
        return { panel: "results", section: "results-report" };
      case "result-lod":
        return { panel: "results", section: "results-lod" };
    }
  }
  switch (node.type) {
    case "mesh_settings":
    case "mesh_result":
      return { panel: "mesh" };
    case "solver_settings":
      return { panel: "solve", section: "solver-model" };
    case "region":
    case "boundary":
      return { panel: "conditions" };
    default:
      return null;
  }
}

/**
 * What Enter opens. The rows that have no Properties of their own (every decorated row) and the mesh and
 * solver settings open their panel; a region or a boundary keeps Enter for its Properties fields, and
 * opens the Conditions panel on a double click instead.
 */
export function enterTargetOf(node: NodeSummary): OpenTarget | null {
  if (rowMetaOf(node) !== null || node.type === "mesh_settings" || node.type === "mesh_result" || node.type === "solver_settings") return openTargetOf(node);
  return null;
}

// ---------------------------------------------------------------------------------------------- building rows
function virtualNode(id: string, name: string, type: string, path: string, status: NodeStatus, meta: RowMeta, children?: NodeSummary[]): NodeSummary {
  return {
    id: `${VIRTUAL_PREFIX}${id}`,
    type,
    name,
    status,
    params: {},
    meta: { ...meta },
    path,
    children_count: children?.length ?? 0,
    // Virtual rows cannot be renamed or deleted: the menu offers neither.
    structural: true,
    ...(children !== undefined ? { children } : {}),
  };
}

/** A copy of `node` with a subtitle (and, when asked, a status), leaving the server's own fields alone. */
function withRow(node: NodeSummary, subtitle: string, status?: NodeStatus): NodeSummary {
  return { ...node, status: status ?? node.status, meta: { ...(node.meta ?? {}), subtitle } };
}

function withChildren(node: NodeSummary, children: NodeSummary[]): NodeSummary {
  return { ...node, children, children_count: children.length };
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** `inlet`, `outlet`, `wall` for those families, else the kind's own lower-case label ("symmetry", "cyclic"). */
function shortKind(kind: ConditionKind): string {
  return kind.family === "inlet" || kind.family === "outlet" || kind.family === "wall" ? kind.family : kind.label.toLowerCase();
}

/** A parameter that has no usable value: unset, empty, or zero where its rule says it must not be. */
function isMissing(raw: unknown, rule: string): boolean {
  if (raw === undefined || raw === null || raw === "") return true;
  const value = isRecord(raw) && typeof raw.value === "number" ? raw.value : typeof raw === "number" ? raw : null;
  return value !== null && value === 0 && (rule.includes("positive") || rule.includes("nonzero"));
}

export interface BoundaryRow {
  subtitle: string;
  /** The value the kind needs is missing: the row's dot says so. */
  missing: boolean;
}

/** "inlet · 0.1 m/s": the kind and the primary value of a boundary node, or what it still needs. */
export function boundaryRow(node: NodeSummary, kinds: readonly ConditionKind[]): BoundaryRow {
  const id = node.params.kind;
  const kind = typeof id === "string" ? kinds.find((k) => k.id === id) : undefined;
  if (kind === undefined) return { subtitle: typeof id === "string" ? id.replace(/_/g, " ") : "", missing: false };
  const used = inUseParams(kind, node.params);
  const needed = used.find((p) => p.required);
  if (needed !== undefined && isMissing(node.params[needed.name], needed.rule)) {
    const what = (needed.schema.title ?? needed.name).toLowerCase();
    return { subtitle: `${shortKind(kind)} · set the ${what}`, missing: true };
  }
  const pick = needed ?? used.find((p) => p.schema["x-kind"] === "quantity") ?? used.find((p) => p.schema.enum !== undefined);
  if (pick === undefined) return { subtitle: shortKind(kind), missing: false };
  const shown = node.displays?.[pick.name];
  const raw = node.params[pick.name];
  const dflt = pick.schema.default;
  const value = typeof shown === "string" && shown !== "" ? shown : typeof raw === "string" ? raw : typeof raw === "number" ? formatTick(raw) : typeof dflt === "string" ? dflt : "";
  return { subtitle: value === "" ? shortKind(kind) : `${shortKind(kind)} · ${value}`, missing: false };
}

/** The status a set-up row shows: the server's, then what `setup.check` says about this node (an error reads as failed). */
function setupStatus(node: NodeSummary, setup: SetupCheck | null, missing: boolean): NodeStatus {
  let status = node.status;
  const found = setup?.findings.filter((f) => f.nodes?.includes(node.id)) ?? [];
  if (found.some((f) => f.severity === "error")) status = "failed";
  else if (status === "ok" && (found.some((f) => f.severity === "warning") || missing)) status = "warning";
  return status;
}

function nodeById(root: NodeSummary | null, id: string): NodeSummary | null {
  if (root === null) return null;
  if (root.id === id) return root;
  for (const child of root.children ?? []) {
    const hit = nodeById(child, id);
    if (hit !== null) return hit;
  }
  return null;
}

/** A region with its material and its boundaries; the boundaries below it get theirs. */
function regionRow(region: NodeSummary, root: NodeSummary, ctx: TreeContext): NodeSummary {
  const group = region.children?.find((c) => c.name === "boundaries" || c.path.endsWith("/boundaries"));
  let boundaries = group?.children ?? [];
  const decorated = boundaries.map((b) => {
    if (b.type !== "boundary") return b;
    const row = boundaryRow(b, ctx.kinds);
    return withRow(b, row.subtitle, setupStatus(b, ctx.setup, row.missing));
  });
  boundaries = decorated;
  const materialId = region.params.material;
  const material = typeof materialId === "string" ? nodeById(root, materialId) : null;
  const text = `${material?.name ?? "no material"} · ${plural(boundaries.length, "boundary", "boundaries")}`;
  const kids = (region.children ?? []).map((c) => (group !== undefined && c === group ? withChildren(c, boundaries) : c));
  const status = material === null && region.status === "ok" ? "warning" : region.status;
  return withChildren(withRow(region, text, setupStatus({ ...region, status }, ctx.setup, false)), kids);
}

// ---------------------------------------------------------------------------------------------- the stages
/** Every node of `type` under `group` in the tree (the operations live under /geometry/operations). */
function nodesOfType(root: NodeSummary, group: string, type: string): NodeSummary[] {
  const out: NodeSummary[] = [];
  const walk = (node: NodeSummary) => {
    if (node.type === type && (node.path === group || node.path.startsWith(`${group}/`))) out.push(node);
    for (const child of node.children ?? []) walk(child);
  };
  walk(root);
  return out;
}

const STATUS_RANK: Record<string, number> = { failed: 4, stale: 3, warning: 2, new: 1, ok: 0 };

/**
 * The Capping row (ADR-0026 decision 2): Fluent's "Enclose fluid regions" step, shown under Mesh. It is
 * the `cap` geometry operation, so the row reads the cap nodes the server already has and adds no
 * command: the worst of their statuses, how many there are, and the first one's id in `ref`.
 */
export function cappingRow(root: NodeSummary): NodeSummary {
  const caps = nodesOfType(root, "/geometry", "cap");
  if (caps.length === 0) {
    return virtualNode("mesh/capping", "Capping", "mesh-capping", "/mesh/capping", "new", { row: "mesh-capping", placeholder: true, subtitle: "no openings closed yet" });
  }
  const status = caps.map((c) => c.status).reduce((a, b) => ((STATUS_RANK[b] ?? 0) > (STATUS_RANK[a] ?? 0) ? b : a));
  const word = status === "failed" ? " · failed" : status === "stale" ? " · stale" : status === "new" ? " · not run" : "";
  return virtualNode("mesh/capping", "Capping", "mesh-capping", "/mesh/capping", status, { row: "mesh-capping", placeholder: false, subtitle: `${plural(caps.length, "cap operation")}${word}`, ref: caps[0]?.id });
}

function meshGroup(group: NodeSummary, root: NodeSummary, ctx: TreeContext): NodeSummary {
  const { settings, result } = findMeshNodes({ children: [group] } as NodeSummary);
  const kids = [...(group.children ?? [])];
  const out: NodeSummary[] = [];
  const capping = cappingRow(root);
  if (settings === null) {
    out.push(virtualNode("mesh-settings", "Mesh settings", "mesh_settings", "/mesh/settings", "new", { row: "mesh-settings", placeholder: true, subtitle: "not set yet" }));
    out.push(capping);
  }
  for (const kid of kids) {
    // Capping follows the sizing, as Fluent's watertight workflow orders them.
    if (settings !== null && kid === settings) {
      out.push(kid, capping);
      continue;
    }
    if (kid.path === "/mesh/results" && result !== null) {
      // One result per mesher is kept (ADR-0034); with more than one, the row says which is in use.
      const several = (kid.children ?? []).filter((r) => r.type === "mesh_result").length > 1;
      out.push(
        withChildren(
          kid,
          (kid.children ?? []).map((r) => {
            if (r.type !== "mesh_result") return r;
            const view = readResult(r);
            if (view === null || view.phase !== "done") return r;
            // The one-line status: the cells, then whether the mesh is current and meets the project's limits.
            const verdict = isStale(view) ? "stale" : view.ok ? "ok" : view.hasQuality ? "quality not met" : "";
            const inUse = several && r.id === result.id ? " · in use" : "";
            return withRow(r, `${formatCount(view.cells)} cells${verdict !== "" ? ` · ${verdict}` : ""}${inUse}`);
          }),
        ),
      );
    } else out.push(kid);
  }
  // Settings found below a sub-group rather than at /mesh/settings: the row still shows, first.
  if (!out.includes(capping)) out.unshift(capping);
  if (result === null) {
    out.push(virtualNode("mesh-result", "Mesh result", "mesh_result", "/mesh/results", "new", { row: "mesh-result", placeholder: true, subtitle: "not meshed" }));
  }
  // **The guided-meshing tasks' rows** (brief M-502 §UI 4): the tasks without a node of their own,
  // under the Mesh section. The value line is the same one the stepper draws (the first todo, else
  // the task's summary), from the same reply, so the two views cannot disagree. The tasks that ARE
  // nodes are not duplicated here: `sizing` is the settings row above, `volume` the result row, and
  // `geometry`/`bodies` live in Geometry ▸ Bodies where the bodies already are.
  const tasks = ctx.tasks ?? null;
  if (tasks !== null) {
    const lines = new Map<string, string>(
      tasks.tasks.map((task: { id: string; todo: string[]; row?: string }) => [
        task.id,
        task.todo[0] ?? task.row ?? "",
      ]),
    );
    const states = new Map<string, string>(
      tasks.tasks.map((task: { id: string; state: string }) => [task.id, task.state]),
    );
    const status = (state: string): NodeStatus =>
      state === "todo" ? "warning" : state === "stale" ? "stale" : state === "failed" ? "failed" : state === "running" ? "new" : "ok";
    const taskRows: Array<[string, string, RowKind]> = [
      ["boundaries", "Boundaries", "mesh-boundaries"],
      ["surface", "Surface", "mesh-surface"],
      ["layers", "Boundary layers", "mesh-layers"],
      ["quality", "Quality", "mesh-quality"],
      ["fidelity", "Fidelity to CAD", "mesh-fidelity"],
    ];
    for (const [id, name, kind] of taskRows) {
      out.push(
        virtualNode(`mesh/${id}`, name, kind, `/mesh/${id}`, status(states.get(id) ?? "todo"), {
          row: kind,
          placeholder: false,
          subtitle: lines.get(id) ?? "",
        }),
      );
    }
  }
  // **The Mesh section header carries where the user is**: "Step N of 9 · <title>", by the same
  // current rule the stepper uses (the reply's own current — the tree cannot see the selection).
  const header =
    tasks === null
      ? ""
      : (() => {
          const index = tasks.tasks.findIndex((task: { id: string }) => task.id === tasks.current);
          const title = index >= 0 ? (tasks.tasks[index]?.title ?? "") : "";
          return index >= 0 ? `Step ${index + 1} of ${tasks.tasks.length} · ${title}` : "";
        })();
  const decorated = withChildren(group, out);
  return header !== "" ? withRow(decorated, header) : decorated;
}

/** "500 it · 1e-4 · 1 core · local": the settings a run starts from. */
export function solverSubtitle(settings: NodeSummary): string {
  const p = settings.params as Record<string, unknown>;
  const parts: string[] = [];
  if (typeof p.iterations === "number") parts.push(`${formatCount(p.iterations)} it`);
  if (typeof p.residual_target === "number" && p.residual_target > 0) parts.push(formatResidual(p.residual_target));
  if (typeof p.cores === "number") parts.push(plural(p.cores, "core"));
  if (typeof p.host === "string" && p.host !== "") parts.push(p.host);
  return parts.join(" · ");
}

function turbulenceOf(settings: NodeSummary | null): string {
  const t = (settings?.params as Record<string, unknown> | undefined)?.turbulence;
  return typeof t === "string" && t !== "" ? t : "laminar";
}

function physicsGroup(group: NodeSummary, settings: NodeSummary | null): NodeSummary {
  const flow = virtualNode("flow-model", "Flow model", "solver_settings", "/solver/settings", settings === null ? "new" : settings.status, {
    row: "flow-model",
    placeholder: settings === null,
    subtitle: settings === null ? "laminar, default" : turbulenceOf(settings),
  });
  return withChildren(group, [flow, ...(group.children ?? [])]);
}

function regionsGroup(group: NodeSummary, root: NodeSummary, ctx: TreeContext): NodeSummary {
  return withChildren(group, (group.children ?? []).map((c) => (c.type === "region" ? regionRow(c, root, ctx) : c)));
}

function solverGroup(group: NodeSummary): NodeSummary {
  const settings = findSolverSettings({ children: [group] } as NodeSummary);
  if (settings !== null) return withChildren(group, (group.children ?? []).map((c) => (c === settings ? withRow(c, solverSubtitle(c)) : c)));
  const row = virtualNode("solver-settings", "Solver settings", "solver_settings", "/solver/settings", "new", { row: "solver-settings", placeholder: true, subtitle: "not set yet" });
  return withChildren(group, [row, ...(group.children ?? [])]);
}

const RUN_WORDS: Record<string, string> = {
  queued: "queued",
  running: "running",
  cancelling: "stopping",
  done: "converged",
  done_unconverged: "not converged",
  failed: "failed",
  cancelled: "cancelled",
  interrupted: "interrupted",
  skipped: "skipped",
};

const RUN_STATUS: Record<string, NodeStatus> = { done: "ok", done_unconverged: "warning", failed: "failed", interrupted: "warning", skipped: "warning" };

/** "converged · 52 it": the state of a run record and how far it went; "stale" is added when the case changed since. */
export function runSubtitle(run: RunSummary): string {
  const state = RUN_WORDS[run.state] ?? run.state;
  const it = run.iterations > 0 ? ` · ${run.iterations} it` : "";
  return `${state}${it}${run.stale ? " · stale" : ""}`;
}

function runsGroup(group: NodeSummary, ctx: TreeContext): NodeSummary {
  if (ctx.runs.length === 0) return group;
  const rows = ctx.runs.map((r) =>
    virtualNode(`run:${r.id}`, r.name !== "" ? r.name : r.id, "run", `/runs/${r.id}`, r.stale ? "stale" : (RUN_STATUS[r.state] ?? "new"), { row: "run", placeholder: false, subtitle: runSubtitle(r), ref: r.id }),
  );
  return withChildren(group, [...(group.children ?? []), ...rows]);
}

/** A field's range as the tree says it: "0 to 0.158 m/s". */
function rangeText(min: number, max: number, units: string): string {
  return `${formatTick(min)} to ${formatTick(max)}${units !== "" ? ` ${units}` : ""}`;
}

function scenesGroup(group: NodeSummary, ctx: TreeContext): NodeSummary {
  const { data, view } = ctx.results;
  const finished = ctx.runs.find((r) => r.state === "done" || r.state === "done_unconverged") ?? null;
  const latest = ctx.runs[0] ?? null;
  const none = latest === null ? "no run yet" : "no results yet";

  // Fields: one row per scalar field once the run's results are read, each with its range.
  const fieldKids = (data?.fields ?? [])
    .filter((f) => f.name !== "U")
    .map((f) =>
      virtualNode(`field:${f.name}`, fieldLabel(f.name, fieldUnits(data, f.name)), "field", `/scenes/fields/${f.name}`, "ok", { row: "field", placeholder: false, subtitle: rangeText(f.min, f.max, fieldUnits(data, f.name)), ref: f.name }),
    );
  const fields = virtualNode("fields", "Fields", "fields", "/scenes/fields", data !== null ? "ok" : "new", { row: "fields", placeholder: data === null, subtitle: data !== null ? fieldKids.map((k) => k.name).join(", ") : finished !== null ? "press Show results" : none }, fieldKids.length > 0 ? fieldKids : undefined);

  const slice = virtualNode("slice", "Slice", "slice", "/scenes/surfaces/slice", data?.slice === true ? "ok" : "new", { row: "slice", placeholder: data === null, subtitle: data === null ? none : data.slice ? (view.slice ? "shown" : "available") : "none for this run" });
  // **Surfaces** (ADR-0026 decision 3): Fluent's Results ▸ Surfaces. The slice is the one surface the
  // API cuts today (`slice.cut` through the run store); the plane clip and the iso-surface join it
  // when their `run.*` commands land. The group is never empty, so it is always shown.
  const surfaces = virtualNode("surfaces", "Surfaces", "surfaces", "/scenes/surfaces", slice.status, { row: "surfaces", placeholder: data === null, subtitle: data === null ? none : data.slice ? "slice" : "none for this run" }, [slice]);

  const last = latest?.last ?? [];
  const residualText = latest === null ? "no run yet" : last.length > 0 ? `${last.map((l) => `${l.name} ${formatResidual(l.value)}`).join(" ")} · ${latest.iterations} it` : latest.iterations > 0 ? `${latest.iterations} it` : "no residuals yet";
  const residuals = virtualNode("residuals", "Residuals", "residuals", "/scenes/residuals", latest === null ? "new" : "ok", { row: "residuals", placeholder: latest === null, subtitle: residualText });

  const monitors = monitorsOf(latest);
  const monitorPatches = new Set(monitors.map((m) => m.patch));
  const monitorsRow = virtualNode("monitors", "Monitors", "monitors", "/scenes/monitors", monitors.length > 0 ? "ok" : "new", {
    row: "monitors",
    placeholder: latest === null,
    subtitle: latest === null ? "no run yet" : monitors.length > 0 ? plural(monitorPatches.size, "patch", "patches") : "none reported",
  });

  return withChildren(group, [...(group.children ?? []), fields, surfaces, residuals, monitorsRow]);
}

/**
 * The tree with every stage's rows in place. The server's nodes that need nothing are the same objects
 * as before, so the tree's row memo keeps working for them; only the groups that gain rows are copied.
 */
export function decorateTree(root: NodeSummary | null, ctx: TreeContext): NodeSummary | null {
  if (root === null || root.children === undefined) return root;
  const settings = findSolverSettings(root);
  const kids = root.children.map((group) => {
    switch (group.path) {
      case "/mesh":
        return meshGroup(group, root, ctx);
      case "/physics":
        return physicsGroup(group, settings);
      case "/regions":
        return regionsGroup(group, root, ctx);
      case "/solver":
        return solverGroup(group);
      case "/runs":
        return runsGroup(group, ctx);
      case "/scenes":
        return scenesGroup(group, ctx);
      default:
        return group;
    }
  });
  return withChildren(root, kids);
}
