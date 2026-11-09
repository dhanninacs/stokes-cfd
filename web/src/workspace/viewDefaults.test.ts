/**
 * The mouse scheme is a saved preference (T-178) and is applied by comparing one workspace document
 * with the one before it. Compared with the store instead, any unrelated change (a theme switch)
 * would overwrite the scheme the Modes island chose for this session.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `state/store` reads `window` as it loads; this test has no DOM.
vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

import { useStore } from "../state/store";
import { defaultDocument } from "./defaults";
import { useWorkspace } from "./store";
import type { MouseScheme, WorkspaceDocument } from "./types";
import { applyMouse, startViewDefaults } from "./viewDefaults";

function withMouse(scheme: MouseScheme, orbitLeft = false, base: WorkspaceDocument = defaultDocument()): WorkspaceDocument {
  return { ...base, preferences: { ...base.preferences, mouse: { ...base.preferences.mouse, scheme, orbitLeft } } };
}

let stop: () => void = () => undefined;

beforeEach(() => {
  useStore.setState({ navMode: "cad", orbitMode: false });
  useWorkspace.setState({ status: "loading", document: defaultDocument() });
  stop = startViewDefaults();
});

afterEach(() => stop());

describe("the mouse preference", () => {
  it("applies the scheme of the first document", () => {
    useWorkspace.setState({ status: "ready", document: withMouse("blender") });
    expect(useStore.getState().navMode).toBe("blender");
  });

  it("applies the Select / Orbit switch with a scheme of the first document", () => {
    useWorkspace.setState({ status: "ready", document: withMouse("toggle", true) });
    expect(useStore.getState().navMode).toBe("toggle");
    expect(useStore.getState().orbitMode).toBe(true);
  });

  it("leaves a scheme chosen for this session alone when something else in the workspace changes", () => {
    useWorkspace.setState({ status: "ready", document: withMouse("blender") });
    useStore.getState().setNavMode("paraview"); // the Modes island: this session only
    const base = useWorkspace.getState().document;
    useWorkspace.setState({ document: { ...base, theme: { name: "ubuntu" }, viewport: { ...base.viewport, grid: false } } });
    expect(useStore.getState().navMode).toBe("paraview");
  });

  it("leaves it alone when the server's refetch returns the same preferences as a new object", () => {
    useWorkspace.setState({ status: "ready", document: withMouse("blender") });
    useStore.getState().setNavMode("paraview");
    useWorkspace.setState({ document: withMouse("blender") });
    expect(useStore.getState().navMode).toBe("paraview");
  });

  it("applies a changed scheme, from the dialog or from workspace.set in the console", () => {
    useWorkspace.setState({ status: "ready", document: defaultDocument() });
    useStore.getState().setNavMode("paraview");
    useWorkspace.setState({ document: withMouse("blender") });
    expect(useStore.getState().navMode).toBe("blender");
  });

  it("applies a changed orbit switch without touching the scheme", () => {
    useWorkspace.setState({ status: "ready", document: withMouse("toggle") });
    useWorkspace.setState({ document: withMouse("toggle", true) });
    expect(useStore.getState().navMode).toBe("toggle");
    expect(useStore.getState().orbitMode).toBe(true);
  });

  it("keeps the switch off under a scheme that has none, and applies it when the scheme becomes Select / Orbit", () => {
    useWorkspace.setState({ status: "ready", document: withMouse("cad", true) });
    expect(useStore.getState().orbitMode).toBe(false);
    useWorkspace.setState({ document: withMouse("toggle", true) });
    expect(useStore.getState().orbitMode).toBe(true);
    useWorkspace.setState({ document: withMouse("paraview", true) });
    expect(useStore.getState().orbitMode).toBe(false);
  });

  it("applies the scheme again when the workspace is reset to the defaults", () => {
    useWorkspace.setState({ status: "ready", document: withMouse("blender") });
    useWorkspace.setState({ document: defaultDocument() });
    expect(useStore.getState().navMode).toBe("paraview");
  });

  it("does nothing for a document with the same mouse section", () => {
    const doc = withMouse("blender");
    useStore.setState({ navMode: "paraview" });
    applyMouse(doc, withMouse("blender"));
    expect(useStore.getState().navMode).toBe("paraview");
  });
});
