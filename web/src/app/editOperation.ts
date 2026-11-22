/**
 * "Edit sketch" and "Edit <operation>" for a body (review finding B3, first step): the way back from
 * a body in the viewport to the operation that made it. Double-clicking a body, the top entry of its
 * context menu and the first button of its context toolbar all go through `editBody`.
 *
 * A drawn sketch opens in the sketcher; any other operation is selected, so its page opens in
 * Details with the fields that drive the body, where a change rebuilds it.
 */
import type { NodeSummary } from "../api/types";
import { useStore } from "../state/store";
import { implicitTitle } from "../state/implicitFields";
import { operationOf } from "../panels/bodySteps";
import { mapEntries } from "../panels/compoundValues";
import { isDrawnSketch } from "../sketch/drawnSketch";
import { parseStored } from "../sketch/bindings";
import { originOf } from "../direct/plane";
import type { SketchTool } from "./createEntries";

/** What editing a body opens: its operation and the words for the entry. */
export interface BodyEdit {
  op: NodeSummary;
  /** "Edit sketch", "Edit cylinder", "Edit move". */
  label: string;
  /** True when it opens the sketcher. */
  sketch: boolean;
}

/** The edit a body offers, or null for an imported body (or one whose operation is gone). */
export function bodyEdit(tree: NodeSummary | null, partId: string): BodyEdit | null {
  const op = operationOf(tree, partId);
  if (op === null) return null;
  if (isDrawnSketch(op)) return { op, label: "Edit sketch", sketch: true };
  // A named profile (a rectangle pulled from the card) is edited on its page, where its sizes are.
  if (op.type === "implicit_sketch") return { op, label: "Edit sketch", sketch: false };
  // "Edit schwarz P lattice" ran out of the card beside its own title: the pattern is on the page.
  if (op.type === "implicit_lattice") return { op, label: "Edit lattice", sketch: false };
  const title = implicitTitle(op.type, op.params as Record<string, unknown>);
  const word = title ?? op.name;
  return { op, label: `Edit ${word.charAt(0).toLowerCase()}${word.slice(1)}`, sketch: false };
}

const PLANES = new Set(["xy", "yz", "zx"]);

/** Opens what made the body: the sketcher on its drawn sketch, else the operation's page. False when there is none. */
export function editBody(partId: string): boolean {
  const s = useStore.getState();
  const edit = bodyEdit(s.tree, partId);
  if (edit === null) return false;
  if (edit.sketch) {
    const params = edit.op.params as Record<string, unknown>;
    const plane = typeof params.plane === "string" && PLANES.has(params.plane) ? (params.plane as "xy" | "yz" | "zx") : "xy";
    const { entities, bindings } = parseStored(params.entities);
    const dimensions = Object.fromEntries(mapEntries(params, undefined, "dimensions").map((e) => [e.name, e.text]));
    s.openSketch({ opId: edit.op.id, plane, entities, bindings, dimensions, tool: "select", origin: originOf(params.origin) });
    return true;
  }
  s.select(edit.op.id);
  return true;
}

/** Opens the sketcher on a new sketch, with `tool` chosen when the ribbon's Line, Rectangle… asked for one. */
export function startSketch(tool?: SketchTool): void {
  useStore.getState().openSketch({ opId: null, plane: "xy", entities: [], ...(tool === undefined ? {} : { tool }) });
}
