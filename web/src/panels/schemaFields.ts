/**
 * The pure helpers of `SchemaFields` (the Properties panel's field loop as one component,
 * T-041): which of the panel's field kinds a schema property is, and which nodes of a type the
 * tree holds. No React, no store: `fieldKindOf` reads one property's schema, `nodesOfType` walks
 * one tree.
 */
import type { NodeSummary, PropertySchema } from "../api/types";

/** The field kinds `SchemaFields` renders, one per branch of its render loop. */
export type FieldKind =
  | "quantity"
  | "enum"
  | "bool"
  | "number"
  | "int"
  | "string"
  | "ref"
  | "array3"
  | "array"
  | "json";

/** Normalises a JSON-schema `type` (a name or a list of names) to a list. */
function typeNames(schema: PropertySchema): string[] {
  const t = schema.type;
  return Array.isArray(t) ? t : typeof t === "string" ? [t] : [];
}

/**
 * Which field a property gets. `x-kind` decides first (quantity, json, ref); then `enum`,
 * which the server marks on a string type, before the bare types; an `array` whose default
 * has three numbers is `array3`, the three-number row, else the read-only `array`.
 */
export function fieldKindOf(schema: PropertySchema): FieldKind {
  const kind = schema["x-kind"];
  if (kind === "quantity" || kind === "ref") return kind;
  if (kind === "json") return "json";
  if (schema.enum && schema.enum.length > 0) return "enum";
  const types = typeNames(schema);
  if (types.includes("boolean")) return "bool";
  if (types.includes("integer")) return "int";
  if (types.includes("number")) return "number";
  if (types.includes("array")) {
    const d = schema.default;
    if (Array.isArray(d) && d.length === 3 && d.every((v) => typeof v === "number" && Number.isFinite(v))) return "array3";
    return "array";
  }
  return "string";
}

/** A node for a `ref` select: its id (what the parameter stores), its name (what the select shows) and its path (the option's tooltip). */
export interface RefNode {
  id: string;
  name: string;
  path: string;
}

/**
 * Every node of `type` in the tree, in tree order (depth first, the children's order),
 * starting at the root itself. The tree root is the project's `NodeSummary`, or null
 * before it loads; null then gives no nodes.
 */
export function nodesOfType(root: NodeSummary | null, type: string): RefNode[] {
  const out: RefNode[] = [];
  const walk = (node: NodeSummary): void => {
    if (node.type === type) out.push({ id: node.id, name: node.name, path: node.path });
    for (const child of node.children ?? []) walk(child);
  };
  if (root) walk(root);
  return out;
}

/**
 * The checks a field makes before it sends anything, as pure functions: the message a field shows when it
 * refuses the text, or null when it accepts it. The field's Enter handler asks the same function as its
 * commit does, so a field that refused its text also keeps the Enter from reaching a card around it (the
 * card would send the value from before the edit and close).
 */
export function quantityRefusal(text: string): string | null {
  return text.trim() === "" ? "Enter a value or an expression." : null;
}

/** A plain number field (`integer`: a whole number). */
export function numberRefusal(text: string, integer: boolean): string | null {
  const n = Number(text.trim());
  if (text.trim() === "" || !Number.isFinite(n)) return "Enter a number.";
  if (integer && !Number.isInteger(n)) return "Enter a whole number.";
  return null;
}

/** The three components of a vector field: every one has to be a number. */
export function vectorRefusal(texts: readonly string[]): string | null {
  return texts.some((t) => t.trim() === "" || !Number.isFinite(Number(t.trim()))) ? "Enter a number." : null;
}

/**
 * True when an Enter inside a field is for the field alone: the text is changed and the field refuses it.
 * `changed` is false for text that equals the applied value, which the field leaves alone (and a card may
 * then commit, as before).
 */
export function enterStaysInField(changed: boolean, refusal: string | null): boolean {
  return changed && refusal !== null;
}
