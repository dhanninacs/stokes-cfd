/**
 * When the build ribbon folds away by itself: on leaving the Geometry stage for a stage that has its own tools
 * (Physics for the fluid and the boundaries, Mesh, Solve, Results, Explore), where the modelling tools compete with
 * the face pick and the stage's cards for the bottom of the view (Windows walkthrough, 2026-11-13). Only a
 * change of stage closes it: the Create button opens it again in any stage, and it stays open until the next
 * change. Pure: the tests run in Node.
 */

/** True when a change of stage from `previous` to `next` should close an open ribbon. */
export function ribbonClosesOnStage(previous: string, next: string): boolean {
  return previous !== next && next !== "Geometry";
}
