/**
 * The sketcher's undo and redo (review A1, A3). One history entry is the whole editable state,
 * entities with their bindings and the dimensions, so an undo can never leave a binding on an
 * entity that is gone or a drawing that disagrees with its dimensions. Every edit pushes; an
 * evaluation that only refreshes the drawn values replaces the present without an entry. Pure.
 */
import type { Bindings } from "./bindings";
import type { Entity } from "./model";

export interface Dim {
  name: string;
  text: string;
}

export interface SketchDoc {
  entities: Entity[];
  bindings: Bindings;
  dims: Dim[];
}

export interface History {
  past: SketchDoc[];
  present: SketchDoc;
  future: SketchDoc[];
}

const LIMIT = 100;

export function start(doc: SketchDoc): History {
  return { past: [], present: doc, future: [] };
}

/** An edit: the present goes onto the undo stack and the redo stack is cleared. */
export function push(h: History, next: SketchDoc): History {
  if (next === h.present) return h;
  return { past: [...h.past.slice(-(LIMIT - 1)), h.present], present: next, future: [] };
}

export function undo(h: History): History {
  if (h.past.length === 0) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}

export function redo(h: History): History {
  if (h.future.length === 0) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

/** The present with new drawn values (an evaluation, not an edit): no history entry. */
export function refresh(h: History, doc: SketchDoc): History {
  return { ...h, present: doc };
}

/**
 * Whether there is an edit to lose: Cancel then asks first (review A4). Every edit pushes, so an
 * empty undo stack is the sketch as it opened (refreshed values are not edits).
 */
export function isDirty(h: History): boolean {
  return h.past.length > 0;
}
