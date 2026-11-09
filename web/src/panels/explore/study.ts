/**
 * Pure helpers over the node tree for the Explore panels: which studies there are, which one the
 * selection points at, and the definition nodes of a study. The tree is the model's own summary
 * (`node.tree`), so nothing here holds state or asks the server.
 */
import type { NodeSummary, NodeTypeSchema } from "../../api/types";
import { findByPath, findNode } from "../../state/store";
import { prettyDisplay } from "../../util/units";
import { ancestorsOf } from "../nodeRules";

export const STUDIES_PATH = "/studies";
export const PARAMETERS_PATH = "/physics/parameters";

export type StudyGroup = "variables" | "responses" | "objectives" | "constraints";

/** The study nodes under `/studies`, in tree order. */
export function listStudies(tree: NodeSummary | null): NodeSummary[] {
  const group = tree?.children?.find((n) => n.path === STUDIES_PATH);
  return (group?.children ?? []).filter((n) => n.type === "study");
}

/** The nodes of one of a study's definition groups, in tree order. */
export function groupNodes(study: NodeSummary, group: StudyGroup): NodeSummary[] {
  return study.children?.find((g) => g.name === group)?.children ?? [];
}

/** The study a node is, or lives inside; null for a node outside every study. */
export function studyContaining(tree: NodeSummary | null, id: string | null): NodeSummary | null {
  const node = findNode(tree, id);
  if (!node) return null;
  if (node.type === "study") return node;
  const chain = ancestorsOf(tree, node.id) ?? [];
  return [...chain].reverse().find((n) => n.type === "study") ?? null;
}

/**
 * The study the Explore panels show: the selected one, or the one that holds the selected node. When the
 * selection is somewhere else (a parameter, a patch) the last study shown stays, as long as it exists;
 * otherwise the first study. Null when the project has none.
 */
export function chooseStudy(tree: NodeSummary | null, selectedId: string | null, remembered: string | null): NodeSummary | null {
  const studies = listStudies(tree);
  if (studies.length === 0) return null;
  const selected = studyContaining(tree, selectedId);
  if (selected) return selected;
  return studies.find((s) => s.id === remembered) ?? studies[0];
}

/** The name a variable's parameter reference shows: `/physics/parameters/U_in` is `U_in`. */
export function shortParameter(reference: string): string {
  return reference.replace(/^\/?physics\/parameters\//, "").replace(/^\//, "");
}

/** Category labels by SI value for the categorical variables of a study, keyed by variable name. */
export function categoryLabels(study: NodeSummary): Record<string, Map<number, string>> {
  const out: Record<string, Map<number, string>> = {};
  for (const variable of groupNodes(study, "variables")) {
    const p = variable.params as Record<string, unknown>;
    if (p.domain !== "categorical" || !Array.isArray(p.values)) continue;
    const labels = typeof p.labels === "string" ? p.labels.split(",").map((s) => s.trim()) : [];
    const map = new Map<number, string>();
    p.values.forEach((value, i) => {
      if (typeof value === "number" && labels[i]) map.set(value, labels[i]);
    });
    if (map.size > 0) out[variable.name] = map;
  }
  return out;
}

/** A quantity a study can vary: a named parameter, or the quantity of a node. */
export interface Target {
  /** What `study.add_variable` takes as `parameter`. */
  value: string;
  group: "parameter" | "quantity";
  dimension: string;
  /** Its current value, in display units. */
  shown: string;
}

function dimensionOf(node: NodeSummary, schema: NodeTypeSchema["properties"][string]): string {
  const declared = schema["x-dimension"] ?? "dimensionless";
  return declared === "dynamic" ? String((node.params as Record<string, unknown>).dimension ?? "dimensionless") : declared;
}

/** The named parameters, then every other node's quantity parameters by path (`/regions/fluid/inlet.velocity`). */
export function targetsOf(tree: NodeSummary | null, types: Record<string, NodeTypeSchema>): Target[] {
  const out: Target[] = [];
  for (const node of findByPath(tree, PARAMETERS_PATH)?.children ?? []) {
    if (node.type !== "parameter") continue;
    out.push({ value: node.name, group: "parameter", dimension: String((node.params as Record<string, unknown>).dimension ?? "dimensionless"), shown: prettyDisplay(node.displays?.value ?? "") });
  }
  const visit = (node: NodeSummary) => {
    if (node.path === "/studies" || node.path === PARAMETERS_PATH) return;
    for (const [name, schema] of Object.entries(types[node.type]?.properties ?? {})) {
      if (schema["x-kind"] !== "quantity") continue;
      out.push({ value: `${node.path}.${name}`, group: "quantity", dimension: dimensionOf(node, schema), shown: prettyDisplay(node.displays?.[name] ?? "") });
    }
    for (const child of node.children ?? []) visit(child);
  };
  for (const top of tree?.children ?? []) visit(top);
  return out;
}
