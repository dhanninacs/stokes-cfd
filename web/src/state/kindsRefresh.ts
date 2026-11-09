/**
 * When the condition catalogue must be asked for again.
 *
 * `boundary.kinds` says which kinds the project's engine can write (`supported`), so the answer
 * changes with the engine: when a project opens, and when the engine setting changes. Every other
 * event leaves it as it was, and a refetch per node event would be a request per keystroke.
 */

/** The solver settings node, where the engine lives. */
const SETTINGS_PATH = "/solver/settings";

export function kindsRefreshWanted(event: string, data: unknown): boolean {
  if (event === "project.opened") return true;
  if (event !== "node.changed" || typeof data !== "object" || data === null) return false;
  const { path, param } = data as { path?: unknown; param?: unknown };
  return path === SETTINGS_PATH && (param === "engine" || param === undefined);
}
