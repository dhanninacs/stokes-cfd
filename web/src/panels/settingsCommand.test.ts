import { describe, expect, it } from "vitest";
import { parameterCommand, settingsMode } from "./settingsCommand";

describe("settings writes", () => {
  const mesh = { id: "mesh-id", type: "mesh_settings", path: "/mesh/settings" };
  const solver = { id: "solve-id", type: "solver_settings", path: "/solver/settings" };
  it("uses validated configure routes for canonical settings", () => {
    expect(parameterCommand(mesh, "base_size", "2 mm")).toEqual({ command: "mesh.configure", params: { values: { base_size: "2 mm" } } });
    expect(parameterCommand(solver, "iterations", 40)).toEqual({ command: "solve.configure", params: { values: { iterations: 40 } } });
  });
  it("uses top-level catalogue selectors", () => {
    expect(parameterCommand(mesh, "mesher", "snappy")).toEqual({ command: "mesh.configure", params: { mesher: "snappy" } });
    expect(parameterCommand(solver, "engine", "engine-id")).toEqual({ command: "solve.configure", params: { engine: "engine-id" } });
  });
  it("never redirects another selected node to singleton settings", () => {
    const other = { ...mesh, path: "/saved/settings" };
    expect(settingsMode(other)).toBeNull();
    expect(parameterCommand(other, "base_size", 3)).toEqual({ command: "parameter.set", params: { node: "mesh-id", name: "base_size", value: 3 } });
  });
});
