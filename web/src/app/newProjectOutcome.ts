/**
 * What the New project dialog does once `project.new` has answered.
 *
 * **Why this is a pure helper.** The rule "close on success, stay open on failure" lives in
 * `ProjectMenu.tsx`'s `create`, which is React-bound and needs a DOM to exercise. Pulling the
 * decision out lets a plain unit test pin it without adding a rendering dependency. The side
 * effects (switching project, clearing the selection) stay in `create`.
 */
export function afterCreate(ok: boolean): { closeDialog: boolean } {
  return { closeDialog: ok };
}
