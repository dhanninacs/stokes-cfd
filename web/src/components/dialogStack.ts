/**
 * Which dialog owns the keyboard, and which may close on Escape.
 *
 * **Extracted from `Dialog.tsx` because it leaked, silently** (G-301's H-3). The stack lived in a
 * module-level array mutated inside a `useEffect` keyed `[focus, id]`; when either changed React ran the
 * cleanup and the effect again, pushing a SECOND entry for a dialog that never closed. `isTop()` then
 * disagreed with the array's end, so **Escape stopped closing any dialog** - and because the scrim keeps
 * `pointer-events: auto`, the whole window became unclickable.
 *
 * **The fix is the operation, not the array**: `push` returns whether an entry was actually added, and
 * the same token is what `remove` takes, so a double effect cannot leave two. Pure, so the balance is
 * testable without a renderer - which the array alone never was.
 */
export interface DialogToken {
  readonly id: symbol;
}

export class DialogStack {
  private readonly entries: symbol[] = [];

  /** Adds a token. **Idempotent per token**, so a re-run effect cannot add a second. */
  push(id: symbol): DialogToken {
    if (!this.entries.includes(id)) this.entries.push(id);
    return { id };
  }

  /** Removes a token wherever it sits. Safe to call twice, which is what an effect cleanup does. */
  remove(token: DialogToken): void {
    const at = this.entries.indexOf(token.id);
    if (at >= 0) this.entries.splice(at, 1);
  }

  /** True when `token` is the one at the end: the dialog that Escape and an outside press belong to. */
  isTop(token: DialogToken): boolean {
    return this.entries.length > 0 && this.entries[this.entries.length - 1] === token.id;
  }

  /** The number of open dialogs - for the tests, and for a future "how deep" question. */
  get depth(): number {
    return this.entries.length;
  }
}

/** The one stack the dialogs share. */
export const dialogStack = new DialogStack();
