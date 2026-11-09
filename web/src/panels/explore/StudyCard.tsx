/**
 * The Study card of the Properties panel: what a selected `study` node is made of. Under the node's own
 * schema-driven fields it lists the variables (the parameter each varies and the values it takes), the
 * responses, the objectives and the constraints, each with the button that adds one (`study.add_variable`,
 * `study.add_response`, `study.set_objective`, `study.add_constraint`). Definitions are nodes, so a row
 * selects its node in the tree and the panel then edits it like any other.
 *
 * PropertiesPanel calls `useStudyCard(node)` and renders what it returns; the study's own `status`
 * parameter is kept by the table commands, so the card shows it as text and the panel does not offer it
 * as a field.
 */
import type { ReactNode } from "react";
import { Gauge, Plus, Ruler, Target, Variable } from "lucide-react";
import type { NodeSummary, UnitsSchema } from "../../api/types";
import { Button } from "../../components/ui";
import { Tooltip } from "../../components/Tooltip";
import { findNode, useStore } from "../../state/store";
import { prettyDisplay } from "../../util/units";
import { openAddConstraint, openAddResponse, openAddVariable, openSetObjective } from "./forms";
import { converterFor, displayUnitOf, formatCell, plural, statusText } from "./format";
import { useExplore, useExploreFeed } from "./store";
import { groupNodes, type StudyGroup } from "./study";

/** The study parameters PropertiesPanel leaves out of its fields: the card shows them. */
const HIDDEN: ReadonlySet<string> = new Set(["status"]);
const NOTHING: { card: ReactNode; hidden: ReadonlySet<string> } = { card: null, hidden: new Set() };

/** The card for a selected study node, and the parameters the panel should not offer as fields. */
export function useStudyCard(node: NodeSummary | null): { card: ReactNode; hidden: ReadonlySet<string> } {
  if (node?.type !== "study") return NOTHING;
  return { card: <StudyCard key={node.id} study={node} />, hidden: HIDDEN };
}

// ---------------------------------------------------------------------------------------------- summaries
/** "0.5 m/s" as its number and its unit. */
function splitDisplay(text: string): [string, string] {
  const match = /^(\S+)\s+(.+)$/.exec(prettyDisplay(text).trim());
  return match ? [match[1], match[2]] : [text, ""];
}

const LISTED = 5;

/** What a variable takes, in one line, in the project's display unit: "1, 2, 3 m/s", "0.5 to 5 m/s · 10 levels". */
export function domainSummary(variable: NodeSummary, units: UnitsSchema | null, system: string): string {
  const p = variable.params as Record<string, unknown>;
  const shown = variable.displays ?? {};
  const dimension = String(p.dimension ?? "dimensionless");
  const si = units?.dimensions[dimension]?.si_unit ?? "";
  const conversion = converterFor(si, displayUnitOf(units, system, dimension));
  const number = (key: string) => splitDisplay(shown[key] ?? "")[0];
  const unit = splitDisplay(shown.min ?? shown.mean ?? shown.max ?? "")[1] || conversion.unit;
  const withUnit = (text: string) => (unit ? `${text} ${unit}` : text);
  const levels = typeof p.count === "number" && p.count > 0 ? ` · ${plural(p.count, "level")}` : "";
  switch (p.domain) {
    case "range": {
      const step = number("step");
      const by = levels || (step && step !== "0" ? ` · step ${step}` : "");
      return `${number("min")} to ${withUnit(number("max"))}${by}`;
    }
    case "uniform":
      return `uniform ${number("min")} to ${withUnit(number("max"))}${levels}`;
    case "normal":
      return `normal, mean ${number("mean")}, std ${withUnit(number("std"))}${levels}`;
    default: {
      const values = Array.isArray(p.values) ? p.values.filter((v): v is number => typeof v === "number") : [];
      const labels = typeof p.labels === "string" && p.labels.trim() ? p.labels.split(",").map((s) => s.trim()) : [];
      if (values.length === 0) return "no values yet";
      const first = values.slice(0, LISTED).map((v, i) => labels[i] || formatCell(conversion.toDisplay(v)));
      const more = values.length > LISTED ? `, … (${values.length})` : "";
      return p.domain === "categorical" ? `${first.join(", ")}${more}` : withUnit(`${first.join(", ")}${more}`);
    }
  }
}

function constraintSummary(c: NodeSummary): string {
  const p = c.params as Record<string, unknown>;
  const limit = c.displays?.limit ? prettyDisplay(c.displays.limit) : "";
  const op = p.operator === "<=" ? "≤" : p.operator === ">=" ? "≥" : "=";
  return `${op} ${limit}`.trim();
}

