/**
 * The `tree` section of the workspace as the project store (state/store.ts) reads it: the
 * stage-to-group mapping (`tree.stages`, `tree.linkStage`) and how many levels open when a tree loads
 * (`tree.defaultExpanded`). A module with no imports, so the store can read it without an import
 * cycle. The workspace store writes it whenever the document changes (`setStageMap`); until then it
 * holds the defaults, which are the document's own: the stage table the store used to carry and two
 * levels open.
 *
 * Paths start with `/` and may name sub-groups (`/physics/materials`). A node belongs to a stage when
 * its path is one of the stage's paths or lies under one; when several stages own it, the one with
 * the longest matching path wins, and the current stage wins a tie, so a node under /runs does not
 * move Results to Solve.
 */

let stages: Record<string, readonly string[]> = {
  Geometry: ["/geometry"],
  Mesh: ["/mesh"],
  Physics: ["/physics", "/regions"],
  Solve: ["/solver", "/monitors", "/runs"],
  Results: ["/scenes", "/runs"],
  Explore: ["/studies"],
};
let linked = true;
let levels = 2;

export function setStageMap(next: Record<string, readonly string[]>, link: boolean, defaultExpanded: number): void {
  stages = next;
  linked = link;
  levels = defaultExpanded;
}

/** The levels of the tree that open when a tree loads (`tree.defaultExpanded`). */
export function defaultLevels(): number {
  return levels;
}

/** The group paths a stage works in. */
export function stageGroups(stage: string): readonly string[] {
  return stages[stage] ?? [];
}

/** Stage chips and the tree navigate each other (`tree.linkStage`). */
export function stageLinked(): boolean {
  return linked;
}

/** True when `path` is `group` or lies below it. */
export function under(path: string, group: string): boolean {
  return path === group || path.startsWith(`${group}/`);
}

/** Length of the longest of the stage's paths that holds `path`, or 0. */
function depthIn(stage: string, path: string): number {
  let best = 0;
  for (const group of stages[stage] ?? []) if (under(path, group) && group.length > best) best = group.length;
  return best;
}

/** The stage a node path belongs to, or null; `current` wins a tie. */
export function stageOfPath(path: string, current: string, order: readonly string[]): string | null {
  let winner: string | null = null;
  let best = 0;
  const here = depthIn(current, path);
  for (const stage of order) {
    const depth = depthIn(stage, path);
    if (depth > best) {
      best = depth;
      winner = stage;
    }
  }
  if (here > 0 && here >= best) return current;
  return winner;
}

/** True when a top-level group (by its path, `/physics`) should be open for the stage: it is listed, or holds a listed sub-group. */
export function stageOpens(stage: string, topPath: string): boolean {
  return (stages[stage] ?? []).some((group) => under(group, topPath));
}

/** The part of a tree node the expansion needs (structural, so this module imports nothing). */
interface TreeNodeLike {
  id: string;
  path: string;
  children?: readonly TreeNodeLike[];
}

/** The expanded map that opens `depth` levels of the tree (0: all folded; 2: the groups and the ones inside them). */
export function expansionFor(root: TreeNodeLike | null, depth: number): Record<string, boolean> {
  const expanded: Record<string, boolean> = {};
  const walk = (node: TreeNodeLike, at: number) => {
    if (at >= depth) return;
    for (const child of node.children ?? []) {
      expanded[child.id] = true;
      walk(child, at + 1);
    }
  };
  if (root) walk(root, 0);
  return expanded;
}

/**
 * The expansion after choosing a stage (linkStage on): every top-level group the stage works in opens,
 * the other top-level groups fold, and a listed sub-group (`/physics/materials`) opens with the groups
 * on its way. What lies below is left as the user left it. For the project store's `setStage`.
 */
export function stageExpanded(stage: string, root: TreeNodeLike | null, expanded: Readonly<Record<string, boolean>>): Record<string, boolean> {
  const next = { ...expanded };
  const groups = stages[stage] ?? [];
  const open = (node: TreeNodeLike) => {
    for (const child of node.children ?? []) {
      if (!groups.some((g) => under(g, child.path))) continue;
      next[child.id] = true;
      open(child);
    }
  };
  for (const top of root?.children ?? []) {
    next[top.id] = stageOpens(stage, top.path);
    if (next[top.id]) open(top);
  }
  return next;
}
