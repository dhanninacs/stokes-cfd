/**
 * Which parts the Results panel's fade applies to, and whether it is even needed.
 *
 * **Why a rule rather than a loop in the component** (G-301's D4): the fade is one `geometry.style` call
 * with a LIST of nodes, and `geometry.style` already exists and already takes lists - so this file is the
 * only new logic and it is pure, which is what makes it testable without a browser.
 *
 * **Bodies that are OUT of the simulation are not faded**, because they are not drawn in the result: the
 * viewport leaves them out, so an opacity written to them is a model edit with nothing to show for it.
 * That is the one distinction the panel can see for itself - the scene's own `regionBodies` set is
 * private to the viewport, and reaching for it here would be a second copy of the same fact.
 */
export interface FadeTarget {
  id: string;
  /** False when the user left the body out of the simulation (`PartNode.simulate`). */
  simulate: boolean;
}

/** The parts a fade applies to: every part that is in the simulation. */
export function fadeTargets(parts: readonly FadeTarget[]): string[] {
  return parts.filter((p) => p.simulate).map((p) => p.id);
}

/**
 * Whether the results are on screen at all.
 *
 * **A fade with no results fades the model for no reason** - and worse, it is a model edit the user did
 * not ask for. The control is disabled rather than hidden, so the panel does not reflow.
 */
export function fadeAvailable(hasResults: boolean, targets: readonly string[]): boolean {
  return hasResults && targets.length > 0;
}
