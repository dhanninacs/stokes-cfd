/** Pure selection and action rules shared by the tree's pointer and keyboard menus. */

export function shouldSelectContextTarget(targetId: string, selectedIds: readonly string[], selectedId: string | null): boolean {
  return !selectedIds.includes(targetId) && selectedId !== targetId;
}

/** A selected row's context actions apply to the whole set; an unselected row is a row-only target. */
export function actionTargetsForContext(targetId: string, selectedIds: readonly string[]): string[] {
  return selectedIds.length > 1 && selectedIds.includes(targetId) ? [...selectedIds] : [targetId];
}

export function isCompleteActionSelection(targetIds: readonly string[], resolvedIds: readonly string[]): boolean {
  return targetIds.length === resolvedIds.length && targetIds.every((id) => resolvedIds.includes(id));
}

export interface VisibilityChangePlan {
  hideInSession: string[];
  revealInSession: string[];
  restoreModelVisibility: string[];
}

/** Applies bulk Show/Hide to both the transient viewport filter and saved visibility flags. */
export function visibilityChangePlan(
  nodeIds: readonly string[],
  show: boolean,
  hiddenIds: readonly string[],
  modelVisible: Readonly<Record<string, boolean>>,
): VisibilityChangePlan {
  const hidden = new Set(hiddenIds);
  const hideInSession: string[] = [];
  const revealInSession: string[] = [];
  const restoreModelVisibility: string[] = [];
  for (const id of nodeIds) {
    if (show) {
      if (hidden.has(id)) revealInSession.push(id);
      if (modelVisible[id] === false) restoreModelVisibility.push(id);
    } else if (!hidden.has(id) && modelVisible[id] !== false) {
      hideInSession.push(id);
    }
  }
  return { hideInSession, revealInSession, restoreModelVisibility };
}

export interface TreeBulkMenuAction {
  id: "show-selected" | "hide-selected" | "simulate-selected" | "exclude-selected";
  label: string;
  kind: "visibility" | "simulation";
  value: boolean;
  nodeIds: readonly string[];
}

/** Commands the menu may safely apply to all of its target nodes. */
export function bulkTreeMenuActions(nodeIds: readonly string[], canChangeVisibility: boolean, allParts: boolean): TreeBulkMenuAction[] {
  if (nodeIds.length < 2) return [];
  const nodes = [...nodeIds];
  const actions: TreeBulkMenuAction[] = [];
  if (canChangeVisibility) {
    actions.push(
      { id: "show-selected", label: `Show ${nodes.length} selected`, kind: "visibility", value: true, nodeIds: nodes },
      { id: "hide-selected", label: `Hide ${nodes.length} selected`, kind: "visibility", value: false, nodeIds: nodes },
    );
  }
  if (allParts) {
    actions.push(
      { id: "simulate-selected", label: `Use ${nodes.length} selected in simulation`, kind: "simulation", value: true, nodeIds: nodes },
      { id: "exclude-selected", label: `Leave ${nodes.length} selected out of simulation`, kind: "simulation", value: false, nodeIds: nodes },
    );
  }
  return actions;
}
