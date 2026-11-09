/**
 * The expansion a project gets when it loads: the tree opens to the workspace's
 * `tree.defaultExpanded` depth **within the current stage**, and every other stage's top-level
 * groups fold.
 *
 * ## Why this is not `expansionFor`
 *
 * `expansionFor(tree, depth)` walks the whole tree, so at depth 2 it opens the first two levels of
 * **every** top-level group. On the demo project that opened `geometry`, `mesh`, `physics` and its
 * `parameters`, `regions`, `scenes` and its child, and `studies` and its study — nine-odd groups, of
 * which only Geometry is what the user is looking at. The launch tree was reported as "everything is
 * extended" and that is exactly what it was: the depth rule had no notion of the stage the window is
 * in, although the stage chips say the window *is* in one (Geometry is the default).
 *
 * ## Why this is not `stageExpanded` either
 *
 * `stageExpanded` is the *chip* rule and answers a different question: it opens the stage's
 * top-level groups and leaves everything below exactly as the user left it, so switching stages does
 * not throw away what they had opened. At launch there is nothing to preserve, and its `open` walk
 * stops at the first level — it opens a child only when a listed group lies *under* it, which is what
 * makes `/physics/materials` open without opening `/physics/parameters`. Run at launch it would show
 * `geometry` and `parts` folded, so the demo's own parts would be hidden behind a disclosure triangle
 * the user has to find.
 *
 * So this combines the two: `stageExpanded`'s **scope** (only the current stage; the rest fold) with
 * `expansionFor`'s **depth** (open `defaultExpanded` levels below each of the stage's top-level
 * groups). The result on the demo is `geometry`, `parts`, `demo_pipe` and `tags` open with their
 * contents visible, and every other group folded — the tree the owner asked for.
 *
 * A module of its own rather than a change to `workspace/stageMap.ts`: that file is another session's
 * (see `.coordination/SESSIONS.md`) and its two functions are both correct for their own callers. This
 * only adds the launch rule, and it reads the workspace's stage table through their exports rather than
 * keeping a copy.
 */
import { defaultLevels, stageGroups, stageOpens, under } from "../workspace/stageMap";

/** The part of a tree node the expansion needs (structural, as `stageMap`'s own interface is). */
export interface TreeNodeLike {
  id: string;
  path: string;
  children?: readonly TreeNodeLike[];
}

/**
 * The expansion for a tree that has just loaded: the current stage's top-level groups open, and
 * `depth` levels below each of them; every other top-level group folds.
 *
 * `depth` is the workspace's `tree.defaultExpanded`, read through `defaultLevels()` by the caller that
 * has no document yet. 0 folds everything (every top-level group closed, so the tree shows its nine
 * group rows and nothing else). The four "other" groups keep whatever expansion they had — which at
 * launch is none, since the map starts empty — so this folds rather than preserves.
 */
export function stageScopedExpansion(
  stage: string,
  root: TreeNodeLike | null,
  depth: number,
  expanded: Readonly<Record<string, boolean>> = {},
): Record<string, boolean> {
  if (root === null) return { ...expanded };
  const next: Record<string, boolean> = { ...expanded };
  const groups = stageGroups(stage);
  for (const top of root.children ?? []) {
    // `stageOpens` is the test `stageExpanded` uses: the stage names this group, or names a group
    // below it (`/physics/materials` opens `/physics`). The `groups.some` half catches a stage that
    // names a sub-group from the top, which `stageOpens` already covers — kept because it states the
    // rule in terms of the paths themselves rather than relying on that.
    const open = depth > 0 && (stageOpens(stage, top.path) || groups.some((g) => under(top.path, g)));
    next[top.id] = open;
    // `depth` levels open BELOW the stage group. The group itself is level 0, so `depth: 2` opens its
    // children and their children — `/geometry`, `/geometry/parts`, `/geometry/parts/demo_pipe` and
    // `/geometry/tags` on the demo, which is the tree the owner's capture shows. Reading the depth as
    // a node count from the root instead (`parts` being level 2) left `demo_pipe` folded and the
    // project's own parts hidden, which is the state the capture was complaining about.
    expandBelow(top, depth, next);
    // And everything outside the stage closes, all the way down, so a folded group is not merely
    // folded at its own row while holding dozens of open children that reappear when it is clicked.
    if (!open) closeBelow(top, next);
  }
  return next;
}

/**
 * Open `levels` levels below `node`, leaving deeper nodes as the caller had them.
 *
 * A node with no children is left alone rather than marked open: it draws no chevron, and writing
 * `true` for every leaf would put entries in `expanded` that mean nothing and inflate any count taken
 * over it (the probes used during this work count exactly that).
 */
function expandBelow(node: TreeNodeLike, levels: number, next: Record<string, boolean>): void {
  if (levels <= 0) return;
  for (const child of node.children ?? []) {
    if ((child.children ?? []).length === 0) continue;
    next[child.id] = true;
    expandBelow(child, levels - 1, next);
  }
}

/** Close every descendant of `node`. */
function closeBelow(node: TreeNodeLike, next: Record<string, boolean>): void {
  for (const child of node.children ?? []) {
    next[child.id] = false;
    closeBelow(child, next);
  }
}

/**
 * `stageScopedExpansion` at the workspace's own depth, for a caller that has no document in hand.
 *
 * The store asks for this at two moments — when the first tree of a project arrives, and after the
 * connection's refresh — and both want the currently configured depth.
 */
export function defaultStageExpansion(
  stage: string,
  root: TreeNodeLike | null,
): Record<string, boolean> {
  return stageScopedExpansion(stage, root, defaultLevels());
}
