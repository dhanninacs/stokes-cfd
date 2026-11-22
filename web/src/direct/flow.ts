/**
 * The steps of the direct flow that tie a command to what the screen does next (ADR-0039).
 */
import { findNode, useStore } from "../state/store";

/**
 * Selects the first output of operation `opId` when its run is done, so the new body carries its
 * Pull handle at once. The store's own selection after a run belongs to the operation card, which
 * the direct flow does not open.
 */
export function selectWhenDone(opId: string): void {
  const before = useStore.getState().lastOperationDone;
  const off = useStore.subscribe((s) => {
    const done = s.lastOperationDone;
    if (done === before || done === null || done.nodeId !== opId) return;
    off();
    window.clearTimeout(timer);
    if (!done.output) return;
    // `pendingSelect` is applied on the NEXT tree change. When the tree already holds the output (its node
    // arrived before the run's done event) no change follows, and the new body stayed unpicked with the
    // tree closed, about one Finish in twenty: select it here in that case.
    const state = useStore.getState();
    if (findNode(state.tree, done.output) !== null) state.select(done.output);
    else useStore.setState({ pendingSelect: done.output });
  });
  const timer = window.setTimeout(off, 120_000);
}
