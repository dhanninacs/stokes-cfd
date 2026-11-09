import type { Stage } from "../state/store";

/**
 * Which panels a slot shows in a stage (T-220).
 *
 * **A pure function, so the rule is testable without a browser.** It is the whole of stage focus:
 * a panel with no `stages` is always-on, and a panel whose stages exclude the current one is hidden.
 *
 * **Pinning is the second half, and it is what keeps the tree usable.** A tree row that opens a
 * foreign stage's panel (clicking a mesh result while in Geometry, say) must not have that panel
 * disappear under the pointer - so the tab it opened stays until the stage changes again. Without it
 * the tree would refold as the user clicked through it, which is worse than the six-tab strip this
 * replaces.
 */
export interface FocusablePanel {
  def: { id: string; stages?: readonly Stage[] };
}

export interface FocusChoice<T extends FocusablePanel> {
  /** The panels to show, in their slot's order. */
  shown: T[];
  /** Panels hidden by the stage, so a caller can say how many rather than only that some are gone. */
  hidden: T[];
}

/**
 * The panels to show for `stage`.
 *
 * `pinned` names a panel opened from outside its stage; it is shown even when the stage excludes it,
 * which is the pinning rule. An unknown pinned id is ignored rather than kept - a pin that outlives
 * its panel would show nothing.
 */
export function panelsForStage<T extends FocusablePanel>(
  panels: readonly T[],
  stage: Stage,
  pinned: string | readonly string[] | null = null,
): FocusChoice<T> {
  const shown: T[] = [];
  const hidden: T[] = [];
  for (const panel of panels) {
    const belongs = panel.def.stages === undefined || panel.def.stages.includes(stage);
    // **`pinned` may name one panel or several** (the owner: *"hiding the tree and settings tabs is
    // broken"*). One pin was enough when the only way to show a foreign panel was to click a tree row,
    // which opens one; a user who OPENS several from the palette - Mesh, then Conditions, then
    // Properties - needs each of them to stay, or the second open silently hides the first.
    const isPinned = Array.isArray(pinned) ? pinned.includes(panel.def.id) : panel.def.id === pinned;
    if (belongs || isPinned) shown.push(panel);
    else hidden.push(panel);
  }
  // **Never return an empty strip.** A slot whose panels all belong to other stages would show a
  // bare card with no tabs and no way back; the panels are shown instead, because a wrong tab is
  // recoverable and an empty slot is not.
  return shown.length > 0 ? { shown, hidden } : { shown: [...panels], hidden: [] };
}
