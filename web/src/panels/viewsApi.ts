/**
 * The wire contract of the saved views (R4), in ONE module: the four commands and the defensive readers of their
 * replies (`stokes/api/commands/view.py`). When the server's contract changes this is the only file that has to.
 *
 * * `view.save {name, record}` adds the view, or replaces the one of that name; `{id, name, created, replaced}`;
 * * `view.list` is `{views: [{id, name, created}]}` in the order the views were first saved;
 * * `view.get {view}` is the record, as saved; `view` is an id, a tree path or a name;
 * * `view.remove {view}` is `{id, name}`.
 *
 * A view is journaled and undoable like any project write (the server's `view.save` is one undo step), so the
 * front end never keeps its own copy of the list past a refresh.
 *
 * Import rule: no React, no store, no DOM. The caller hands in `execute` (the store's), so this loads in Node and
 * the tests give it a fake. Everything read from the wire is data: a missing or mistyped field gives a neutral
 * value, never an exception.
 */

export const VIEW_COMMANDS = {
  save: "view.save",
  list: "view.list",
  get: "view.get",
  remove: "view.remove",
} as const;

/** The store's `execute`, as far as this module uses it. */
export type Execute = (method: string, params?: Record<string, unknown>, options?: { silent?: boolean }) => Promise<unknown>;

/** One row of `view.list`. */
export interface ViewSummary {
  id: string;
  name: string;
  /** ISO 8601 (UTC), or "". */
  created: string;
}

export interface SaveReply {
  id: string;
  name: string;
  created: string;
  replaced: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** The rows of a `view.list` reply; a row without an id is dropped (it could not be applied or removed). */
export function readViewList(raw: unknown): ViewSummary[] {
  const rows = isRecord(raw) && Array.isArray(raw.views) ? raw.views : [];
  const out: ViewSummary[] = [];
  for (const row of rows) {
    if (!isRecord(row) || text(row.id) === "") continue;
    out.push({ id: text(row.id), name: text(row.name), created: text(row.created) });
  }
  return out;
}

/** The reply of `view.save`, or null when it is not understood. */
export function readSaveReply(raw: unknown): SaveReply | null {
  if (!isRecord(raw) || text(raw.id) === "") return null;
  return { id: text(raw.id), name: text(raw.name), created: text(raw.created), replaced: raw.replaced === true };
}

/** Save `record` under `name` (the server trims the name and checks it). Resolves to the reply; rejects with the server's refusal. */
export async function saveView(execute: Execute, name: string, record: unknown): Promise<SaveReply> {
  const reply = readSaveReply(await execute(VIEW_COMMANDS.save, { name, record }));
  if (reply === null) throw new Error("The server's answer about the saved view was not understood.");
  return reply;
}

export async function listViews(execute: Execute): Promise<ViewSummary[]> {
  return readViewList(await execute(VIEW_COMMANDS.list, {}, { silent: true }));
}

/** A view's record, or null when the server sent something that is not an object. */
export async function getView(execute: Execute, view: string): Promise<Record<string, unknown> | null> {
  const raw = await execute(VIEW_COMMANDS.get, { view }, { silent: true });
  return isRecord(raw) ? raw : null;
}

export async function removeView(execute: Execute, view: string): Promise<void> {
  await execute(VIEW_COMMANDS.remove, { view });
}

/**
 * Take back the removal of a view, only while it is still the newest step of the undo stack (`history.undo` steps back
 * whatever is newest, and a removal that other changes have buried is not to be undone by a toast). Resolves to whether
 * the view came back. A removal is the server's `remove_node` step (`view.remove` pushes a `RemoveNode`).
 */
export async function undoRemoveView(execute: Execute): Promise<boolean> {
  const state = (await execute("history.state", {}, { silent: true })) as { undo_kind?: unknown } | null;
  if (state === null || typeof state !== "object" || state.undo_kind !== "remove_node") return false;
  await execute("history.undo");
  return true;
}
