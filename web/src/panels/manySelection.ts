/**
 * What a multi-selection can be told, and what it can be told TOGETHER.
 *
 * **The brief's rule is "no entry that will be refused"** (claude-win2's 1400 file): a bulk action is
 * offered only when it is valid for EVERY chosen row, and greyed out with the reason otherwise. These are
 * the two questions the pane asks, answered as pure functions so the pane has no judgement of its own.
 */
export type BulkAction = "show" | "hide" | "simulate" | "colour" | "delete";

export interface ChosenRow {
  id: string;
  type: string;
}

/** The actions valid for a selection of these types, in the order the pane shows them. */
export function bulkActionsFor(types: readonly string[]): BulkAction[] {
  // **Nothing chosen is nothing to act on.** An empty set makes `every` vacuously true, so without this
  // guard it would offer every action for a selection that does not exist.
  if (types.length === 0) return [];
  const all = (t: string) => types.every((x) => x === t);
  const actions: BulkAction[] = [];
  // Hide and show apply to anything drawable.
  actions.push("show", "hide");
  // **"In simulation" is a part's own state**, so it needs every row to be a part.
  if (all("part")) actions.push("simulate");
  // Colour and opacity are a part's or a tag's (ADR-0021 4.3) - a boundary has no swatch.
  if (types.every((t) => t === "part" || t === "tag")) actions.push("colour");
  actions.push("delete");
  return actions;
}

/**
 * The heading: "3 parts" for one type, "3 items" for several.
 *
 * **The type is named because it is the useful fact**: three parts can be edited together; a part and a
 * tag cannot, which is the next line of the page.
 */
export function manyTitle(types: readonly string[], count: number): string {
  const noun = count === 1 ? "item" : "items";
  if (types.length === 0) return `${count} ${noun}`;
  const first = types[0]!;
  const oneType = types.every((t) => t === first);
  if (!oneType) return `${count} ${noun}`;
  const plural = `${first}s`;
  return `${count} ${plural}`;
}

/** True when every row is the same type, which is the case where properties can be edited together. */
export function sameType(types: readonly string[]): boolean {
  return types.length > 0 && types.every((t) => t === types[0]);
}

/**
 * Whether a parameter's values AGREE across the chosen rows, and the value to show.
 *
 * **"Mixed" is the brief's word** (claude-win2's 1400 file: *"a field whose values differ shows 'mixed'
 * (empty input with a 'mixed' placeholder)"*). An empty box would read as "not set"; "mixed" says the
 * rows disagree, which is a different fact and the one a user needs before editing.
 */
export interface FieldValues {
  mixed: boolean;
  /** The shared value when they agree, otherwise null. */
  value: string;
}

/** Compare the chosen rows' values for one parameter. Values arrive already formatted as text. */
export function fieldValues(values: readonly string[]): FieldValues {
  // **No rows is not "mixed"** - there is nothing to disagree. The page is not shown then anyway.
  if (values.length === 0) return { mixed: false, value: "" };
  const first = values[0]!;
  const same = values.every((v) => v === first);
  return same ? { mixed: false, value: first } : { mixed: true, value: "" };
}

/**
 * The parameters worth editing on a set of rows: the ones EVERY row has.
 *
 * **A parameter that only some rows have would be a field that silently skips the rest**, which is the
 * "no entry that will be refused" rule again, applied to a property instead of an action.
 */
export function editableParameters(parameterNames: readonly (readonly string[])[]): string[] {
  if (parameterNames.length === 0) return [];
  const [first, ...rest] = parameterNames;
  return (first ?? []).filter((name) => rest.every((names) => names.includes(name)));
}

/**
 * The command and parameters a bulk action runs, or null when the selection cannot take it.
 *
 * **One function, so the page has no command knowledge of its own** and the four existing commands are
 * used exactly as they are - `geometry.style` and `geometry.simulate` already take a LIST of nodes, which
 * is why no new verb was needed for these three.
 *
 * `delete` is deliberately absent: it needs a confirmation with the count first, so it is the page's job
 * rather than a command shape.
 */
export interface BulkCall {
  command: "geometry.style" | "geometry.simulate";
  params: Record<string, unknown>;
}

export function bulkCall(action: BulkAction, nodes: readonly string[], current: { visible: boolean }): BulkCall | null {
  if (nodes.length === 0) return null;
  const list = [...nodes];
  switch (action) {
    case "show":
      return { command: "geometry.style", params: { nodes: list, visible: true } };
    case "hide":
      return { command: "geometry.style", params: { nodes: list, visible: false } };
    case "simulate":
      return { command: "geometry.simulate", params: { nodes: list, simulate: true } };
    case "colour":
      // The colour and opacity fields write their own values; this is the "turn it back on" path.
      return { command: "geometry.style", params: { nodes: list, visible: current.visible } };
    case "delete":
      // Handled by the page, which asks first.
      return null;
    default:
      return null;
  }
}
