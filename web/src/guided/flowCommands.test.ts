/**
 * What the Guided cards' flow options send. They are the Solve settings page's own `solve.configure`; the
 * one thing a card adds is that heat transfer picks the thermal engine in the same call, because the
 * energy equation is refused on the other one (found by the mixing-elbow walkthrough, which had to leave
 * Guided mode to set a temperature at all).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

import type { NodeSummary } from "../api/types";
import { useStore } from "../state/store";
import { coresOf, setCores, setFlowModel, setHeatTransfer, setMeshCores } from "./commands";

const calls: { method: string; params: Record<string, unknown> }[] = [];

function solverTree(): NodeSummary {
  const settings = { id: "s", type: "solver_settings", name: "settings", path: "/solver/settings", status: "ok", params: {}, children_count: 0 } as unknown as NodeSummary;
  return { id: "root", type: "group", name: "", path: "/", status: "ok", params: {}, children_count: 1, children: [{ id: "sv", type: "group", name: "solver", path: "/solver", status: "ok", params: {}, children_count: 1, children: [settings] }] } as unknown as NodeSummary;
}

beforeEach(() => {
  calls.length = 0;
  useStore.setState({
    tree: solverTree(),
    execute: (async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      return {};
    }) as never,
    refresh: (async () => undefined) as never,
  });
});

describe("the Guided flow options", () => {
  it("chooses the flow model with solve.configure", async () => {
    await setFlowModel("realizableKE");
    expect(calls).toEqual([{ method: "solve.configure", params: { values: { turbulence: "realizableKE" } } }]);
  });

  it("turns heat transfer on together with the thermal engine, and off without touching the engine", async () => {
    await setHeatTransfer(true);
    await setHeatTransfer(false);
    expect(calls.map((c) => c.params)).toEqual([{ values: { engine: "of14.fluid", energy: true } }, { values: { energy: false } }]);
  });

  it("makes the solver settings first when the project has none", async () => {
    useStore.setState({ tree: { id: "root", type: "group", name: "", path: "/", status: "ok", params: {}, children_count: 0, children: [] } as unknown as NodeSummary });
    await setCores(4);
    expect(calls.map((c) => c.params)).toEqual([{}, { values: { cores: 4 } }]);
  });

  it("sets the mesh's cores with mesh.configure, not the solver's, making the mesh settings first", async () => {
    await setMeshCores(6);
    expect(calls).toEqual([
      { method: "mesh.configure", params: {} },
      { method: "mesh.configure", params: { values: { cores: 6 } } },
    ]);
  });

  it("reads the cores back, and says nothing for a value that is not a whole number of cores", () => {
    const at = (cores: unknown) => ({ params: { cores } }) as unknown as NodeSummary;
    expect(coresOf(at(4))).toBe(4);
    expect(coresOf(at(0))).toBeNull();
    expect(coresOf(at(1.5))).toBeNull();
    expect(coresOf(null)).toBeNull();
  });
});
