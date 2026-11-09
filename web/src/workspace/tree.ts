/**
 * The `tree` section applied to the project tree: the order of the top-level groups and the groups
 * the tree does not show. The stage mapping (`stages`, `linkStage`) and the levels that open when a
 * tree loads (`defaultExpanded`) are in stageMap.ts, where the project store reads them.
 */
import { useMemo } from "react";
import type { NodeSummary } from "../api/types";
import { STAGES, type Stage } from "../state/store";
import { useWorkspace } from "./store";

/**
 * The stages the stage control shows: the ones `tree.stages` lists, in its order, that the project
 * store knows (a stage of a newer version or a plug-in is skipped until the store has it).
 */
export function shownStages(stages: Record<string, unknown>): Stage[] {
  const known = STAGES as readonly string[];
  const listed = Object.keys(stages).filter((s): s is Stage => known.includes(s));
  return listed.length ? listed : [...STAGES];
}

export function useStages(): Stage[] {
  const stages = useWorkspace((s) => s.document.tree.stages);
  return useMemo(() => shownStages(stages), [stages]);
}

/** A top-level group's name: the last segment of its path ("/physics" gives "physics"). */
function groupName(node: NodeSummary): string {
  return node.path.split("/")[1] ?? node.name;
}

/**
 * The root with its top-level groups reordered by `order` (unlisted groups follow in their own order)
 * and the `hidden` ones left out. The nodes themselves are the tree's, so ids, selection and lookups
 * are unaffected; the same root comes back when nothing changes.
 */
export function arrangeTree(root: NodeSummary | null, order: readonly string[], hidden: readonly string[]): NodeSummary | null {
  if (!root?.children) return root;
  const rank = new Map(order.map((name, i) => [name, i]));
  const kids = root.children.filter((c) => !hidden.includes(groupName(c)));
  const sorted = kids
    .map((node, i) => ({ node, i, r: rank.get(groupName(node)) ?? order.length + i }))
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .map((x) => x.node);
  const same = sorted.length === root.children.length && sorted.every((n, i) => n === root.children?.[i]);
  return same ? root : { ...root, children: sorted };
}
