/**
 * The keymap: the workspace's `shortcuts` section turned into one global key handler, and the hint
 * every tooltip shows (ADR-0015 §7). Chords are canonical as the server stores them (modifiers in the
 * order Ctrl, Alt, Shift, Meta, then one key, joined with `+`; docs/engineering/workspace-schema.md).
 *
 * Matching: letters, digits and punctuation by `KeyboardEvent.code` (`KeyF`, `Digit1`, `Comma`), so
 * Shift and the keyboard layout do not change them; named keys by `KeyboardEvent.key`. `Ctrl` matches
 * Control or, on macOS, Command (`ctrlKey || metaKey`, as the handlers always did); `Meta` is the
 * Windows or Command key itself. The handler stays out of the way as the old ones did: nothing acts
 * behind a modal dialog or on a key a menu or dialog consumed (`defaultPrevented`), plain keys do not
 * fire while a text field, menu or dialog has the focus, and a held key repeats only undo, redo and
 * the palette. Tree actions (F2, Delete, the menu key) belong to the tree's own handler.
 */
import { useMemo } from "react";
import { ACTION_SPECS, runAction } from "../app/actions";
import { useWorkspace } from "./store";

/** The chords of a binding, primary first: a chord, a list of chords, or "" / [] for none. */
export function chordsOf(binding: unknown): string[] {
  if (typeof binding === "string") return binding ? [binding] : [];
  if (Array.isArray(binding)) return binding.filter((c): c is string => typeof c === "string" && c.length > 0);
  return [];
}

const CODE_KEYS: Record<string, string> = {
  Backquote: "`",
  Minus: "-",
  Equal: "=",
  BracketLeft: "[",
  BracketRight: "]",
  Backslash: "\\",
  Semicolon: ";",
  Quote: "'",
  Comma: ",",
  Period: ".",
  Slash: "/",
  NumpadAdd: "Plus",
};
const NAMED_KEYS = new Set([
  "Enter",
  "Escape",
  "Tab",
  "Backspace",
  "Delete",
  "Insert",
  "Home",
  "End",
  "PageUp",
  "PageDown",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "ContextMenu",
]);
const PUNCTUATION = "`-=[]\\;',./";

/** The canonical key of a key event, or null for a modifier or a key no chord can name. */
export function eventKey(e: KeyboardEvent): string | null {
  const letter = /^Key([A-Z])$/.exec(e.code);
  if (letter) return letter[1];
  const digit = /^Digit([0-9])$/.exec(e.code);
  if (digit) return digit[1];
  if (e.code in CODE_KEYS) return CODE_KEYS[e.code];
  if (e.code === "Space" || e.key === " ") return "Space";
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(e.key)) return e.key;
  if (NAMED_KEYS.has(e.key)) return e.key;
  if (e.key === "+") return "Plus";
  // An event without a code (a synthetic one): the character itself.
  if (!e.code && e.key.length === 1) {
    if (/[a-z]/i.test(e.key)) return e.key.toUpperCase();
    if (/[0-9]/.test(e.key) || PUNCTUATION.includes(e.key)) return e.key;
  }
  return null;
}

/** The canonical chords a key event can stand for; Command alone may mean Ctrl (macOS) or Meta. */
export function eventChords(e: KeyboardEvent): string[] {
  const key = eventKey(e);
  if (!key) return [];
  const build = (ctrl: boolean, meta: boolean) =>
    [ctrl ? "Ctrl" : "", e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : "", meta ? "Meta" : "", key].filter(Boolean).join("+");
  if (e.metaKey && !e.ctrlKey) return [build(true, false), build(false, true)];
  return [build(e.ctrlKey, e.metaKey)];
}

const HINT_KEYS: Record<string, string> = {
  Escape: "Esc",
  Delete: "Del",
  ArrowUp: "↑",
  ArrowDown: "↓",
  ArrowLeft: "←",
  ArrowRight: "→",
  PageUp: "PgUp",
  PageDown: "PgDn",
  Plus: "+",
  ContextMenu: "Menu",
};

/** A chord as the key caps show it: `Ctrl+Shift+S` is "Ctrl Shift S", `Escape` is "Esc". */
export function chordHint(chord: string): string {
  return chord
    .split("+")
    .map((part) => HINT_KEYS[part] ?? part)
    .join(" ");
}

/** A chord in `aria-keyshortcuts` syntax: `Ctrl+K` is "Control+K". */
export function chordAria(chord: string): string {
  return chord
    .split("+")
    .map((part) => (part === "Ctrl" ? "Control" : part === "Plus" ? "+" : part))
    .join("+");
}

export interface ShortcutHint {
  /** For the tooltip's key cap, e.g. "Ctrl K"; undefined when the action has no chord. */
  hint?: string;
  /** For `aria-keyshortcuts`, e.g. "Control+K". */
  keys?: string;
}

/** The first chord of an action's binding, as a hint and as aria-keyshortcuts. */
export function useShortcut(actionId: string): ShortcutHint {
  const binding = useWorkspace((s) => s.document.shortcuts[actionId]);
  const first = chordsOf(binding)[0];
  return useMemo(() => (first ? { hint: chordHint(first), keys: chordAria(first) } : {}), [first]);
}

/** The first chord's hint, outside React (menus built on the fly). */
export function shortcutHint(actionId: string): string | undefined {
  const first = chordsOf(useWorkspace.getState().document.shortcuts[actionId])[0];
  return first ? chordHint(first) : undefined;
}

/** True when a key event is one of an action's chords; for handlers with their own focus rules (the tree). */
export function matchesAction(e: KeyboardEvent, actionId: string): boolean {
  const chords = chordsOf(useWorkspace.getState().document.shortcuts[actionId]);
  return eventChords(e).some((c) => chords.includes(c));
}

let cache: { source: unknown; map: Map<string, string[]> } | null = null;

/** Chord to the action ids bound to it, in document order; rebuilt when the section changes. */
function keymap(): Map<string, string[]> {
  const shortcuts = useWorkspace.getState().document.shortcuts;
  if (cache?.source === shortcuts) return cache.map;
  const map = new Map<string, string[]>();
  for (const [id, binding] of Object.entries(shortcuts)) {
    for (const chord of chordsOf(binding)) map.set(chord, [...(map.get(chord) ?? []), id]);
  }
  cache = { source: shortcuts, map };
  return map;
}

/** Text fields, menus and dialogs own their keys; plain-key shortcuts stay out of the way. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT") return true;
  return target.closest("[role='textbox'], [role='combobox'], [role='menu'], [role='dialog']") !== null;
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.defaultPrevented || e.isComposing) return;
  if (document.querySelector("[aria-modal='true']")) return; // nothing acts behind a modal dialog
  const chords = eventChords(e);
  if (chords.length === 0) return;
  const map = keymap();
  const typing = isTypingTarget(e.target);
  for (const chord of chords) {
    for (const id of map.get(chord) ?? []) {
      const spec = ACTION_SPECS.get(id);
      if (spec?.context === "tree") continue;
      if (typing && !spec?.whileTyping) continue;
      if (e.repeat && !spec?.repeat) return;
      if (runAction(id)) {
        e.preventDefault();
        return;
      }
    }
  }
}

/** The one global key handler; returns the call that removes it. */
export function installKeymap(): () => void {
  window.addEventListener("keydown", onKeyDown);
  return () => window.removeEventListener("keydown", onKeyDown);
}
