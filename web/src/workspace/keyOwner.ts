/**
 * Who owns the keyboard while a modal editor is open (the sketcher, review A2). While an owner
 * holds it, the global keymap (`keymap.ts`) fires no shortcut; the owner handles its keys itself.
 * Its own module so it stays free of the store and the actions (the tests run in Node).
 */
let owner: string | null = null;

/** Take the whole keyboard; the returned call gives it back (a stale release leaves a newer claim). */
export function claimKeyboard(name: string): () => void {
  owner = name;
  return () => {
    if (owner === name) owner = null;
  };
}

/** The current owner of the keyboard, or null when the keymap has it. */
export function keyboardOwner(): string | null {
  return owner;
}
