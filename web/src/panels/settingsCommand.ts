import type { NodeSummary } from "../api/types";

/** Configure commands target singleton paths; other nodes must retain their own identity. */
export function settingsMode(node: Pick<NodeSummary, "type" | "path">): "solver" | "mesh" | null {
  if (node.type === "solver_settings" && node.path === "/solver/settings") return "solver";
  if (node.type === "mesh_settings" && node.path === "/mesh/settings") return "mesh";
  return null;
}

export function parameterCommand(node: Pick<NodeSummary, "id" | "type" | "path">, name: string, value: unknown): { command: string; params: Record<string, unknown> } {
  const mode = settingsMode(node);
  if (mode === "solver") return { command: "solve.configure", params: name === "engine" ? { engine: value } : { values: { [name]: value } } };
  if (mode === "mesh") return { command: "mesh.configure", params: name === "mesher" ? { mesher: value } : { values: { [name]: value } } };
  return { command: "parameter.set", params: { node: node.id, name, value } };
}
