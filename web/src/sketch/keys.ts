/**
 * What a key does while the sketch is open (review A2). The sketch owns the keyboard: every key on
 * the canvas is consumed here, so nothing reaches the app behind it (the global keymap stands down
 * as well, `claimKeyboard` in `workspace/keymap.ts`). Tool letters, Esc, Enter, Delete, Ctrl+Z,
 * Ctrl+Shift+Z and Ctrl+Y, and while a draft takes sizes, the characters of the typed size. Pure.
 */
import { TOOLS, type Tool } from "./model";

export type KeyAction =
  | { kind: "swallow" }
  | { kind: "tool"; tool: Tool }
  | { kind: "undo" }
  | { kind: "redo" }
  | { kind: "escape" }
  | { kind: "enter" }
  | { kind: "tab"; back: boolean }
  | { kind: "char"; ch: string }
  | { kind: "backspace" }
  | { kind: "delete" }
  | { kind: "fit" }
  | { kind: "snap" };

export interface KeyLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

/**
 * The action of a key. `entryOpen`: the draft takes typed sizes, so printable characters go to
 * the field (a tool letter then is part of an expression such as `W`; Esc first to change tool).
 * `swallow` stops the key reaching the app without preventing the browser's own default (F5).
 */
export function sketchKey(e: KeyLike, state: { entryOpen: boolean; entryEmpty: boolean }): KeyAction {
  const key = e.key;
  if (e.ctrlKey || e.metaKey) {
    const k = key.toLowerCase();
    if (k === "z") return e.shiftKey ? { kind: "redo" } : { kind: "undo" };
    if (k === "y") return { kind: "redo" };
    return { kind: "swallow" };
  }
  if (key === "Escape") return { kind: "escape" };
  if (key === "Enter") return { kind: "enter" };
  if (key === "Tab") return { kind: "tab", back: e.shiftKey };
  if (key === "Backspace") return state.entryOpen && !state.entryEmpty ? { kind: "backspace" } : { kind: "delete" };
  if (key === "Delete") return { kind: "delete" };
  if (e.altKey || key.length !== 1 || key === " ") return { kind: "swallow" };
  if (state.entryOpen) return { kind: "char", ch: key };
  const upper = key.toUpperCase();
  const tool = TOOLS.find((t) => t.key === upper);
  if (tool) return { kind: "tool", tool: tool.id };
  if (upper === "F") return { kind: "fit" };
  if (upper === "G") return { kind: "snap" };
  return { kind: "swallow" };
}
