/**
 * Per-device conveniences: reduced motion and the first-run hint (and the palette's recent commands,
 * through panels/storage.ts). Everything the user arranges (panel widths, the drawer height, the
 * theme) is in the workspace instead (ADR-0015), which the server stores.
 *
 * Values live in localStorage under the "stokes." prefix and are shared between components
 * through useSyncExternalStore. Storage can be unavailable (private windows, blocked site
 * data), so every access is wrapped and falls back to a per-session memory copy: the UI
 * must render and behave without it.
 */
import { useCallback, useSyncExternalStore } from "react";

const PREFIX = "stokes.";
const memory = new Map<string, string>();
const listeners = new Set<() => void>();

export function readPref(key: string): string | null {
  try {
    const value = window.localStorage.getItem(PREFIX + key);
    if (value !== null) return value;
  } catch {
    /* storage blocked: use the memory copy */
  }
  return memory.get(key) ?? null;
}

export function writePref(key: string, value: string | null): void {
  if (value === null) memory.delete(key);
  else memory.set(key, value);
  try {
    if (value === null) window.localStorage.removeItem(PREFIX + key);
    else window.localStorage.setItem(PREFIX + key, value);
  } catch {
    /* the memory copy carries this session */
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key.startsWith(PREFIX)) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useStringPref(key: string, fallback: string): [string, (value: string) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => readPref(key) ?? fallback,
    () => fallback,
  );
  const set = useCallback((next: string) => writePref(key, next), [key]);
  return [value, set];
}

export function useBoolPref(key: string, fallback: boolean): [boolean, (value: boolean) => void] {
  const [raw, setRaw] = useStringPref(key, fallback ? "1" : "0");
  const set = useCallback((next: boolean) => setRaw(next ? "1" : "0"), [setRaw]);
  return [raw === "1", set];
}

export const REDUCED_MOTION_KEY = "reducedMotion";

/**
 * Show only the current stage's panels in a slot (T-220); **on by default**.
 *
 * The plan's reason is the owner's complaint about the six-tab strip: filtering is cheap and
 * every later item adds a panel. Off, every slot shows every panel it holds - which is the
 * behaviour before this existed, so the switch is a way back rather than a new mode.
 */
export const STAGE_FOCUS_KEY = "stageFocus";

/** True when the user asked for reduced motion, in the OS or in Appearance. Safe to call anywhere. */
export function prefersReducedMotion(): boolean {
  return (
    readPref(REDUCED_MOTION_KEY) === "1" ||
    (typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches)
  );
}
