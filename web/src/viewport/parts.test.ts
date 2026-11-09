import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StokesClient } from "../api/client";
import type { SceneManager } from "./scene";

const fake = vi.hoisted(() => {
  const state: Record<string, unknown> = {};
  const listeners = new Set<(state: Record<string, unknown>, previous: Record<string, unknown>) => void>();
  return { state, listeners, update(patch: Record<string, unknown>) {
    const previous = { ...state };
    Object.assign(state, patch);
    for (const listener of listeners) listener(state, previous);
  } };
});
vi.mock("../state/store", () => ({ useStore: {
  getState: () => fake.state,
  subscribe: (listener: (state: Record<string, unknown>, previous: Record<string, unknown>) => void) => {
    fake.listeners.add(listener);
    return () => fake.listeners.delete(listener);
  },
} }));
import { PartsController } from "./parts";

describe("project geometry without implicit examples", () => {
  beforeEach(() => {
    fake.listeners.clear();
    Object.assign(fake.state, { tree: null, partNodes: [], parts: {}, setGeometry: vi.fn(), log: vi.fn() });
  });

  it("does not fetch or draw a demo at startup, in an empty project, or while an import waits", () => {
    const fetchGeometry = vi.fn();
    const setSource = vi.fn();
    const controller = new PartsController({ fetchGeometry } as unknown as StokesClient, { setSource, hasSource: () => false } as unknown as SceneManager);
    fake.update({ tree: { id: "empty", children: [] } });
    fake.update({ tree: { id: "importing", children: [] }, parts: { imported: { id: "imported", revision: 0, present: false } } });
    fake.update({ tree: { id: "failed", children: [] }, parts: {} });
    expect(fetchGeometry).not.toHaveBeenCalled();
    expect(setSource).not.toHaveBeenCalled();
    controller.dispose();
  });

  it("fetches actual project parts by id once their geometry is ready", () => {
    const fetchGeometry = vi.fn(() => new Promise<ArrayBuffer>(() => undefined));
    const controller = new PartsController({ fetchGeometry } as unknown as StokesClient, { hasSource: () => false } as unknown as SceneManager);
    fake.update({ tree: { id: "project" }, parts: { imported: { id: "imported", revision: 1, present: true } } });
    expect(fetchGeometry).toHaveBeenCalledExactlyOnceWith("imported", expect.any(AbortSignal));
    controller.dispose();
  });
});
