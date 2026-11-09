/**
 * The `viewport` section's defaults for a new view (docs/engineering/workspace-schema.md): the
 * projection and the view mode, applied when the first document arrives and whenever a project opens.
 * The O and V keys change the current view without writing the workspace, so a later change of the
 * section waits for the next project. `background` is CSS (the layout engine sets it on <main>) and
 * `grid` is the viewport's own switch (Viewport.tsx follows it at once): both show as the document says.
 *
 * The mouse scheme (`preferences.mouse`, T-178) is different: it is applied when the first document
 * arrives and afterwards only when the section CHANGES, comparing one document with the one before it.
 * Never document against store: any unrelated change (a theme switch) would then overwrite the scheme
 * the Modes island chose for this session. `apply()` stays for the two values that wait for a project.
 */
import { useStore } from "../state/store";
import { useWorkspace } from "./store";
import type { WorkspaceDocument } from "./types";

function apply(): void {
  const { projection, viewMode } = useWorkspace.getState().document.viewport;
  const store = useStore.getState();
  if (store.orthographic !== (projection === "orthographic")) store.setOrthographic(projection === "orthographic");
  if (store.viewMode !== viewMode) store.setViewMode(viewMode);
}

/**
 * Puts the mouse scheme and the Select / Orbit switch of `next` on the viewport when they differ from
 * those of `previous` (`null`: the first document, which always applies). The scheme goes first because
 * `setNavMode` clears the switch.
 */
export function applyMouse(previous: WorkspaceDocument | null, next: WorkspaceDocument): void {
  const was = previous?.preferences.mouse;
  const mouse = next.preferences.mouse;
  const schemeChanged = was === undefined || was.scheme !== mouse.scheme;
  const orbitChanged = was === undefined || was.orbitLeft !== mouse.orbitLeft;
  if (!schemeChanged && !orbitChanged) return;
  const store = useStore.getState();
  if (schemeChanged && store.navMode !== mouse.scheme) store.setNavMode(mouse.scheme);
  // The switch belongs to Select / Orbit; under another scheme it stays stored but off.
  const orbit = mouse.scheme === "toggle" && mouse.orbitLeft;
  const now = useStore.getState();
  if (now.orbitMode !== orbit) now.setOrbitMode(orbit);
}

export function startViewDefaults(): () => void {
  let applied = false;
  const offWorkspace = useWorkspace.subscribe((state, previous) => {
    if (!applied && state.status === "ready" && previous.status !== "ready") {
      applied = true;
      apply();
      applyMouse(null, state.document);
    } else if (applied && state.document !== previous.document) {
      applyMouse(previous.document, state.document);
    }
  });
  let offEvents: (() => void) | null = null;
  const attach = () => {
    offEvents?.();
    offEvents = useStore.getState().client?.onEvent((event) => {
      if (event === "project.opened") apply();
    }) ?? null;
  };
  attach();
  const offClient = useStore.subscribe((state, previous) => {
    if (state.client !== previous.client) attach();
  });
  return () => {
    offWorkspace();
    offClient();
    offEvents?.();
  };
}