// ---------------------------------------------------------------------------------------------- card
function Row({ node, icon, title, aside, sub, mono }: { node: NodeSummary; icon: ReactNode; title: string; aside?: string; sub?: string; mono?: boolean }) {
  const select = useStore((s) => s.select);
  const selected = useStore((s) => s.selectedId === node.id);
  return (
    <Tooltip label={[title, aside, sub].filter(Boolean).join("  ·  ")} side="top" describe={false}>
      <button type="button" className="sc-row no-drag" aria-current={selected || undefined} onClick={() => select(node.id)}>
        <span className="sc-icon" aria-hidden="true">
          {icon}
        </span>
        <span className="sc-text">
          <span className="sc-line">
            <span className={`sc-title ${mono ? "mono" : ""}`}>{title}</span>
            {aside && <span className="sc-aside muted">{aside}</span>}
          </span>
          {sub && <span className="sc-sub mono faint">{sub}</span>}
        </span>
      </button>
    </Tooltip>
  );
}

function Section({ title, count, add, onAdd, empty, children }: { title: string; count: number; add: string; onAdd: () => void; empty: string; children: ReactNode }) {
  return (
    <section className="sc-sec" aria-label={title}>
      <div className="sc-head">
        <span className="section-title">{title}</span>
        <span className="sc-count muted">{count > 0 ? count : ""}</span>
        <Button className="ws-small sc-add" onClick={onAdd}>
          <Plus size={13} /> {add}
        </Button>
      </div>
      {count > 0 ? <div className="sc-list">{children}</div> : <p className="sc-empty muted">{empty}</p>}
    </section>
  );
}

function StudyCard({ study }: { study: NodeSummary }) {
  useExploreFeed(); // the table's counts under "Design table" stay current even when no Explore panel is on screen
  const tree = useStore((s) => s.tree);
  const units = useStore((s) => s.units);
  const system = useStore((s) => s.project?.display_units ?? "si");
  const results = useExplore((s) => (s.results?.study === study.id ? s.results : null));
  const nodes = (group: StudyGroup) => groupNodes(findNode(tree, study.id) ?? study, group);
  const variables = nodes("variables");
  const responses = nodes("responses");
  const objectives = nodes("objectives");
  const constraints = nodes("constraints");
  const status = String((study.params as Record<string, unknown>).status ?? "idle");

  return (
    <div className="sc-card" role="group" aria-label="Study">
      <div className="sc-table">
        <span className="muted">Design table</span>
        <span>{results ? `${status} · ${statusText(results.counts, results.points.length)}` : status}</span>
      </div>
      <Section title="Variables" count={variables.length} add="Add variable…" onAdd={() => openAddVariable(study.id)} empty="Nothing varies yet. Add the parameters to explore.">
        {variables.map((v) => {
          const reference = String((v.params as Record<string, unknown>).parameter ?? "");
          const path = reference.startsWith("/") ? reference : reference.includes(".") ? reference : `/physics/parameters/${reference}`;
          return <Row key={v.id} node={v} icon={<Variable size={14} />} title={v.name} aside={domainSummary(v, units, system)} sub={path} />;
        })}
      </Section>
      <Section title="Responses" count={responses.length} add="Add response…" onAdd={() => openAddResponse(study.id)} empty="Nothing is recorded yet. Add what each point should report.">
        {responses.map((r) => {
          const p = r.params as Record<string, unknown>;
          return <Row key={r.id} node={r} icon={<Gauge size={14} />} title={String(p.label || r.name)} aside={String(p.expression ?? "")} />;
        })}
      </Section>
      <Section title="Objectives" count={objectives.length} add="Set objective…" onAdd={() => openSetObjective(study.id)} empty="No objective. Set one for each response to push.">
        {objectives.map((o) => {
          const response = findNode(tree, String((o.params as Record<string, unknown>).response ?? ""));
          const direction = (o.params as Record<string, unknown>).direction === "maximise" ? "maximise" : "minimise";
          return <Row key={o.id} node={o} icon={<Target size={14} />} title={response?.name ?? o.name} aside={direction} />;
        })}
      </Section>
      <Section title="Constraints" count={constraints.length} add="Add constraint…" onAdd={() => openAddConstraint(study.id)} empty="No constraint. Add one to mark points as infeasible.">
        {constraints.map((c) => (
          <Row key={c.id} node={c} icon={<Ruler size={14} />} title={String((c.params as Record<string, unknown>).expression ?? c.name)} aside={constraintSummary(c)} mono />
        ))}
      </Section>
    </div>
  );
}
