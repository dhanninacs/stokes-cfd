/**
 * The theme from the workspace (docs/engineering/theme-tokens.md, "Applying a theme").
 *
 * A built-in theme drives the project store's theme (`setTheme`), which App writes to `data-theme`.
 * A custom theme sets `data-theme` to its base and its tokens as inline custom properties on
 * `<html>`, which win over every stylesheet block; the previous overrides are removed first. `auto`,
 * `cupertino-auto` and `ubuntu-auto` follow the OS appearance inside their family. The theme
 * editor's draft, while there is one, is applied instead of the document's theme (live preview).
 *
 * The launch theme (`?theme=`, `--theme`) is a session override when it names a theme: a review
 * capture with `--theme dark` stays dark whatever the workspace says. It gives way as soon as the
 * workspace's theme changes during the session (a choice in Appearance, the top-bar toggle, a
 * `workspace.set` from the console or an agent). `auto`, the default, defers to the workspace.
 *
 * Everything runs synchronously in store subscriptions, so the project theme and the tokens change
 * in the same React render and App applies both before the next paint.
 */
import type { ClientConfig } from "../api/client";
import { composeTheme, useStore, type Appearance, type Theme } from "../state/store";
import { useWorkspace, type WorkspaceState } from "./store";
import { isCustomTheme, type ThemeName, type ThemeSpec } from "./types";

/** Window event sent after the inline tokens changed while `data-theme` did not (see theme-tokens.md). */
export const THEME_TOKENS_EVENT = "stokes:theme-tokens";

/** Names a theme may set: lower-case custom properties; the two layout outputs are the app's. */
const TOKEN_NAME = /^--[a-z0-9-]+$/;
const READ_ONLY = new Set(["--tree-width", "--props-width"]);

export function osAppearance(): Appearance {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/** A built-in theme name as the theme the project store holds. */
export function resolveName(name: ThemeName | ClientConfig["theme"], os: Appearance): Theme {
  if (name === "auto") return os;
  if (name === "cupertino-auto") return composeTheme("cupertino", os);
  if (name === "ubuntu-auto") return composeTheme("ubuntu", os);
  return name;
}

export interface DesiredTheme {
  theme: Theme;
  tokens: Record<string, string>;
}

/** The theme the document (or the editor's draft) asks for, before the launch override. */
export function themeOf(spec: ThemeSpec, os: Appearance): DesiredTheme {
  if (isCustomTheme(spec)) {
    const tokens: Record<string, string> = {};
    for (const [name, value] of Object.entries(spec.tokens)) {
      if (TOKEN_NAME.test(name) && !READ_ONLY.has(name) && typeof value === "string" && value.length <= 256) tokens[name] = value;
    }
    return { theme: spec.base, tokens };
  }
  return { theme: resolveName(spec.name, os), tokens: {} };
}

let firstThemeKey: string | null = null;
/** Set once the user chose a theme in this session: the launch theme no longer overrides. */
let launchReleased = false;

/** A theme choice made in the interface ends the launch override (see the module comment). */
export function releaseLaunchTheme(): void {
  launchReleased = true;
}

function desired(w: WorkspaceState, launch: ClientConfig["theme"], os: Appearance): DesiredTheme {
  const docTheme = w.document.theme;
  const key = JSON.stringify(docTheme);
  if (firstThemeKey === null && w.status === "ready") firstThemeKey = key;
  if (w.themeDraft) return themeOf(w.themeDraft, os);
  const docName = isCustomTheme(docTheme) ? docTheme.base : docTheme.name;
  const unchanged = !launchReleased && (firstThemeKey === null || key === firstThemeKey);
  if (launch !== "auto" && launch !== docName && unchanged) return { theme: resolveName(launch, os), tokens: {} };
  return themeOf(docTheme, os);
}

/**
 * Write a theme to the workspace (Appearance, the top-bar toggle, the theme editor). Replacing a
 * custom theme with a built-in one offers Undo, because the custom tokens are gone otherwise.
 */
export function chooseTheme(next: ThemeSpec): void {
  releaseLaunchTheme();
  const w = useWorkspace.getState();
  const previous = w.document.theme;
  void w.setValue(["theme"], next).then((ok) => {
    if (!ok || !isCustomTheme(previous) || isCustomTheme(next)) return;
    const name = previous.title ? `“${previous.title}”` : "your custom theme";
    useStore.getState().pushToast("info", `Switched away from ${name}.`, {
      label: "Undo",
      run: () => void useWorkspace.getState().setValue(["theme"], previous),
    });
  });
}

// ---------------------------------------------------------------------------------------------- tokens for App
let tokens: Record<string, string> = {};
const listeners = new Set<() => void>();

export function subscribeTokens(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The inline tokens App applies with `data-theme` (stable identity until they change). */
export function currentTokens(): Record<string, string> {
  return tokens;
}

function sameTokens(a: Record<string, string>, b: Record<string, string>): boolean {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k]);
}

const applied = new Set<string>();

/**
 * Put the overrides on `<html>`, removing the ones of the previous theme. Called by App's layout
 * effect together with `data-theme`. When only the tokens changed, the readers that follow
 * `data-theme` (the top bar's measuring, the residual chart) are told by setting the attribute again,
 * and THEME_TOKENS_EVENT goes out for the ones that listen to it.
 */
export function applyTokens(next: Record<string, string>, themeChanged: boolean): void {
  const root = document.documentElement;
  let changed = false;
  for (const name of [...applied]) {
    if (!(name in next)) {
      root.style.removeProperty(name);
      applied.delete(name);
      changed = true;
    }
  }
  for (const [name, value] of Object.entries(next)) {
    if (root.style.getPropertyValue(name) === value) {
      applied.add(name);
      continue;
    }
    root.style.setProperty(name, value);
    applied.add(name);
    changed = true;
  }
  if (!changed) return;
  if (!themeChanged && root.dataset.theme) root.setAttribute("data-theme", root.dataset.theme);
  window.dispatchEvent(new CustomEvent(THEME_TOKENS_EVENT));
}

// ---------------------------------------------------------------------------------------------- sync
/**
 * Keeps the project store's theme and the inline tokens equal to what the workspace asks for. Also
 * re-asserts it when something else moved the project theme (the store follows the OS on its own
 * when the launch theme was `auto`). Returns the cleanup.
 */
export function startThemeSync(launch: ClientConfig["theme"]): () => void {
  const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: light)") : null;
  const sync = () => {
    const w = useWorkspace.getState();
    if (w.status === "loading") return;
    const next = desired(w, launch, osAppearance());
    if (!sameTokens(next.tokens, tokens)) {
      tokens = next.tokens;
      listeners.forEach((listener) => listener());
    }
    const store = useStore.getState();
    if (store.theme !== next.theme) store.setTheme(next.theme);
  };
  const offWorkspace = useWorkspace.subscribe((state, previous) => {
    if (state.document.theme !== previous.document.theme || state.themeDraft !== previous.themeDraft || state.status !== previous.status) sync();
  });
  const offStore = useStore.subscribe((state, previous) => {
    if (state.theme !== previous.theme) sync();
  });
  media?.addEventListener("change", sync);
  sync();
  return () => {
    offWorkspace();
    offStore();
    media?.removeEventListener("change", sync);
  };
}
